from synctx_common.config import OutboundFeedbackConfig, load_config


def test_external_importer_is_not_part_of_internal_config():
    assert "external_importer" not in OutboundFeedbackConfig.model_fields


def test_legacy_external_importer_block_is_ignored(tmp_path):
    path = tmp_path / ".synctx.yaml"
    path.write_text(
        "outbound_feedback:\n"
        "  enabled: true\n"
        "  replay_window_minutes: 60\n"
        "  external_importer:\n"
        "    create_pr: true\n"
        "    direct_push: false\n"
        '    branch_prefix: "synctx-feedback/"\n',
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.outbound_feedback.enabled is True
    assert cfg.outbound_feedback.replay_window_minutes == 60
    assert not hasattr(cfg.outbound_feedback, "external_importer")
