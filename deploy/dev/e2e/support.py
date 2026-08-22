import os
import uuid

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3002").rstrip("/")
ADMIN_USERNAME = os.environ.get("E2E_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "123456")
RUN_ID = os.environ.get("E2E_RUN_ID") or uuid.uuid4().hex[:10]
DEFAULT_PASSWORD = "Passw0rd!123"

_seq = 0


def unique_username():
    global _seq
    _seq += 1
    return f"e2e{RUN_ID}{_seq:03d}"


def unique_marker():
    global _seq
    _seq += 1
    return f"mk{RUN_ID}{_seq:03d}"


def unique_key(prefix="k"):
    global _seq
    _seq += 1
    return f"{prefix}{RUN_ID}{_seq:03d}"


class User:
    def __init__(self, client, user_id, username, password, refresh_token=""):
        self.client = client
        self.id = user_id
        self.username = username
        self.password = password
        self.refresh_token = refresh_token
