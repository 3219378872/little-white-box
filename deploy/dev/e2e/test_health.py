from api_client import error_of


def test_health_ok(anon):
    r = anon.health()
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_dependencies_consistent(anon):
    r = anon.health_ready()
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ready", "degraded", "unavailable"}
    deps = body["dependencies"]
    assert isinstance(deps, dict) and deps
    assert all(v in {"ok", "down"} for v in deps.values())
    if any(v == "down" for v in deps.values()):
        assert body["status"] in {"degraded", "unavailable"}
    else:
        assert body["status"] == "ready"


def test_trace_id_echoed(anon):
    r = anon.health()
    assert r.headers.get("X-Trace-Id") == r.sent_trace_id


def test_unknown_route_plain_404(anon):
    r = anon.get("/api/v9/not-a-route")
    assert r.status_code == 404
    assert error_of(r) is None


def test_cors_preflight_allowed(anon):
    r = anon.request("OPTIONS", "/api/v2/search", headers={
        "Origin": "http://e2e.example",
        "Access-Control-Request-Method": "GET",
    })
    assert r.status_code in {200, 204}
