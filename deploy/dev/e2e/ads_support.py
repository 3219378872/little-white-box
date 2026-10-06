"""Helpers for the ad review and sponsored ads black-box suite.

E2E reviewers are scoped to the demo market ID only, every e2e advertiser and
ad carries an "E2E" prefix, and ads expire after two hours so the serving
index does not accumulate across runs. Reviewer roles are granted through the
same ops path as humans use (backend rolectl, RVW-050).
"""
import atexit
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from poll import eventually

MARKET = "ID"
LANGUAGE = "id"
E2E_PREFIX = "E2E"
BACKEND = Path(os.environ.get("BACKEND") or Path(__file__).resolve().parents[3] / "little-white-box-content-community")
_ROLECTL = None


def rolectl(*args):
    global _ROLECTL
    if _ROLECTL is None:
        if not os.environ.get("DB_REVIEW"):
            pytest.skip("DB_REVIEW is not loaded; run through `just e2e`")
        binary = Path(tempfile.gettempdir()) / f"xbh-e2e-rolectl-{os.getpid()}"
        # The ~15MB build is per run; remove it at exit instead of leaving it in /tmp.
        atexit.register(binary.unlink, missing_ok=True)
        build = subprocess.run(["go", "build", "-o", str(binary), "./app/review/rolectl"], cwd=BACKEND,
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, f"rolectl build failed in {BACKEND}: {build.stderr[-300:]}"
        _ROLECTL = binary
    result = subprocess.run([str(_ROLECTL), *args], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"rolectl {args[0]} failed: {result.stderr[-300:]}"


def grant(user, roles):
    rolectl("grant", "-user", str(user.id), "-roles", roles, "-markets", MARKET, "-languages", LANGUAGE)


def revoke(user):
    rolectl("revoke", "-user", str(user.id))


def key(prefix="ad"):
    # Random per call (not the per-run sequence of support.unique_key), so a key
    # never depends on how many other helpers ran before it.
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def expiry_ms(hours=2):
    return int((time.time() + hours * 3600) * 1000)


def ad_payload(title, **overrides):
    payload = {
        "title": f"{E2E_PREFIX} {title}", "body": "Kopi segar dipanggang setiap minggu", "cta": "Beli",
        "landingUrl": "https://kopi.example-shop.com/promo", "market": MARKET, "industry": "GENERAL",
        "startMs": 0, "endMs": expiry_ms(), "mediaIds": [], "idempotencyKey": key(),
    }
    payload.update(overrides)
    return payload


def is_e2e_task(task):
    snapshot = task.get("snapshotJson") or ""
    return f'"{E2E_PREFIX} ' in snapshot or f'"name":"{E2E_PREFIX}' in snapshot


def claim_matching(reviewer, predicate, purpose="", timeout=90):
    """Claim until a task matching predicate is held.

    Leftover e2e tasks ahead in the queue are closed out; any non-e2e task
    ahead of ours is released and the test skipped, so the suite never
    decides somebody else's review.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = reviewer.client.post("/api/v2/review/tasks/claim", json={"purpose": purpose})
        assert r.status_code == 200, f"claim failed: {r.status_code} {r.text[:200]}"
        body = r.json()
        if not body.get("found"):
            time.sleep(2)
            continue
        task = body["task"]
        if predicate(task):
            return task
        generation = task["leaseGeneration"]
        if not is_e2e_task(task):
            reviewer.client.post(f"/api/v2/review/tasks/{task['taskId']}/release", json={"leaseGeneration": generation})
            pytest.skip("review queue for the e2e market has a non-e2e task ahead of this run")
        verdict = {"verdict": "approve"} if task["bizType"] == "advertiser_qualification" else \
            {"verdict": "reject", "policyCodes": ["FORMAT.FUNCTIONALITY"]}
        decide(reviewer, task, **verdict)
    raise AssertionError("expected review task was not claimable in time")


def decide(reviewer, task, verdict, policy_codes=None, **extra):
    body = {"leaseGeneration": task["leaseGeneration"], "verdict": verdict, "policyCodes": policy_codes or [],
            "idempotencyKey": key("decision")}
    body.update(extra)
    return reviewer.client.post(f"/api/v2/review/tasks/{task['taskId']}/decision", json=body)


def wait_ad(owner, ad_id, predicate, desc, timeout=90):
    def check():
        r = owner.client.get(f"/api/v2/ads/{ad_id}")
        if r.status_code != 200:
            return None
        ad = r.json()["ad"]
        return ad if predicate(ad) else None
    return eventually(check, desc=desc, timeout=timeout)


def recommend(client, session_id, ad_slots=True, request_id=None, market=MARKET):
    """One anonymous recommend page in MARKET, optionally with a sponsored slot."""
    params = {"anonymousId": f"anon-{session_id}", "sessionId": session_id,
              "pageSize": 20, "market": market}
    if ad_slots:
        params["adSlots"] = 1
    r = client.recommend(request_id=request_id or key("req"), **params)
    assert r.status_code == 200, f"recommend failed: {r.status_code} {r.text[:200]}"
    return r.json()
