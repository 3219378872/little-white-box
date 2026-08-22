import pytest

from api_client import assert_error
from support import unique_marker


def test_upload_png_returns_media(user, png_bytes):
    r = user.client.upload_image(("probe.png", png_bytes, "image/png"))
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["mediaId"] > 0
    for field in ("url", "thumbnailUrl"):
        assert "/xbh-media/" in body[field], body[field]
    assert body["thumbnailUrl"] != body["url"]


def test_uploaded_image_fetchable_via_proxy_and_direct(base_url, anon, user,
                                                       png_bytes):
    r = user.client.upload_image(("fetch.png", png_bytes, "image/png"))
    assert r.status_code == 200
    body = r.json()

    path = body["url"].split("/xbh-media/", 1)[1]
    via_proxy = anon.get(f"{base_url}/xbh-media/{path}")
    assert via_proxy.status_code == 200, f"proxy fetch failed: {via_proxy.status_code}"
    assert len(via_proxy.content) > 0

    direct = anon.get(body["url"])
    assert direct.status_code == 200
    assert len(direct.content) > 0


def test_upload_requires_auth(anon, png_bytes):
    r = anon.upload_image(("anon.png", png_bytes, "image/png"))
    assert_error(r, 401, 1006)


def test_upload_rejects_unsupported_type(user):
    marker = unique_marker()
    r = user.client.upload_image((f"notes-{marker}.txt",
                                  "plain text not allowed".encode(),
                                  "text/plain"))
    assert_error(r, 400, 4002)


def test_upload_rejects_oversize(user):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (11 * 1024 * 1024)
    r = user.client.upload_image(("huge.png", big, "image/png"))
    assert r.status_code == 413, f"expected 413 for oversize, got {r.status_code}"


@pytest.mark.parametrize("filename,content_type,magic", [
    ("broken.jpg", "image/jpeg", b"\xff\xd8\xff\xe0"),
    ("broken.webp", "image/webp", b"RIFF"),
])
def test_upload_malformed_image_payload_rejected(user, filename, content_type,
                                                 magic):
    payload = magic + b"\x00" * 256
    r = user.client.upload_image((filename, payload, content_type))
    assert r.status_code >= 400, \
        f"malformed {content_type} payload should be rejected, got {r.status_code}"
