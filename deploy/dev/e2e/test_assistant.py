import pytest

from api_client import assert_error, parse_sse_stream


def _open_stream_or_skip(client, message, attempts=4):
    resp = client.assistant_chat_stream(message, attempts=attempts)
    if resp.status_code != 200:
        raise AssertionError(
            f"assistant chat returned HTTP {resp.status_code}: {resp.text[:150]}")
    return resp


def test_chat_requires_auth(anon):
    r = anon.assistant_chat("hello")
    assert_error(r, 401, 1006)


def test_empty_message_yields_error_frame(user):
    resp = user.client.assistant_chat("", stream=True)
    assert resp.status_code == 200
    frames = parse_sse_stream(resp)
    assert frames, "expected at least one SSE frame"
    error_frames = [f for f in frames if f["type"] == "error"]
    assert error_frames, f"expected error frame, got {[f['type'] for f in frames]}"
    assert error_frames[0]["errorCode"] == "INVALID_REQUEST"
    assert error_frames[0]["degraded"] is True


def test_stream_content_type_is_event_stream(user):
    resp = _open_stream_or_skip(user.client, "介绍一下这个社区")
    assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
    resp.close()


def test_stream_emits_tokens_then_done(user):
    resp = _open_stream_or_skip(user.client, "用一句话介绍这个社区")

    frames = parse_sse_stream(resp)
    assert frames, "no SSE frames received"
    types = [f["type"] for f in frames]
    if types[-1] == "error":
        pytest.skip(f"assistant degraded by upstream: {frames[-1].get('errorCode')}")

    assert "token" in types, f"expected token frames, got types={types}"
    assert types[-1] == "done", f"stream did not end with done frame: {types}"

    conversation_ids = {f["conversationId"] for f in frames}
    assert len(conversation_ids) == 1
    assert next(iter(conversation_ids)), "empty conversationId"

    text = "".join(f.get("text", "") for f in frames if f["type"] == "token")
    assert text.strip(), "assistant produced no text"


def test_overlong_message_rejected_locally(user):
    resp = user.client.assistant_chat("字" * 2001, stream=True)
    assert resp.status_code == 200
    frames = parse_sse_stream(resp)
    error_frames = [f for f in frames if f["type"] == "error"]
    assert error_frames, f"expected local rejection frame, got {[f['type'] for f in frames]}"
