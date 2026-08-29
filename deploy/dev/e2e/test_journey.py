import time

from api_client import parse_sse_stream
from poll import eventually
from support import unique_key, unique_marker


def test_full_community_journey(anon, make_user, png_bytes):
    ready = anon.health_ready().json()
    assert ready["dependencies"]

    author = make_user()
    reader = make_user()

    upload = author.client.upload_image(("avatar.png", png_bytes, "image/png"))
    assert upload.status_code == 200
    media = upload.json()

    marker = unique_marker()
    profile_update = author.client.update_profile(
        {"nickname": f"journey {marker}", "bio": f"journey bio {marker}",
         "avatarUrl": media["url"]})
    assert profile_update.status_code == 200
    profile = anon.get_user(author.id).json()
    assert profile["nickname"] == f"journey {marker}"

    follow = reader.client.follow(author.id)
    assert follow.status_code == 200

    title = f"journey post {marker}"
    create = author.client.create_post(
        {"title": title,
         "content": f"journey content {marker}",
         "tags": [f"tag{marker}"],
         "mediaIds": [media["mediaId"]],
         "status": 1})
    assert create.status_code == 200, create.text[:200]
    created = create.json()
    post_id = created["postId"]
    revision = created["revision"]

    def in_follow_feed():
        resp = reader.client.follow_feed(pageSize=50)
        return any(item["postId"] == post_id for item in resp.json()["items"])
    eventually(in_follow_feed, desc="journey post fans out to follower feed",
               timeout=90)

    like = reader.client.like(post_id, 1)
    fav = reader.client.favorite(post_id)
    comment = reader.client.comment_create(
        {"postId": post_id, "content": f"journey comment {marker}",
         "idempotencyKey": unique_key()})
    assert like.status_code == 200 and fav.status_code == 200
    assert comment.status_code == 200, comment.text[:200]
    comment_id = comment.json()["commentId"]

    reply = author.client.comment_create(
        {"postId": post_id, "content": "journey reply", "parentId": comment_id,
         "replyUserId": reader.id, "idempotencyKey": unique_key()})
    assert reply.status_code == 200

    detail = eventually(
        lambda: (d := reader.client.post_detail(post_id).json())
        and d["isLiked"] and d["isFavorited"] and d["likeCount"] >= 1
        and d["favoriteCount"] >= 1 and d["commentCount"] >= 2 and d or None,
        desc="post detail converges after interactions", timeout=180)

    def searchable():
        resp = anon.search(marker, pageSize=20)
        return any(p["id"] == post_id for p in resp.json().get("posts", []))
    eventually(searchable, desc="journey post appears in search",
               timeout=180, interval=3)

    rec = reader.client.recommend(request_id=unique_key("rq"), pageSize=5)
    assert rec.status_code == 200
    positions = [item["position"] for item in rec.json()["items"]]
    assert positions == sorted(positions)

    dm_key = unique_key("dm")
    dm_payload = {"receiverId": reader.id, "content": f"journey dm {marker}",
                  "msgType": 1, "idempotencyKey": dm_key}
    dm_first = author.client.send_message(dm_payload)
    dm_replay = author.client.send_message(dm_payload)
    assert dm_first.status_code == 200 and dm_replay.status_code == 200
    assert dm_replay.json()["messageId"] == dm_first.json()["messageId"]

    convo = eventually(lambda: next(
        (c for c in reader.client.conversations(pageSize=50).json()["conversations"]
         if c["targetUserId"] == author.id), None),
        desc="reader sees DM conversation", timeout=30)

    def unread_visible():
        current = next(
            (c for c in reader.client.conversations(pageSize=50).json()["conversations"]
             if c["targetUserId"] == author.id), None)
        return current is not None and current["unreadCount"] >= 1
    eventually(unread_visible, desc="unread count on receiver side", timeout=60)

    read = reader.client.mark_conversation_read(convo["id"])
    assert read.status_code == 200

    events_payload = [
        {"clientEventId": unique_key("ce"), "occurredAt": int(time.time() * 1000),
         "action": "click", "targetId": post_id, "targetType": "post"},
        {"clientEventId": unique_key("ce"), "occurredAt": int(time.time() * 1000),
         "action": "exposure", "targetId": post_id, "targetType": "post",
         "requestId": unique_key("rq"), "position": 1, "scene": "home"},
    ]
    behavior = reader.client.behavior_events(events_payload,
                                             anonymous_id=f"journey-{marker}")
    assert behavior.status_code == 202, behavior.text[:200]
    accepted_ids = [res["clientEventId"] for res
                    in behavior.json()["results"] if res["accepted"]]
    assert len(accepted_ids) == 2

    reader.client.set_agent_consent(True)
    posted = reader.client.post_assistant_message(
        f"帖子 {title} 讲了什么", request_id=unique_key("asst"))
    if posted.status_code == 200:
        run_id = posted.json().get("runId")
        if isinstance(run_id, int) and run_id > 0:
            events = reader.client.assistant_run_events(run_id)
            if events.status_code == 200:
                frames = parse_sse_stream(events)
                types = [f["type"] for f in frames]
                assert types, "assistant stream produced no frames"
                if types[-1] == "done":
                    assert "token" in types or "run_started" in types

    update = author.client.update_post(
        post_id, {"title": f"{title} v2", "content": f"journey content {marker} updated",
                  "expectedRevision": revision})
    assert update.status_code == 200, update.text[:200]
    new_revision = update.json()["revision"]
    assert new_revision == revision + 1

    conflict = author.client.update_post(
        post_id, {"title": title, "content": "conflict body",
                  "expectedRevision": revision})
    assert conflict.status_code == 409, conflict.text[:200]
    assert conflict.json()["code"] == 2007

    delete = author.client.delete_post(post_id, new_revision)
    assert delete.status_code == 200
    gone = anon.post_detail(post_id)
    assert gone.status_code == 404

    unfollow = reader.client.unfollow(author.id)
    assert unfollow.status_code == 200
