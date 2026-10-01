"""Black-box checks for the ad review platform and sponsored ads (W1～W6).

Covers SPEC-review-platform RVW-A01/A03/A05 and SPEC-sponsored-ads
ADS-A01/A02/A04/A06/A07/A08 plus reports and appeals (ADS-014/026/030) against
the real stack. Machine auto-pass and QA sampling need the embedding Router
with seeds plus a non-stub Ranker, so they stay at the unit/integration level.
"""
import base64
import socket
import uuid

import pytest
import requests

from ads_support import (E2E_PREFIX, MARKET, ad_payload, claim_matching, decide, grant, key, recommend, revoke,
                         wait_ad)
from api_client import assert_error
from dbprobe import DbUnavailable, mysql
from poll import eventually
from support import BASE_URL

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def unique_png():
    # Trailing bytes after IEND keep a valid PNG signature while making the content hash unique per run.
    return PNG_1X1 + uuid.uuid4().bytes


@pytest.fixture(scope="module")
def reviewers(make_user):
    qual, first, second, admin = make_user(), make_user(), make_user(), make_user()
    grant(qual, "qualification_reviewer")
    grant(first, "reviewer,qa")
    grant(second, "reviewer,qa")
    grant(admin, "policy_admin,reviewer")
    return {"qual": qual, "first": first, "second": second, "admin": admin}


@pytest.fixture(scope="module")
def advertiser(make_user, reviewers):
    owner = make_user()
    r = owner.client.put("/api/v2/ads/advertiser", json={
        "name": f"{E2E_PREFIX} Adv {owner.username}", "markets": [MARKET], "expectedRevision": 0,
        "idempotencyKey": key("adv")})
    assert r.status_code == 200, r.text[:200]
    advertiser_id = r.json()["advertiser"]["advertiserId"]
    task = claim_matching(reviewers["qual"], lambda t: t["bizType"] == "advertiser_qualification"
                          and t["objectId"] == advertiser_id)
    assert decide(reviewers["qual"], task, "approve").status_code == 200

    def approved():
        body = owner.client.get("/api/v2/ads/advertiser").json()
        return body["advertiser"] if body["advertiser"]["approvedRevision"] >= 1 else None
    eventually(approved, desc="advertiser approval applied", timeout=90)
    return owner


def create_ad(owner, title, **overrides):
    r = owner.client.post("/api/v2/ads", json=ad_payload(title, **overrides))
    assert r.status_code == 200, f"create ad failed: {r.status_code} {r.text[:200]}"
    return r.json()["ad"]


def approve_ad(reviewer, ad_id, revision):
    task = claim_matching(reviewer, lambda t: t["bizType"] == "ad_creative" and t["objectId"] == ad_id
                          and t["objectRevision"] == revision)
    r = decide(reviewer, task, "approve")
    assert r.status_code == 200, r.text[:200]
    return task


def sponsored_ids(page):
    return {slot["ad"]["adId"]: slot["ad"] for slot in page.get("sponsored", [])}


class Viewer:
    """Fetches pages for one anonymous session, hides every other ad (ADS-026 is per session) and counts
    each delivery of the ad under test, so frequency assertions see every page the session received."""

    def __init__(self, anon, ad_id):
        self.anon, self.ad_id, self.session, self.deliveries = anon, ad_id, key("sess"), 0

    def fetch(self):
        served = sponsored_ids(recommend(self.anon, self.session))
        for other in [ad_id for ad_id in served if ad_id != self.ad_id]:
            r = self.anon.post(f"/api/v2/ads/{other}/hide", json={"sessionId": self.session})
            assert r.status_code == 200, r.text[:200]
        ours = served.get(self.ad_id)
        if ours:
            self.deliveries += 1
        return ours


def test_advertiser_cannot_skip_review(anon, make_user):
    owner = make_user()
    assert_error(owner.client.post("/api/v2/ads", json=ad_payload("no advertiser")), 403, 7101)
    assert_error(anon.get("/api/v2/ads/advertiser"), 401, 1006)


# ADS-A06 / ADS-016：非 https、含用户信息或私有主机的落地页被拒绝。
def test_landing_url_rules(advertiser):
    for url in ("http://shop.example-shop.com/", "https://user:pw@shop.example-shop.com/",
                "https://10.0.0.8/promo", "https://intranet.local/"):
        assert_error(advertiser.client.post("/api/v2/ads", json=ad_payload("bad url", landingUrl=url)), 400, 7104)


# ADS-A08：年龄限制行业在所有市场被硬规则拒绝并带政策码；缺少资质的受监管行业不能送审。
def test_age_restricted_and_regulated_industries(advertiser):
    assert_error(advertiser.client.post("/api/v2/ads", json=ad_payload("loan", industry="FINANCIAL")), 400, 7103)
    for industry in ("ALCOHOL", "GAMBLING"):
        ad = create_ad(advertiser, f"{industry.lower()} promo", industry=industry)
        rejected = wait_ad(advertiser, ad["adId"], lambda a: a["reviewStatus"] == "rejected",
                           desc=f"{industry} hard-rule rejection")
        assert f"INDUSTRY.{industry}" in rejected["policyCodes"]
        assert rejected["servingStatus"] == "none"


# ADS-A01 / ADS-A02 / ADS-A04 / ADS-A06：审核通过、素材发布、推荐流投放、编辑期间投旧快照、频控与隐藏。
def test_approved_ad_serves_from_snapshot_with_caps(anon, advertiser, reviewers):
    content = unique_png()
    upload = advertiser.client.post("/api/v2/ads/assets/creative",
                                    files={"file": ("creative.png", content, "image/png")},
                                    data={"idempotencyKey": key("asset")})
    assert upload.status_code == 200, upload.text[:200]
    asset = upload.json()
    public_url = f"{BASE_URL}/xbh-media/ads/{asset['sha256']}"
    assert requests.get(public_url, timeout=10).status_code in (403, 404), "unapproved creative must not be public"
    try:
        private = requests.get(f"http://127.0.0.1:8333/xbh-ad-private/assets/{asset['assetId']}", timeout=5)
        assert private.status_code in (401, 403, 404), "private bucket must not allow anonymous reads"
    except requests.ConnectionError:
        pass
    own = advertiser.client.get(f"/api/v2/ads/assets/{asset['assetId']}")
    assert own.status_code == 200 and base64.b64decode(own.json()["contentBase64"]) == content

    ad = create_ad(advertiser, "kopi v1", mediaIds=[asset["assetId"]])
    assert ad["reviewStatus"] == "pending_review" and ad["approved"] is None
    approve_ad(reviewers["first"], ad["adId"], 1)
    serving = wait_ad(advertiser, ad["adId"], lambda a: a["eligible"] and a["approvedRevision"] == 1,
                      desc="approved ad becomes eligible")
    assert serving["servingStatus"] == "serving"
    assert serving["approved"]["media"][0]["publicUrl"].endswith(asset["sha256"])
    assert requests.get(public_url, timeout=10).status_code == 200

    # ADS-A01：未声明能力的请求不出现 sponsored；声明后获得带标识的槽位。
    assert "sponsored" not in recommend(anon, key("sess"), ad_slots=False)
    viewer = Viewer(anon, ad["adId"])
    slot_ad = eventually(viewer.fetch, desc="sponsored slot served", timeout=60, interval=1)
    assert slot_ad["disclosure"] == "sponsored" and slot_ad["revision"] == 1
    assert slot_ad["why"] == {"market": MARKET, "scene": "home", "personalized": False}
    assert slot_ad["advertiserName"].startswith(E2E_PREFIX)

    # ADS-A02：编辑后审核期间继续投放 revision 1。
    edited = advertiser.client.put(f"/api/v2/ads/{ad['adId']}", json={
        **ad_payload("kopi v2", mediaIds=[asset["assetId"]]), "expectedRevision": 1})
    assert edited.status_code == 200, edited.text[:200]
    assert edited.json()["ad"]["approvedRevision"] == 1
    stale = advertiser.client.put(f"/api/v2/ads/{ad['adId']}", json={**ad_payload("kopi v2b"), "expectedRevision": 1})
    assert_error(stale, 409, 2007)

    # ADS-A04：同一会话每个 UTC 自然日最多 3 次；其他会话不受影响。
    for _ in range(8):
        served = viewer.fetch()
        if served:
            assert served["revision"] == 1, "pending revision must not serve"
    assert viewer.deliveries == 3, f"expected exactly 3 deliveries for one session, got {viewer.deliveries}"
    assert eventually(Viewer(anon, ad["adId"]).fetch, desc="another session still gets the ad", timeout=60, interval=1)

    # ADS-026：隐藏后当前会话不再投放。
    hidden = Viewer(anon, ad["adId"])
    assert anon.post(f"/api/v2/ads/{ad['adId']}/hide", json={"sessionId": hidden.session}).status_code == 200
    for _ in range(3):
        assert hidden.fetch() is None

    # 新版本通过后切换到 revision 2。
    approve_ad(reviewers["first"], ad["adId"], 2)
    wait_ad(advertiser, ad["adId"], lambda a: a["approvedRevision"] == 2, desc="revision 2 applied")
    switched = Viewer(anon, ad["adId"])

    def revision_two():
        served = switched.fetch()
        if served and served["revision"] != 2:
            switched.session, switched.deliveries = key("sess"), 0
            return None
        return served
    assert eventually(revision_two, desc="serving switches to revision 2", timeout=90, interval=1)["title"].endswith("kopi v2")


# RVW-A01：审核中修改使旧任务作废，旧持有者提交返回「任务已作废」。
def test_new_revision_supersedes_held_task(advertiser, reviewers):
    ad = create_ad(advertiser, "supersede v1")
    task = claim_matching(reviewers["first"], lambda t: t["objectId"] == ad["adId"] and t["objectRevision"] == 1)
    edited = advertiser.client.put(f"/api/v2/ads/{ad['adId']}", json={**ad_payload("supersede v2"), "expectedRevision": 1})
    assert edited.status_code == 200

    # 作废发生在审核平台收到新 revision 的送审之时（outbox → MQ → worker），先等它可见。
    def superseded():
        r = reviewers["first"].client.get(f"/api/v2/review/tasks/{task['taskId']}")
        return r.status_code == 200 and r.json()["task"]["status"] == "superseded"
    eventually(superseded, desc="old task superseded", timeout=60)
    assert_error(decide(reviewers["first"], task, "approve"), 410, 7002)
    assert_error(reviewers["first"].client.post(f"/api/v2/review/tasks/{task['taskId']}/renew",
                                                json={"leaseGeneration": task["leaseGeneration"]}), 410, 7002)
    try:
        status = mysql("xbh_review", f"SELECT status FROM review_task WHERE id = {int(task['taskId'])}").strip()
        assert status == "superseded"
    except DbUnavailable:
        pass
    approve_ad(reviewers["first"], ad["adId"], 2)
    wait_ad(advertiser, ad["adId"], lambda a: a["approvedRevision"] == 2, desc="new revision approved")


# RVW-A03：持有被接手后，旧持有者的续期、放弃与提交都被拒绝。
def test_lease_fencing_between_reviewers(advertiser, reviewers):
    first, second = reviewers["first"], reviewers["second"]
    ad = create_ad(advertiser, "fencing")
    task = claim_matching(first, lambda t: t["objectId"] == ad["adId"])
    renewed = first.client.post(f"/api/v2/review/tasks/{task['taskId']}/renew",
                                json={"leaseGeneration": task["leaseGeneration"]})
    assert renewed.status_code == 200
    assert first.client.post(f"/api/v2/review/tasks/{task['taskId']}/release",
                             json={"leaseGeneration": task["leaseGeneration"]}).status_code == 200
    taken = claim_matching(second, lambda t: t["taskId"] == task["taskId"])
    assert taken["leaseGeneration"] == task["leaseGeneration"] + 1
    for action in ("renew", "release"):
        assert_error(first.client.post(f"/api/v2/review/tasks/{task['taskId']}/{action}",
                                       json={"leaseGeneration": task["leaseGeneration"]}), 409, 7001)
    assert_error(decide(first, task, "approve"), 409, 7001)
    rejected = decide(second, taken, "reject", ["MISLEADING.CLAIM"], nominateSeed=True)
    assert rejected.status_code == 200 and rejected.json()["policyCodes"] == ["MISLEADING.CLAIM"]
    assert_error(decide(second, taken, "approve"), 409, 7004)
    wait_ad(advertiser, ad["adId"], lambda a: a["reviewStatus"] == "rejected", desc="rejection applied")

    # RVW-030：候选种子需要不同于提名人的政策管理员确认。
    admin = reviewers["admin"]
    seeds = admin.client.get("/api/v2/review/seeds", params={"status": "candidate", "limit": 200})
    assert seeds.status_code == 200
    seed = next(s for s in seeds.json()["seeds"] if s["sourceTaskId"] == task["taskId"])
    assert_error(second.client.post(f"/api/v2/review/seeds/{seed['seedId']}/confirm"), 403, 7003)
    confirmed = admin.client.post(f"/api/v2/review/seeds/{seed['seedId']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()["seed"]["status"] == "active"
    assert admin.client.post(f"/api/v2/review/seeds/{seed['seedId']}/retire").json()["seed"]["status"] == "retired"


# RVW-A05 / RVW-050：撤销角色对下一次请求立即生效；非审核员看不到工作台。
def test_role_revocation_is_immediate(make_user):
    reviewer = make_user()
    assert reviewer.client.get("/api/v2/review/me").json()["active"] is False
    assert_error(reviewer.client.post("/api/v2/review/tasks/claim", json={}), 403, 7003)
    grant(reviewer, "reviewer")
    profile = reviewer.client.get("/api/v2/review/me").json()
    assert profile["active"] and profile["markets"] == [MARKET]
    assert reviewer.client.post("/api/v2/review/tasks/claim", json={}).status_code == 200
    revoke(reviewer)
    assert_error(reviewer.client.post("/api/v2/review/tasks/claim", json={}), 403, 7003)


def _port_open(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# RVW-013：精排分数达到允许自动拒绝的阈值时直接拒绝（需 `just infer-up` 启动精排占位 sidecar）。
def test_ranker_fixture_auto_rejects(advertiser):
    if not _port_open(9026):
        pytest.skip("moderation-infer is not running; start it with `just infer-up`")
    ad = create_ad(advertiser, "ranker", body="Konten uji [[fixture:CONTENT.SELF_HARM=0.97]]")
    rejected = wait_ad(advertiser, ad["adId"], lambda a: a["reviewStatus"] == "rejected", desc="ranker auto reject")
    assert rejected["policyCodes"] == ["CONTENT.SELF_HARM"]


def serving_ad(advertiser, reviewer, title, **overrides):
    ad = create_ad(advertiser, title, **overrides)
    approve_ad(reviewer, ad["adId"], 1)
    return wait_ad(advertiser, ad["adId"], lambda a: a["eligible"], desc=f"{title} becomes eligible")


def served_to_new_session(anon, ad_id, timeout=60):
    return eventually(Viewer(anon, ad_id).fetch, desc="ad served to a fresh session", timeout=timeout, interval=1)


# ADS-030 / ADS-026 / ADS-014 / RVW-024：举报对举报人隐藏并生成复审任务；举报成立下线；
# 被下线的 revision 可申诉一次，复审由不同审核员作出且为最终结论。
def test_report_takes_ad_offline_and_appeal_restores_it(anon, advertiser, reviewers):
    first, second = reviewers["first"], reviewers["second"]
    ad = serving_ad(advertiser, first, "reported kopi")
    assert ad["appealable"] is False
    served_to_new_session(anon, ad["adId"])

    reporter = Viewer(anon, ad["adId"])
    assert_error(anon.post(f"/api/v2/ads/{ad['adId']}/report", json={"sessionId": reporter.session, "reason": "boring"}),
                 400, 2)
    assert_error(anon.post(f"/api/v2/ads/{ad['adId']}/report", json={"reason": "scam"}), 400, 2)
    r = anon.post(f"/api/v2/ads/{ad['adId']}/report", json={"sessionId": reporter.session, "reason": "scam"})
    assert r.status_code == 200 and r.json()["counted"] is True, r.text[:200]
    again = anon.post(f"/api/v2/ads/{ad['adId']}/report", json={"sessionId": reporter.session, "reason": "other"})
    assert again.status_code == 200 and again.json()["counted"] is False
    for _ in range(3):
        assert reporter.fetch() is None, "reported ad must not be served to the reporter's session"
    other = anon.post(f"/api/v2/ads/{ad['adId']}/report", json={"sessionId": key("sess"), "reason": "misleading"})
    assert other.status_code == 200 and other.json()["counted"] is True

    task = claim_matching(first, lambda t: t["objectId"] == ad["adId"] and t["purpose"] == "report", purpose="report")
    assert task["objectRevision"] == 1

    # 第二条举报经 outbox → MQ 异步送审，可能晚于领取到达；同批次只提高已有任务的优先级。
    def raised():
        r = first.client.get(f"/api/v2/review/tasks/{task['taskId']}")
        return r.status_code == 200 and r.json()["task"]["priority"] >= 60
    eventually(raised, desc="second report raises the task priority", timeout=60)
    assert decide(first, task, "reject", ["CONTENT.DECEPTIVE"]).status_code == 200
    offline = wait_ad(advertiser, ad["adId"], lambda a: a["servingStatus"] == "offline", desc="report upheld")
    assert offline["pauseReason"] == "report" and offline["policyCodes"] == ["CONTENT.DECEPTIVE"]
    assert offline["appealable"] is True and not offline["eligible"]

    appealed = advertiser.client.post(f"/api/v2/ads/{ad['adId']}/appeal", json={"idempotencyKey": key("appeal")})
    assert appealed.status_code == 200, appealed.text[:200]
    assert appealed.json()["ad"]["reviewStatus"] == "appealing"
    assert_error(advertiser.client.post(f"/api/v2/ads/{ad['adId']}/appeal", json={}), 409, 7106)

    # 原决策人看不到这条申诉。
    held = first.client.post("/api/v2/review/tasks/claim", json={"purpose": "appeal"}).json()
    if held.get("found"):
        assert held["task"]["objectId"] != ad["adId"], "appeal must exclude the original decider"
        first.client.post(f"/api/v2/review/tasks/{held['task']['taskId']}/release",
                          json={"leaseGeneration": held["task"]["leaseGeneration"]})
    appeal = claim_matching(second, lambda t: t["objectId"] == ad["adId"] and t["purpose"] == "appeal",
                            purpose="appeal")
    assert appeal["originalDecision"]["verdict"] == "reject"
    assert decide(second, appeal, "approve").status_code == 200
    restored = wait_ad(advertiser, ad["adId"], lambda a: a["servingStatus"] == "serving", desc="appeal approved")
    assert restored["reviewStatus"] == "approved" and restored["appealable"] is False
    assert_error(advertiser.client.post(f"/api/v2/ads/{ad['adId']}/appeal", json={}), 409, 7106)
    served_to_new_session(anon, ad["adId"], timeout=90)


# ADS-031 / ADS-A07：生效种子库变化后在投广告按新代次回扫；判定违规先暂停（停止投放）并进入人审，
# 人审确认后下线，否定后恢复投放。需要精排占位 sidecar（`just infer-up`，fixture 标记驱动分数）。
def test_rescan_pauses_violations_until_human_review(anon, advertiser, reviewers):
    if not _port_open(9026):
        pytest.skip("moderation-infer is not running; start it with `just infer-up`")
    first, second, admin = reviewers["first"], reviewers["second"], reviewers["admin"]
    body = "Kopi hemat [[fixture:CONTENT.DECEPTIVE=0.97]]"
    run = key("rescan")

    # 先确认种子：代次在确认后 60 秒生效，此前过审的广告都会回扫。标题各不相同，避免指纹复用直接结案；
    # 召回可用时文案相近仍命中种子，不可用时全部 issue 进入精排。
    seed_ad = create_ad(advertiser, f"{run} seed", body=body)
    seed_task = claim_matching(first, lambda t: t["objectId"] == seed_ad["adId"])
    assert decide(first, seed_task, "reject", ["CONTENT.DECEPTIVE"], nominateSeed=True).status_code == 200
    seeds = admin.client.get("/api/v2/review/seeds", params={"status": "candidate", "limit": 200}).json()["seeds"]
    seed = next(s for s in seeds if s["sourceTaskId"] == seed_task["taskId"])
    assert admin.client.post(f"/api/v2/review/seeds/{seed['seedId']}/confirm").status_code == 200
    try:
        # 初审：DECEPTIVE 0.97 在 ID 市场不允许自动拒绝，落入灰区由人工通过。
        confirmed = serving_ad(advertiser, first, f"{run} a", body=body)
        cleared = serving_ad(advertiser, first, f"{run} b", body=body)
        for ad in (confirmed, cleared):
            paused = wait_ad(advertiser, ad["adId"], lambda a: a["servingStatus"] == "paused",
                             desc="rescan violation pauses serving", timeout=240)
            assert paused["pauseReason"] == "rescan" and "CONTENT.DECEPTIVE" in paused["policyCodes"]
            assert not paused["eligible"]
        watcher = Viewer(anon, confirmed["adId"])
        for _ in range(3):
            assert watcher.fetch() is None, "paused ad must not be served"

        ids = {confirmed["adId"]: ("reject", ["CONTENT.DECEPTIVE"]), cleared["adId"]: ("approve", None)}
        while ids:
            task = claim_matching(second, lambda t: t["purpose"] == "rescan" and t["objectId"] in ids, purpose="rescan")
            assert task["escalationReason"] == "rescan-violation"
            verdict, codes = ids.pop(task["objectId"])
            assert decide(second, task, verdict, codes).status_code == 200
        offline = wait_ad(advertiser, confirmed["adId"], lambda a: a["servingStatus"] == "offline",
                          desc="rescan violation confirmed")
        assert offline["pauseReason"] == "rescan" and offline["appealable"] is True
        wait_ad(advertiser, cleared["adId"], lambda a: a["servingStatus"] == "serving" and a["eligible"],
                desc="rescan violation dismissed")
        served_to_new_session(anon, cleared["adId"], timeout=90)
    finally:
        admin.client.post(f"/api/v2/review/seeds/{seed['seedId']}/retire")
