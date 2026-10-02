from types import SimpleNamespace

import triage.main as tm
from triage.gmail_client import AuthError


def test_eval_returns_2_on_auth_error(monkeypatch):
    def boom(*a, **k):
        raise AuthError("expired")

    monkeypatch.setattr(tm.GmailClient, "from_token", staticmethod(boom))
    monkeypatch.setattr(tm, "load_router", lambda: object())
    assert tm.main(["eval"]) == 2


def test_bad_config_returns_1(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad config")

    monkeypatch.setattr(tm, "load_config", boom)
    assert tm.main(["once"]) == 1


def test_run_survives_auth_error_then_stops_on_keyboard_interrupt(monkeypatch, tmp_path):
    sleeps, builds, runs = [], [], []

    def from_token(token_path, read_only):
        builds.append(1)
        return object()

    def run_once(*a, **k):
        runs.append(1)
        if len(runs) == 1:
            raise AuthError("revoked")
        return "ok"

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(tm, "APP_DIR", tmp_path)
    monkeypatch.setattr(tm, "load_config", lambda d: SimpleNamespace(interval_minutes=1))
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(tm.GmailClient, "from_token", staticmethod(from_token))
    monkeypatch.setattr(tm, "load_router", lambda: object())
    monkeypatch.setattr(tm, "Classifier", lambda cfg, router: object())
    monkeypatch.setattr(tm, "run_once", run_once)
    monkeypatch.setattr(tm.time, "sleep", sleep)
    assert tm.main(["run"]) == 0
    assert sleeps[0] == 3600
    assert len(builds) == 2
    assert len(runs) == 2


def _setup_run(monkeypatch, tmp_path, from_token, run_once, sleep):
    monkeypatch.setattr(tm, "APP_DIR", tmp_path)
    monkeypatch.setattr(tm, "load_config", lambda d: SimpleNamespace(interval_minutes=1))
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(tm.GmailClient, "from_token", staticmethod(from_token))
    monkeypatch.setattr(tm, "load_router", lambda: object())
    monkeypatch.setattr(tm, "Classifier", lambda cfg, router: object())
    monkeypatch.setattr(tm, "run_once", run_once)
    monkeypatch.setattr(tm.time, "sleep", sleep)


def test_run_rebuild_auth_errors_never_exit(monkeypatch, tmp_path, caplog):
    sleeps, builds, runs = [], [], []

    def from_token(token_path, read_only):
        builds.append(1)
        if len(builds) in (2, 3):
            raise AuthError("revoked")
        return object()

    def run_once(*a, **k):
        runs.append(1)
        if len(runs) == 1:
            raise AuthError("revoked")
        return "ok"

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 4:
            raise KeyboardInterrupt

    _setup_run(monkeypatch, tmp_path, from_token, run_once, sleep)
    assert tm.main(["run"]) == 0
    assert sleeps[:3] == [3600, 3600, 3600]
    assert len(builds) == 4
    assert "re-run auth.py on the host" in caplog.text


def test_run_startup_auth_error_does_not_exit(monkeypatch, tmp_path):
    sleeps, builds = [], []

    def from_token(token_path, read_only):
        builds.append(read_only)
        if len(builds) == 1:
            raise AuthError("revoked")
        return object()

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    _setup_run(monkeypatch, tmp_path, from_token, lambda *a, **k: "ok", sleep)
    assert tm.main(["run"]) == 0
    assert sleeps[0] == 3600 and builds == [False, False]


def test_once_startup_auth_error_returns_2(monkeypatch, tmp_path):
    def from_token(token_path, read_only):
        raise AuthError("revoked")

    _setup_run(monkeypatch, tmp_path, from_token, lambda *a, **k: "ok", lambda s: None)
    assert tm.main(["once"]) == 2
