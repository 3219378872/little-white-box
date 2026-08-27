import pytest

from api_client import assert_error, error_of, parse_sse_stream

_ASSISTANT_DB_HINT = (
    "DB_ASSISTANT/schema is required (derive from DB_CONTENT; replay "
    "patches + GRANT ALL on xbh_assistant)"
)


def _assert_assistant_store_ok(resp, action):
    assert resp.status_code != 503, (
        f"{action} returned HTTP 503; {_ASSISTANT_DB_HINT}; "
        f"body={resp.text[:200]}")
    assert resp.status_code != 500, (
        f"{action} returned HTTP 500; {_ASSISTANT_DB_HINT}; "
        f"body={resp.text[:200]}")


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
    assert_error(anon.list_assistant_memory(), 401, 1006)
    assert_error(anon.update_assistant_memory(1, value="rpg"), 401, 1006)
    assert_error(anon.delete_assistant_memory(1), 401, 1006)
    assert_error(anon.list_assistant_watch(), 401, 1006)
    assert_error(anon.create_assistant_watch({
        "conditionType": "author_new_post",
        "targetType": "author",
        "targetId": 1,
    }), 401, 1006)
    assert_error(anon.update_assistant_watch(1, False), 401, 1006)
    assert_error(anon.delete_assistant_watch(1), 401, 1006)
    assert_error(anon.list_assistant_watch_hits(), 401, 1006)
    assert_error(anon.mark_assistant_watch_hits_read([1]), 401, 1006)
    assert_error(anon.submit_assistant_recommend_feedback(1, "dislike"),
                 401, 1006)


def test_consent_includes_version_fields(user):
    r = user.client.get_agent_consent()
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert isinstance(body.get("consentVersion"), int)
    assert isinstance(body.get("currentVersion"), int)
    assert body["currentVersion"] == 2


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


def test_memory_list_after_consent(user):
    client = user.client
    r = client.set_agent_consent(True)
    assert r.status_code == 200, r.text[:200]
    try:
        listed = client.list_assistant_memory()
        _assert_assistant_store_ok(listed, "GET /assistant/memory")
        assert listed.status_code == 200, listed.text[:200]
        body = listed.json()
        assert isinstance(body.get("items"), list)
    finally:
        client.set_agent_consent(False)


def test_watch_crud_and_unknown_condition(user, published_post):
    client = user.client
    post = published_post(user.client)
    detail = client.post_detail(post["postId"])
    assert detail.status_code == 200, detail.text[:200]
    author_id = detail.json()["authorId"]
    payload = {
        "conditionType": "author_new_post",
        "targetType": "author",
        "targetId": author_id,
    }
    created = client.create_assistant_watch(payload)
    _assert_assistant_store_ok(created, "POST /assistant/watch")
    assert created.status_code == 200, created.text[:200]
    task = created.json().get("task") or {}
    task_id = task.get("id")
    assert isinstance(task_id, int) and task_id > 0

    try:
        listed = client.list_assistant_watch()
        _assert_assistant_store_ok(listed, "GET /assistant/watch")
        assert listed.status_code == 200, listed.text[:200]
        tasks = listed.json().get("tasks")
        assert isinstance(tasks, list)
        assert any(item.get("id") == task_id for item in tasks)

        dup = client.create_assistant_watch(payload)
        _assert_assistant_store_ok(dup, "POST /assistant/watch duplicate")
        assert 400 <= dup.status_code < 500, (
            f"duplicate watch must be conflict/error not 500: "
            f"{dup.status_code} {dup.text[:200]}")

        patched = client.update_assistant_watch(task_id, False)
        _assert_assistant_store_ok(patched, "PATCH /assistant/watch")
        assert patched.status_code == 200, patched.text[:200]
        after = client.list_assistant_watch().json().get("tasks") or []
        found = next(item for item in after if item.get("id") == task_id)
        assert found.get("enabled") is False

        unknown = client.create_assistant_watch({
            "conditionType": "price_drop",
            "targetType": "post",
            "targetId": post["postId"],
        })
        _assert_assistant_store_ok(unknown, "POST /assistant/watch unknown")
        assert 400 <= unknown.status_code < 500, (
            f"unknown conditionType must be 4xx, got {unknown.status_code}: "
            f"{unknown.text[:200]}")
    finally:
        deleted = client.delete_assistant_watch(task_id)
        _assert_assistant_store_ok(deleted, "DELETE /assistant/watch")
        assert deleted.status_code == 200, deleted.text[:200]
        remaining = client.list_assistant_watch()
        assert remaining.status_code == 200, remaining.text[:200]
        assert all(item.get("id") != task_id
                   for item in remaining.json().get("tasks") or [])


def test_watch_hits_list_empty_inbox(user):
    listed = user.client.list_assistant_watch_hits()
    _assert_assistant_store_ok(listed, "GET /assistant/watch/hits")
    assert listed.status_code == 200, listed.text[:200]
    body = listed.json()
    assert isinstance(body.get("hits"), list)


def test_recommend_feedback_never_500(user, published_post):
    post = published_post(user.client)
    r = user.client.submit_assistant_recommend_feedback(
        post["postId"], "dislike")
    _assert_assistant_store_ok(r, "POST /assistant/recommend/feedback")
    assert r.status_code != 500, (
        f"valid dislike feedback must not 500: {r.text[:200]}")
    if r.status_code != 200:
        assert error_of(r) is not None, (
            f"expected 200 or structured error, got HTTP {r.status_code}: "
            f"{r.text[:200]}")
