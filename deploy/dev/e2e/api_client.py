import json
import time
import uuid

import requests


def parse_sse_stream(resp, max_frames=1000):
    frames = []
    buf = b""
    done = False
    for chunk in resp.iter_content(chunk_size=None):
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buf += chunk
        while b"\n\n" in buf and not done:
            raw, buf = buf.split(b"\n\n", 1)
            for line in raw.decode("utf-8", errors="replace").splitlines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[len("data: "):]))
            if frames and frames[-1]["type"] == "done":
                done = True
        if done or len(frames) >= max_frames:
            break
    return frames


def error_of(resp):
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict) and "code" in body and "message" in body:
        return body
    return None


def assert_error(resp, status, code):
    assert resp.status_code == status, \
        f"expected HTTP {status}, got {resp.status_code}: {resp.text[:200]}"
    body = error_of(resp)
    assert body is not None, f"expected error envelope, got: {resp.text[:200]}"
    assert body["code"] == code, \
        f"expected code {code}, got {body['code']} ({body.get('message')})"


class ApiClient:
    def __init__(self, base_url, token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()

    def as_user(self, token):
        return ApiClient(self.base_url, token)

    def request(self, method, path, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        trace = uuid.uuid4().hex
        headers["X-Trace-Id"] = trace
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        url = path if path.startswith("http") else self.base_url + path
        kwargs.setdefault("timeout", 30)
        resp = self.session.request(method, url, headers=headers, **kwargs)
        resp.sent_trace_id = trace
        return resp

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def health(self):
        return self.get("/api/v1/health")

    def health_ready(self):
        return self.get("/api/v1/health/ready")

    def register(self, payload):
        return self.post("/api/v1/auth/register", json=payload)

    def login(self, payload):
        return self.post("/api/v1/auth/login", json=payload)

    def send_verify_code(self, phone, code_type):
        return self.post("/api/v1/auth/verify-code",
                         json={"phone": phone, "type": code_type})

    def refresh(self, refresh_token):
        return self.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token})

    def get_user(self, user_id):
        return self.get(f"/api/v1/user/{user_id}")

    def user_posts(self, user_id, **params):
        return self.get(f"/api/v1/users/{user_id}/posts", params=params)

    def user_favorites(self, user_id, **params):
        return self.get(f"/api/v1/users/{user_id}/favorites", params=params)

    def update_profile(self, payload):
        return self.put("/api/v1/user/profile", json=payload)

    def follow(self, target_user_id):
        return self.post("/api/v1/user/follow", json={"targetUserId": target_user_id})

    def unfollow(self, target_user_id):
        return self.delete("/api/v1/user/follow", json={"targetUserId": target_user_id})

    def get_personalization(self):
        return self.get("/api/v2/me/personalization")

    def set_personalization(self, enabled):
        return self.put("/api/v2/me/personalization", json={"enabled": enabled})

    def post_list(self, **params):
        params.setdefault("pageSize", 20)
        return self.get("/api/v1/posts", params=params)

    def post_detail(self, post_id):
        return self.get(f"/api/v1/post/{post_id}")

    def create_post(self, payload):
        return self.post("/api/v2/post", json=payload)

    def update_post(self, post_id, payload):
        return self.put(f"/api/v2/post/{post_id}", json=payload)

    def delete_post(self, post_id, expected_revision):
        return self.delete(f"/api/v2/post/{post_id}",
                           json={"expectedRevision": expected_revision})

    def comment_list(self, post_id, **params):
        return self.get(f"/api/v1/comments/{post_id}", params=params)

    def comment_create(self, payload):
        return self.post("/api/v1/comment", json=payload)

    def comment_delete(self, comment_id):
        return self.delete(f"/api/v1/comment/{comment_id}")

    def like(self, target_id, target_type):
        return self.post("/api/v1/like",
                         json={"targetId": target_id, "targetType": target_type})

    def unlike(self, target_id, target_type):
        return self.delete("/api/v1/like",
                           json={"targetId": target_id, "targetType": target_type})

    def favorite(self, post_id):
        return self.post("/api/v1/favorite", json={"postId": post_id})

    def unfavorite(self, post_id):
        return self.delete("/api/v1/favorite", json={"postId": post_id})

    def upload_image(self, file_tuple):
        return self.post("/api/v1/media/image", files={"file": file_tuple})

    def behavior_events(self, events, anonymous_id=None, session_id=None,
                        auth=None):
        payload = {"events": events}
        if anonymous_id is not None:
            payload["anonymousId"] = anonymous_id
        if session_id is not None:
            payload["sessionId"] = session_id
        return self.post("/api/v2/behavior/events", json=payload, auth=auth)

    def follow_feed(self, **params):
        params.setdefault("pageSize", 20)
        return self.get("/api/v2/feed/follow", params=params)

    def recommend(self, request_id=None, **params):
        if request_id is not None:
            params["requestId"] = request_id
        return self.get("/api/v2/feed/recommend", params=params)

    def search(self, keyword, **params):
        return self.get("/api/v2/search",
                        params={"keyword": keyword, **params})

    def search_users(self, keyword, **params):
        return self.get("/api/v2/search/users",
                        params={"keyword": keyword, **params})

    def search_tags(self, keyword, **params):
        return self.get("/api/v2/search/tags",
                        params={"keyword": keyword, **params})

    def conversations(self, **params):
        return self.get("/api/v2/messages/conversations", params=params)

    def conversation_messages(self, conversation_id, **params):
        return self.get(f"/api/v2/messages/conversations/{conversation_id}",
                        params=params)

    def send_message(self, payload):
        return self.post("/api/v2/messages", json=payload)

    def mark_conversation_read(self, conversation_id):
        return self.post(f"/api/v2/messages/conversations/{conversation_id}/read")

    def unread_summary(self):
        return self.get("/api/v2/messages/unread")

    def assistant_chat(self, message, stream=False, conversation_id=None):
        payload = {"message": message}
        if conversation_id is not None:
            payload["conversationId"] = conversation_id
        headers = {"Accept": "text/event-stream"}
        return self.post("/api/v2/assistant/chat", json=payload, stream=stream,
                         headers=headers)

    def assistant_chat_stream(self, message, attempts=3):
        last = None
        for attempt in range(attempts):
            last = self.assistant_chat(message, stream=True)
            if last.status_code == 200:
                return last
            if attempt < attempts - 1:
                time.sleep(2)
        return last
