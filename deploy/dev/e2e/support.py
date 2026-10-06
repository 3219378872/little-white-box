"""Shared constants and unique-name helpers for the black-box e2e suite."""
import base64
import os
import socket
import uuid

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3002").rstrip("/")
ADMIN_USERNAME = os.environ.get("E2E_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "123456")
RUN_ID = os.environ.get("E2E_RUN_ID") or uuid.uuid4().hex[:10]
DEFAULT_PASSWORD = "Passw0rd!123"
# Smallest valid PNG; uploads append bytes when they need a unique content hash.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

_seq = 0


def port_open(port, host="127.0.0.1"):
    """True when something accepts TCP connections on HOST:PORT (1s timeout)."""
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


# Names below share one per-run sequence so every generated value is unique
# within the run, and RUN_ID keeps them unique across runs.
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


# A registered test user with its own authenticated client.
class User:
    def __init__(self, client, user_id, username, password, refresh_token=""):
        self.client = client
        self.id = user_id
        self.username = username
        self.password = password
        self.refresh_token = refresh_token
