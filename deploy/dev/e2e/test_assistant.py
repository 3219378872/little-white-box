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


def _sse_error_code(frames):
    error_frames = [f for f in frames if f.get("type") == "error"]
    return error_frames[0].get("errorCode") if error_frames else None


def test_agent_endpoints_require_auth(anon):
    assert_error(anon.get_agent_consent(), 401, 1006)
    assert_error(anon.set_agent_consent(True), 401, 1006)


def test_agent_mode_gate_and_revoke(user):
    client = user.client

    granted = client.get_agent_consent()
    assert granted.status_code == 200
    original = granted.json().get("granted")

    def cleanup():
        client.set_agent_consent(False)

    try:
        # 未授权：agent 请求被网关结构化拒绝（AGNT-002）。
        if not original:
            resp = client.assistant_chat("hello", stream=True, mode="agent",
                                         request_id="e2e-agent-gate")
            frames = parse_sse_stream(resp)
            assert _sse_error_code(frames) == "AGENT_NOT_AUTHORIZED"

        # 授权后：不再返回授权错误（模型不可用时按 AGNT-061 结构化降级）。
        r = client.set_agent_consent(True)
        assert r.status_code == 200
        assert client.get_agent_consent().json()["granted"] is True
        resp = client.assistant_chat("hello", stream=True, mode="agent",
                                     request_id="e2e-agent-granted")
        frames = parse_sse_stream(resp)
        code = _sse_error_code(frames)
        assert code != "AGENT_NOT_AUTHORIZED", f"unexpected auth error: {frames}"

        # 撤销后立即拒绝（AGNT-006）。
        r = client.set_agent_consent(False)
        assert r.status_code == 200
        resp = client.assistant_chat("hello", stream=True, mode="agent",
                                     request_id="e2e-agent-revoked")
        frames = parse_sse_stream(resp)
        assert _sse_error_code(frames) == "AGENT_NOT_AUTHORIZED"
    finally:
        cleanup()


def test_tool_confirm_rejects_unknown_call(user):
    client = user.client
    r = client.confirm_assistant_tool("e2e-request", "no-such-call", True)
    assert r.status_code == 400


def test_enhanced_search_default_unchanged(user):
    resp = user.client.assistant_chat("用一句话介绍这个社区", stream=True,
                                      request_id="e2e-enhanced-default")
    frames = parse_sse_stream(resp)
    assert frames, "enhanced_search produced no frames"
    codes = {f.get("errorCode") for f in frames if f.get("type") == "error"}
    assert "AGENT_NOT_AUTHORIZED" not in codes
