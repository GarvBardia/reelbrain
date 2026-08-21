"""scripts/ensure_ollama.py — the pure orchestration logic. All I/O mocked:
no real Ollama process, no real HTTP calls, no real sleeps."""
from scripts import ensure_ollama as eo


def _fake_clock(start=0.0):
    """A monotonic clock you can advance by calling sleep_fn -- so the
    timeout loop can be exercised without a real wall-clock wait."""
    state = {"t": start}

    def now():
        return state["t"]

    def sleep(seconds):
        state["t"] += seconds

    return now, sleep


def test_already_up_never_attempts_to_start():
    started = []
    ok = eo.ensure_ollama(is_up_fn=lambda: True, start_fn=lambda: started.append(1) or True)
    assert ok is True
    assert started == []  # never even tried to launch it


def test_down_then_starts_and_comes_up_within_timeout():
    now, sleep = _fake_clock()
    # up-checks: down, down, then up on the third poll
    responses = iter([False, False, True])
    ok = eo.ensure_ollama(
        is_up_fn=lambda: next(responses),
        start_fn=lambda: True,
        sleep_fn=sleep, now_fn=now,
        timeout_seconds=30, poll_interval=2,
    )
    assert ok is True


def test_start_fn_fails_to_launch_returns_false_without_polling():
    now, sleep = _fake_clock()
    polled = []
    ok = eo.ensure_ollama(
        is_up_fn=lambda: polled.append(1) or False,
        start_fn=lambda: False,
        sleep_fn=sleep, now_fn=now,
    )
    assert ok is False
    # the first is_up_fn() call (the initial check) is expected; no polling
    # after a failed launch
    assert len(polled) == 1


def test_starts_but_never_responds_times_out():
    now, sleep = _fake_clock()
    ok = eo.ensure_ollama(
        is_up_fn=lambda: False,  # never comes up
        start_fn=lambda: True,
        sleep_fn=sleep, now_fn=now,
        timeout_seconds=10, poll_interval=2,
    )
    assert ok is False


def test_never_raises_and_always_returns_a_bool(capsys):
    # A real regression this guards: main()'s whole point is to never block
    # the batch file, so this must be a clean bool, not an exception.
    now, sleep = _fake_clock()
    ok = eo.ensure_ollama(
        is_up_fn=lambda: False, start_fn=lambda: True,
        sleep_fn=sleep, now_fn=now, timeout_seconds=4, poll_interval=2,
    )
    assert isinstance(ok, bool)
    out = capsys.readouterr().out
    assert "[ensure_ollama]" in out


def test_real_start_reports_missing_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(eo, "OLLAMA_EXE", str(tmp_path / "does-not-exist.exe"))
    assert eo._real_start() is False
