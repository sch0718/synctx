import importlib
import sys
import os

import pytest

_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
_app_module = None


def _load_app():
    for k in list(sys.modules.keys()):
        if k == "app" or k.startswith("app."):
            del sys.modules[k]
    if _src in sys.path:
        sys.path.remove(_src)
    sys.path.insert(0, _src)
    return importlib.import_module("app")


def pytest_collectstart(collector):
    # conftest는 pytest 시작 시점에 로드되므로, 다른 suite가 sys.path에 끼워 넣은 동명 `app` 모듈을
    # 피하려면 이 디렉토리의 테스트 모듈을 import하기 직전에 경로와 캐시를 다시 맞춰야 한다.
    global _app_module
    if isinstance(collector, pytest.Module):
        _app_module = _load_app()


@pytest.fixture(autouse=True)
def _cdn_app_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "app", _app_module)
