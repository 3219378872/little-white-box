from unittest.mock import Mock

import pytest

import api_client
import poll


def test_eventually_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(poll.time, "sleep", lambda _: None)
    attempts = iter([ValueError("503 body"), None, "ready"])

    def check():
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    assert poll.eventually(check, timeout=5) == "ready"


def test_eventually_chains_last_error_at_deadline(monkeypatch):
    monkeypatch.setattr(poll.time, "sleep", lambda _: None)

    def check():
        raise KeyError("items")

    with pytest.raises(poll.EventuallyFailed, match="KeyError") as failed:
        poll.eventually(check, timeout=0)
    assert isinstance(failed.value.__cause__, KeyError)


def test_eventually_lets_pytest_skip_escape():
    def check():
        pytest.skip("probe unavailable")

    with pytest.raises(pytest.skip.Exception):
        poll.eventually(check, timeout=5)


def test_sse_stream_is_bounded_by_wall_clock(monkeypatch):
    clock = iter([0.0, 1.0, 2.0, 999.0])
    monkeypatch.setattr(api_client.time, "monotonic", lambda: next(clock))
    response = Mock()
    response.iter_content.return_value = iter([b": heartbeat\n\n"] * 4)

    with pytest.raises(AssertionError, match="not finished within 10s"):
        api_client.parse_sse_stream(response, timeout=10)


def test_sse_stream_stops_at_terminal_frame():
    response = Mock()
    response.iter_content.return_value = iter(
        [b": heartbeat\n\n", b'data: {"type": "token"}\n\n', b'data: {"type": "done"}\n\n'])

    frames = api_client.parse_sse_stream(response)

    assert [f["type"] for f in frames] == ["token", "done"]
