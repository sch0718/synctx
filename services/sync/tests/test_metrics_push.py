import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

import metrics_push
import sync_engine
from metrics_push import RepoMetrics, push_totals
from synctx_common.observability import (
    sync_failures_total,
    sync_items_total,
    sync_last_success_timestamp,
    sync_runs_total,
)
from test_embed_sync import FakeLock, engine, locked_with, profile, repo  # noqa: F401


@pytest.fixture(autouse=True)
def immediate_baseline(monkeypatch):
    # 기준값 수집 대기는 test_deferred_increase_waits_until_baseline_could_be_scraped에서 따로 본다
    monkeypatch.setattr(metrics_push, "BASELINE_SCRAPE_GRACE_SECONDS", 0)


def totals_by_name(totals, name, **labels):
    return sum(v for (n, ls), v in totals.items() if n == name and all(dict(ls).get(k) == x for k, x in labels.items()))


def saved(tmp_path, repo_id="r"):
    return RepoMetrics(str(tmp_path), repo_id).load()


def test_commit_adds_only_this_run_increase_to_saved_totals(tmp_path):
    sync_runs_total.labels(repo_id="m1", result="success").inc(5)
    first = RepoMetrics(str(tmp_path), "m1")
    sync_runs_total.labels(repo_id="m1", result="success").inc()
    sync_failures_total.labels(repo_id="m1", target="xwiki", error_type="auth_error").inc()
    first.commit()
    first.mark_pushed()

    second = RepoMetrics(str(tmp_path), "m1")
    sync_runs_total.labels(repo_id="m1", result="success").inc()
    totals = second.commit()

    assert totals_by_name(totals, "synctx_sync_runs_total", result="success") == 2
    assert totals_by_name(totals, "synctx_sync_failures_total", error_type="auth_error") == 1


def test_commit_keeps_last_success_when_run_did_not_set_it(tmp_path):
    first = RepoMetrics(str(tmp_path), "m2")
    sync_last_success_timestamp.labels(repo_id="m2").set(1000)
    first.commit()

    failed_run = RepoMetrics(str(tmp_path), "m2")
    sync_runs_total.labels(repo_id="m2", result="failed").inc()
    totals = failed_run.commit()

    assert totals_by_name(totals, "synctx_sync_last_success_timestamp") == 1000


def test_commit_ignores_other_repositories(tmp_path):
    metrics = RepoMetrics(str(tmp_path), "m3")
    sync_runs_total.labels(repo_id="other", result="success").inc()
    totals = metrics.commit()
    assert {dict(labels)["repo_id"] for _, labels in totals} == {"m3"}
    assert totals_by_name(totals, "synctx_sync_runs_total") == 0


def test_alerted_series_start_at_zero_so_first_failure_is_an_increase(tmp_path):
    baseline = RepoMetrics(str(tmp_path), "m6")
    totals = baseline.commit()
    baseline.mark_pushed()
    auth = ("synctx_sync_failures_total", (("error_type", "auth_error"), ("repo_id", "m6"), ("target", "xwiki")))
    rejected = ("synctx_html_bundle_total", (("repo_id", "m6"), ("result", "too_large"), ("service", "sync")))
    assert totals[auth] == 0 and totals[rejected] == 0

    run = RepoMetrics(str(tmp_path), "m6")
    sync_failures_total.labels(repo_id="m6", target="xwiki", error_type="auth_error").inc()
    assert run.commit()[auth] == 1


def test_corrupt_file_starts_from_this_run(tmp_path):
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "m4.json").write_text("{not json", "utf-8")
    metrics = RepoMetrics(str(tmp_path), "m4")
    sync_runs_total.labels(repo_id="m4", result="success").inc()
    assert totals_by_name(metrics.commit(), "synctx_sync_runs_total") == 1


class Gateway(BaseHTTPRequestHandler):
    requests = []
    deleted = []
    groups = []

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        Gateway.requests.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        body = json.dumps({"status": "success", "data": [{"labels": g} for g in Gateway.groups]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        Gateway.deleted.append(self.path)
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def gateway():
    Gateway.requests = []
    Gateway.deleted = []
    Gateway.groups = []
    server = HTTPServer(("127.0.0.1", 0), Gateway)
    Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_push_replaces_repo_group_with_totals(gateway):
    totals = {
        ("synctx_sync_runs_total", (("repo_id", "r"), ("result", "success"))): 3.0,
        ("synctx_sync_last_success_timestamp", (("repo_id", "r"),)): 1000.0,
        ("synctx_html_bundle_total", (("repo_id", "r"), ("result", "too_large"), ("service", "sync"))): 2.0,
    }
    push_totals(gateway, "r", totals)
    [(path, body)] = Gateway.requests
    assert path == "/metrics/job/synctx-sync/repo_id/r"
    assert 'synctx_sync_runs_total{repo_id="r",result="success"} 3.0' in body
    assert "# TYPE synctx_sync_runs_total counter" in body
    assert 'synctx_sync_last_success_timestamp{repo_id="r"} 1000.0' in body
    assert 'synctx_html_bundle_total{repo_id="r",result="too_large",service="sync"} 2.0' in body


@pytest.mark.asyncio
async def test_run_saves_and_pushes_totals_across_runs(tmp_path, monkeypatch, repo, gateway):
    monkeypatch.setenv(metrics_push.PUSHGATEWAY_URL_ENV, gateway)
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    repo.head, repo.diff = "h2", [("M", "docs/motion/motion.js")]
    repo.files["docs/motion/motion.js"] = "window.v = 2;"
    await eng.run_sync(profile())

    totals = saved(tmp_path)
    assert totals_by_name(totals, "synctx_sync_runs_total", result="success") == 2
    assert totals_by_name(totals, "synctx_html_bundle_total", result="ok") == 2
    assert len(Gateway.requests) == 2
    assert 'synctx_sync_runs_total{repo_id="r",result="success"} 2.0' in Gateway.requests[-1][1]


@pytest.mark.asyncio
async def test_up_to_date_run_counts_as_success(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    sync_last_success_timestamp.labels(repo_id="r").set(0)
    RepoMetrics(str(tmp_path), "r")._save({("synctx_sync_last_success_timestamp", (("repo_id", "r"),)): 0.0})

    assert await eng.run_sync(profile()) is None
    assert totals_by_name(saved(tmp_path), "synctx_sync_last_success_timestamp") > 0


@pytest.mark.asyncio
async def test_failed_run_is_saved_without_losing_last_success(tmp_path, monkeypatch, repo, gateway):
    monkeypatch.setenv(metrics_push.PUSHGATEWAY_URL_ENV, gateway)
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile())
    last_success = totals_by_name(saved(tmp_path), "synctx_sync_last_success_timestamp")

    repo.head, repo.diff = "h2", [("M", "docs/motion/motion.js")]
    repo.files["docs/motion/motion.js"] = "window.v = 2;"
    eng.xwiki_client.upsert_page.side_effect = RuntimeError("401 unauthorized")
    await eng.run_sync(profile())

    totals = saved(tmp_path)
    assert totals_by_name(totals, "synctx_sync_failures_total", error_type="auth_error") == 1
    assert totals_by_name(totals, "synctx_sync_runs_total", result="failed") == 1
    assert totals_by_name(totals, "synctx_sync_last_success_timestamp") == last_success


@pytest.mark.asyncio
async def test_dry_run_does_not_save_metrics(tmp_path, monkeypatch, repo):
    eng = engine(tmp_path, monkeypatch)
    await eng.run_sync(profile(), dry_run=True)
    assert not (tmp_path / "metrics" / "r.json").exists()


@pytest.mark.asyncio
async def test_lost_lease_does_not_save_metrics(tmp_path, monkeypatch, repo):
    lock = FakeLock()
    locked_with(monkeypatch, lock)
    eng = engine(tmp_path, monkeypatch)

    async def publish_then_lose(*args, **kwargs):
        lock.lost = True
        return True

    eng.xwiki_client.upsert_page.side_effect = publish_then_lose
    with pytest.raises(sync_engine.LeaseLostError):
        await eng.run_sync(profile())
    assert not (tmp_path / "metrics" / "r.json").exists()


@pytest.mark.asyncio
async def test_push_failure_does_not_fail_sync(tmp_path, monkeypatch, repo):
    monkeypatch.setenv(metrics_push.PUSHGATEWAY_URL_ENV, "http://127.0.0.1:9")
    eng = engine(tmp_path, monkeypatch)
    report = await eng.run_sync(profile())
    assert report.fail_count == 0
    assert totals_by_name(saved(tmp_path), "synctx_sync_runs_total", result="success") == 1


def test_saved_file_is_readable_json(tmp_path):
    metrics = RepoMetrics(str(tmp_path), "m5")
    sync_runs_total.labels(repo_id="m5", result="success").inc()
    metrics.commit()
    data = json.loads((tmp_path / "metrics" / "m5.json").read_text("utf-8"))
    [runs] = [x for x in data["samples"] if x["name"] == "synctx_sync_runs_total"]
    assert runs == {"name": "synctx_sync_runs_total", "labels": {"repo_id": "m5", "result": "success"}, "value": 1.0}


def test_repo_without_success_gets_watch_start_as_last_success(tmp_path):
    run = RepoMetrics(str(tmp_path), "m7")
    sync_runs_total.labels(repo_id="m7", result="failed").inc()
    totals = run.commit()
    assert totals_by_name(totals, "synctx_sync_last_success_timestamp") > 0

    later = RepoMetrics(str(tmp_path), "m7")
    sync_runs_total.labels(repo_id="m7", result="failed").inc()
    assert totals_by_name(later.commit(), "synctx_sync_last_success_timestamp") == totals_by_name(
        totals, "synctx_sync_last_success_timestamp"
    )


def test_first_run_error_is_pushed_after_a_zero_baseline(tmp_path):
    auth = ("synctx_sync_failures_total", (("error_type", "auth_error"), ("repo_id", "m8"), ("target", "xwiki")))
    first = RepoMetrics(str(tmp_path), "m8")
    sync_failures_total.labels(repo_id="m8", target="xwiki", error_type="auth_error").inc()
    assert first.commit()[auth] == 0
    first.mark_pushed()

    second = RepoMetrics(str(tmp_path), "m8")
    assert second.commit()[auth] == 1

    third = RepoMetrics(str(tmp_path), "m8")
    sync_failures_total.labels(repo_id="m8", target="xwiki", error_type="auth_error").inc()
    assert third.commit()[auth] == 2


def test_deferred_increase_waits_until_zero_baseline_was_pushed(tmp_path):
    auth = ("synctx_sync_failures_total", (("error_type", "auth_error"), ("repo_id", "m9"), ("target", "xwiki")))
    first = RepoMetrics(str(tmp_path), "m9")
    sync_failures_total.labels(repo_id="m9", target="xwiki", error_type="auth_error").inc()
    assert first.commit()[auth] == 0

    after_failed_push = RepoMetrics(str(tmp_path), "m9")
    assert after_failed_push.commit()[auth] == 0
    after_failed_push.mark_pushed()

    after_baseline = RepoMetrics(str(tmp_path), "m9")
    assert after_baseline.commit()[auth] == 1


def test_error_after_unpushed_baseline_stays_deferred(tmp_path):
    auth = ("synctx_sync_failures_total", (("error_type", "auth_error"), ("repo_id", "m11"), ("target", "xwiki")))
    RepoMetrics(str(tmp_path), "m11").commit()

    failing = RepoMetrics(str(tmp_path), "m11")
    sync_failures_total.labels(repo_id="m11", target="xwiki", error_type="auth_error").inc()
    assert failing.commit()[auth] == 0
    failing.mark_pushed()

    assert RepoMetrics(str(tmp_path), "m11").commit()[auth] == 1


def test_first_items_are_deferred_with_failures_so_failure_rate_keeps_denominator(tmp_path):
    items = ("synctx_sync_items_total", (("action", "M"), ("repo_id", "m10"), ("target", "xwiki")))
    first = RepoMetrics(str(tmp_path), "m10")
    sync_items_total.labels(repo_id="m10", target="xwiki", action="M").inc(10)
    assert first.commit()[items] == 0
    first.mark_pushed()

    second = RepoMetrics(str(tmp_path), "m10")
    sync_items_total.labels(repo_id="m10", target="xwiki", action="M").inc(2)
    assert second.commit()[items] == 12


def test_metrics_file_does_not_collide_with_other_repo_state(tmp_path):
    from state import RepoState, StateManager
    StateManager(str(tmp_path)).save_state("demo.metrics", RepoState(repo_id="demo.metrics", last_successful_sha="abc"))
    RepoMetrics(str(tmp_path), "demo").commit()
    assert StateManager(str(tmp_path)).get_state("demo.metrics").last_successful_sha == "abc"


def test_deferred_increase_waits_until_baseline_could_be_scraped(tmp_path, monkeypatch):
    auth = ("synctx_sync_failures_total", (("error_type", "auth_error"), ("repo_id", "m12"), ("target", "xwiki")))
    monkeypatch.setattr(metrics_push, "BASELINE_SCRAPE_GRACE_SECONDS", 60)
    now = [1000.0]
    monkeypatch.setattr(metrics_push.time, "time", lambda: now[0])
    first = RepoMetrics(str(tmp_path), "m12")
    sync_failures_total.labels(repo_id="m12", target="xwiki", error_type="auth_error").inc()
    first.commit()
    first.mark_pushed()

    now[0] += 5
    assert RepoMetrics(str(tmp_path), "m12").commit()[auth] == 0

    now[0] += 60
    assert RepoMetrics(str(tmp_path), "m12").commit()[auth] == 1


@pytest.mark.asyncio
async def test_failure_to_record_push_does_not_fail_sync(tmp_path, monkeypatch, repo, gateway):
    monkeypatch.setenv(metrics_push.PUSHGATEWAY_URL_ENV, gateway)

    def broken(self):
        raise OSError("disk full")

    monkeypatch.setattr(RepoMetrics, "mark_pushed", broken)
    eng = engine(tmp_path, monkeypatch)
    report = await eng.run_sync(profile())
    assert report.fail_count == 0


def test_prune_deletes_groups_of_repos_removed_from_config(gateway):
    Gateway.groups = [
        {"job": "synctx-sync", "repo_id": "kept"},
        {"job": "synctx-sync", "repo_id": "removed"},
        {"job": "other", "repo_id": "removed"},
        {"job": "synctx-sync"},
    ]
    assert metrics_push.prune_removed_repos(gateway, {"kept"}) == ["removed"]
    assert Gateway.deleted == ["/metrics/job/synctx-sync/repo_id/removed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("argv,pruned", [([], True), (["--repo", "kept"], False), (["--dry-run"], False)])
async def test_cli_prunes_only_after_full_run(monkeypatch, argv, pruned):
    import sync_cli
    from synctx_common.config import RepoAuthConfig, RepoProfile, SyncConfig, SynctxConfig

    class FakeEngine:
        def __init__(self, config):
            pass

        async def run_sync(self, repo, dry_run=False):
            return None

        async def close(self):
            pass

    calls = []
    config = SynctxConfig(
        repositories=[RepoProfile(id="kept", name="K", url="https://git.test/k.git", auth=RepoAuthConfig(type="none"))],
        sync=SyncConfig(),
    )
    monkeypatch.setattr(sync_cli, "load_config", lambda path: config)
    monkeypatch.setattr(sync_cli, "SyncEngine", FakeEngine)
    monkeypatch.setattr(sync_cli, "prune_removed_repos", lambda url, ids: calls.append((url, list(ids))) or [])
    monkeypatch.setenv(metrics_push.PUSHGATEWAY_URL_ENV, "http://pg:9091")
    monkeypatch.setattr("sys.argv", ["synctx-sync", *argv])

    assert await sync_cli.async_main() == 0
    assert calls == ([("http://pg:9091", ["kept"])] if pruned else [])


@pytest.mark.asyncio
async def test_cli_prunes_when_last_repository_was_removed(monkeypatch):
    import sync_cli
    from synctx_common.config import SyncConfig, SynctxConfig

    calls = []
    monkeypatch.setattr(sync_cli, "load_config", lambda path: SynctxConfig(repositories=[], sync=SyncConfig()))
    monkeypatch.setattr(sync_cli, "prune_removed_repos", lambda url, ids: calls.append(list(ids)) or [])
    monkeypatch.setenv(metrics_push.PUSHGATEWAY_URL_ENV, "http://pg:9091")
    monkeypatch.setattr("sys.argv", ["synctx-sync"])

    assert await sync_cli.async_main() == 1
    assert calls == [[]]
