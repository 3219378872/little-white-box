import time

import pytest

from api_client import assert_error
from dbprobe import DbUnavailable, clickhouse
from support import unique_key, unique_marker


def _event(action, target_id, target_type="post", **extra):
    event = {"clientEventId": unique_key("ce"), "occurredAt": int(time.time() * 1000),
             "action": action, "targetId": target_id, "targetType": target_type}
    event.update(extra)
    return event


def test_valid_batch_returns_202_with_results(user, published_post):
    post = published_post(user.client)
    events = [
        _event("click", post["postId"]),
        _event("exposure", post["postId"], requestId=unique_key("rq"),
               position=1, scene="home"),
    ]
    r = user.client.behavior_events(events)
    assert r.status_code == 202, f"expected 202 Accepted, got {r.status_code}"
    body = r.json()
    assert body["acceptedCount"] == 2
    assert body["rejectedCount"] == 0
    results = body["results"]
    assert len(results) == 2
    by_id = {res["clientEventId"]: res for res in results}
    for event in events:
        result = by_id[event["clientEventId"]]
        assert result["accepted"] is True
        assert result["eventId"] > 0


def test_exposure_position_zero_rejected(user, published_post):
    post = published_post(user.client)
    r = user.client.behavior_events(
        [_event("exposure", post["postId"], requestId=unique_key("rq"),
                position=0, scene="home")])
    body = r.json()
    assert body["rejectedCount"] == 1
    result = body["results"][0]
    assert result["accepted"] is False
    assert "position" in result["reason"]


def test_exposure_missing_scene_rejected(user, published_post):
    post = published_post(user.client)
    r = user.client.behavior_events(
        [_event("exposure", post["postId"], requestId=unique_key("rq"),
                position=1)])
    body = r.json()
    assert body["rejectedCount"] == 1
    assert "scene" in body["results"][0]["reason"]


def test_client_forbidden_actions_rejected(user, published_post):
    post = published_post(user.client)
    events = [_event("like", post["postId"]),
              _event("impression", post["postId"])]
    r = user.client.behavior_events(events)
    assert r.status_code == 202
    body = r.json()
    assert body["acceptedCount"] == 0
    assert body["rejectedCount"] == 2
    assert all(res["accepted"] is False and res["reason"]
               for res in body["results"])


def test_empty_events_batch_param_error(anon):
    r = anon.post("/api/v2/behavior/events", json={"events": []})
    assert r.status_code == 400
    assert r.json()["code"] == 2
    assert "行为事件数量" in r.json()["message"]


def test_over_hundred_events_batch_param_error(user, published_post):
    post = published_post(user.client)
    events = [_event("click", post["postId"]) for _ in range(101)]
    r = user.client.behavior_events(events)
    assert r.status_code == 400
    assert "行为事件数量" in r.json()["message"]


def test_malformed_json_param_error(anon):
    r = anon.post("/api/v2/behavior/events", data="{not json",
                  headers={"Content-Type": "application/json"})
    assert_error(r, 400, 2)


def test_anonymous_batch_accepted(anon):
    marker = unique_marker()
    events = [_event("exposure", 123456789, requestId=unique_key("rq"),
                     position=2, scene="home")]
    r = anon.behavior_events(events, anonymous_id=f"anon-{marker}",
                             session_id=f"sess-{marker}")
    assert r.status_code == 202
    assert r.json()["acceptedCount"] == 1


def test_accepted_events_land_in_clickhouse(user, published_post):
    post = published_post(user.client)
    click_ev = _event("click", post["postId"])
    exposure_ev = _event("exposure", post["postId"],
                         requestId=unique_key("rq"), position=3, scene="home")
    anonymous_marker = f"anon-{unique_marker()}"
    r = user.client.behavior_events([click_ev, exposure_ev],
                                    anonymous_id=anonymous_marker,
                                    session_id="sess-ch")
    body = r.json()
    assert body["acceptedCount"] == 2
    accepted_ids = [res["clientEventId"] for res in body["results"]
                    if res["accepted"]]
    assert len(accepted_ids) == 2

    id_list = ",".join(f"'{cid}'" for cid in accepted_ids)
    query = (f"SELECT client_event_id, anonymous_id, session_id, action, "
             f"target_id, position FROM xbh_analytics.behavior_events "
             f"WHERE client_event_id IN ({id_list}) FORMAT TSV")

    rows = []
    deadline = time.monotonic() + 120
    last_error = None
    while time.monotonic() < deadline:
        try:
            out = clickhouse(query)
            rows = [line for line in out.splitlines() if line.strip()]
            if len(rows) >= 2:
                break
        except (DbUnavailable, RuntimeError) as exc:
            last_error = exc
            pytest.skip(f"ClickHouse probe unavailable: {exc}")
        time.sleep(3)
    assert len(rows) >= 2, f"behavior events did not land in ClickHouse: {rows} {last_error}"

    fields = {}
    for line in rows:
        cols = line.split("\t")
        fields[cols[0]] = cols
    click_row = fields.get(click_ev["clientEventId"])
    exposure_row = fields.get(exposure_ev["clientEventId"])
    assert click_row is not None and exposure_row is not None
    assert click_row[1] == anonymous_marker
    assert click_row[3] == "click"
    assert int(click_row[4]) == post["postId"]
    assert exposure_row[3] == "exposure"
    assert int(exposure_row[5]) == 3
