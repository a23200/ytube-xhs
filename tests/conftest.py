import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_YTDLP_AUTH_DIR", str(tmp_path / "runtime-auth"))
