"""Shared plumbing for the standalone Playwright evidence scripts.

The scripts drive the real Flutter web build on the local stack; they need
requests and playwright in their Python environment (not the pytest suite).
"""
import argparse
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

DEFAULT_BASE = 'http://127.0.0.1:3002'
DEFAULT_CHROMIUM = '/home/dev/.local/bin/chromium'


def parse_args(description):
    """--base (entry URL, no trailing slash), --output dir, --chromium binary."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--base', default=DEFAULT_BASE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chromium', default=DEFAULT_CHROMIUM)
    args = parser.parse_args()
    args.base = args.base.rstrip('/')
    return args


def direct_session():
    """A requests session that ignores proxy env vars (the stack is loopback)."""
    session = requests.Session()
    session.trust_env = False
    return session


def register_user(session, base, prefix, password):
    """Register a fresh user named PREFIX+random; returns (username, response JSON)."""
    username = prefix + uuid.uuid4().hex[:12]
    response = session.post(base + '/api/v1/auth/register', json={
        'username': username, 'password': password}, timeout=30)
    assert response.status_code == 200, response.status_code
    return username, response.json()


@contextmanager
def chromium_browser(chromium):
    """Headless Chromium from an explicit binary, closed on exit."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=chromium, args=['--no-sandbox'])
        try:
            yield browser
        finally:
            browser.close()


def enable_semantics(page):
    """Turn on Flutter's semantics tree so role/label locators can find widgets."""
    placeholder = page.locator('flt-semantics-placeholder')
    placeholder.wait_for(state='attached', timeout=60000)
    placeholder.evaluate('(element) => element.click()')


def stored_tokens(page):
    """The app's token record; Flutter stores it as a JSON string inside JSON."""
    return json.loads(json.loads(page.evaluate("localStorage.getItem('flutter.tokens')")))


def logged_in_context(browser, user, width, scheme):
    """A browser context whose localStorage already holds USER's session."""
    context = browser.new_context(viewport={'width': width, 'height': 900}, color_scheme=scheme)
    now = int(time.time())
    tokens = dict(access_token=user['token'], refresh_token=user.get('refreshToken', ''),
                  access_expire=now + 1800, refresh_expire=now + 86400,
                  refresh_after=now + 1500, session_revision=1)
    context.add_init_script(
        "localStorage.setItem('flutter.tokens', JSON.stringify(JSON.stringify(%s)));"
        " localStorage.setItem('flutter.tokens.session_revision', '1');" % json.dumps(tokens))
    return context
