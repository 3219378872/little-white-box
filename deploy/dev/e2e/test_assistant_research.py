import json
import os

import pytest

from api_client import parse_sse_stream
from poll import eventually
from support import unique_key


pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_EXPECT_ASSISTANT_RESEARCH") != "1",
    reason="requires the deterministic research provider fixture",
)


@pytest.mark.parametrize("scenario", ["community", "web", "web_failure", "invalid_citation"])
def test_research_roundtrip(user, make_user, published_post, scenario):
    client = user.client
    assert client.set_agent_consent(True).status_code == 200
    query = unique_key("research")
    post = None
    if scenario in {"community", "invalid_citation"}:
        post = published_post(client, title=query)
        eventually(lambda: any(str(item.get("id", item.get("postId"))) == str(post["postId"])
                               for item in client.search(query).json().get("posts", [])),
                   timeout=40, interval=1, desc="research post indexed")
    if scenario == "web_failure":
        query += " WEB_UNAVAILABLE"
    request = {"message": "E2E_RESEARCH_MARKER " + json.dumps({
        "query": query, "invalidCitation": scenario == "invalid_citation"}),
        "requestId": unique_key("research-command"), "clientProtocolVersion": 2}
    try:
        accepted = client.post("/api/v2/assistant/messages", json=request)
        assert accepted.status_code == 200, accepted.text[:200]
        run_id = accepted.json()["runId"]

        def waiting():
            response = client.get_assistant_thread()
            assert response.status_code == 200, response.text[:200]
            return (response.json().get("thread") or {}).get("questionRequest")

        question = eventually(waiting, timeout=20, interval=.25, desc="durable question")
        assert question["status"] == "pending"
        with client.assistant_run_events(run_id) as response:
            assert response.status_code == 200
            pending = parse_sse_stream(response, stop_types={"questions_required", "error"})
        assert pending[-1]["type"] == "questions_required", pending
        assert not any(item["type"] in {"done", "answer_committed"} for item in pending)
        assert waiting()["id"] == question["id"], "disconnect must preserve the question"
        answers = {"questionRequestId": question["id"], "requestId": unique_key("answers"),
                   "answers": [{"questionId": "priority", "selectedOptionIds": [],
                                "text": "", "disposition": "unknown"}]}
        other = make_user()
        assert other.client.post(f"/api/v2/assistant/runs/{run_id}/answers", json=answers).status_code == 404
        submitted = client.post(f"/api/v2/assistant/runs/{run_id}/answers", json=answers)
        assert submitted.status_code == 200, submitted.text[:200]
        assert client.post(f"/api/v2/assistant/runs/{run_id}/answers", json=answers).status_code == 200
        changed = {**answers, "requestId": unique_key("different-answer")}
        assert client.post(f"/api/v2/assistant/runs/{run_id}/answers", json=changed).status_code == 409
        eventually(lambda: not client.get_assistant_thread().json()["thread"]["activeRunId"],
                   timeout=40, interval=.25, desc="research run terminal")
        with client.assistant_run_events(run_id) as response:
            assert response.status_code == 200
            events = parse_sse_stream(response)
        assert events[-1]["type"] == "done", events[-1]
        committed = [event for event in events if event["type"] == "answer_committed"]
        assert len(committed) == 1
        answer = committed[0]["answerPresentation"]
        assert all("UNVALIDATED_DRAFT_MARKER" not in event.get("text", "")
                   for event in events if event["type"] in {"token", "answer_committed", "done"})
        history = client.list_assistant_messages().json()["messages"]
        saved = [message for message in history if message.get("answerPresentation")]
        assert len(saved) == 1 and saved[0]["answerPresentation"] == answer
        assert any(message.get("questionRequest", {}).get("answers", [{}])[0].get("disposition") == "unknown"
                   for message in history if message.get("questionRequest"))
        if scenario == "web_failure":
            assert answer["sources"] == []
            assert "不可用" in answer["blocks"][0]["text"]
        else:
            source = answer["sources"][0]
            assert source["available"] is True and source["excerpts"]
            citation = answer["blocks"][0]["citations"][0]
            assert citation["handle"] == source["handle"]
            assert citation["evidenceIds"][0] == source["excerpts"][0]["id"]
            assert source["kind"] == ("web" if scenario == "web" else "post")
        if post:
            deleted = client.delete_post(post["postId"], post["revision"])
            assert deleted.status_code == 200, deleted.text[:200]
            stale = client.list_assistant_messages().json()["messages"]
            source = next(message["answerPresentation"]["sources"][0]
                          for message in stale if message.get("answerPresentation"))
            assert source["available"] is False and source["excerpts"] == []
        assert client.delete_assistant_history().status_code == 200
        assert client.list_assistant_messages().json()["messages"] == []
    finally:
        client.set_agent_consent(False)
