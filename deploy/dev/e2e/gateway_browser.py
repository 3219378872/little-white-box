"""Verify UI login, real token refresh, content actions and Assistant reconnection.

Requires requests, Playwright and a real local stack. Registers an isolated user,
cleans its post, and writes only non-secret summaries and synthetic screenshots.
"""
import argparse
import json
import re
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


def enable_semantics(page):
    placeholder = page.locator('flt-semantics-placeholder')
    placeholder.wait_for(state='attached', timeout=60000)
    placeholder.evaluate('(element) => element.click()')


def stored_tokens(page):
    return json.loads(json.loads(page.evaluate("localStorage.getItem('flutter.tokens')")))


def run(base, output, chromium):
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    username = 'gateway' + uuid.uuid4().hex[:12]
    password = 'Gateway_test_2026!'
    registered = session.post(base + '/api/v1/auth/register', json={
        'username': username, 'password': password}, timeout=30)
    assert registered.status_code == 200, registered.status_code
    report = {'base': base, 'trigger': 'invalid access token with valid refresh token',
              'uiLogin': False, 'refresh': False, 'contentActions': False,
              'assistantReload': False, 'pageErrors': []}
    post_id = None
    headers = {'Authorization': 'Bearer ' + registered.json()['token']}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=chromium,
                                        args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            page = context.new_page()
            page.on('pageerror', lambda e: report['pageErrors'].append(type(e).__name__))
            page.goto(base + '/#/auth/login')
            enable_semantics(page)
            page.get_by_role('textbox', name='用户名', exact=True).fill(username)
            # Flutter replaces its password input when focus changes. Keyboard events
            # after focusing reach the controller; a one-shot DOM fill can be lost.
            password_field = page.get_by_label('密码', exact=True)
            password_field.click()
            password_field.press_sequentially(password, delay=35)
            with page.expect_response(lambda r: r.url.endswith('/api/v1/auth/login')) as login:
                page.get_by_role('button', name='登录', exact=True).click()
            assert login.value.status == 200, login.value.status
            page.wait_for_function("localStorage.getItem('flutter.tokens') !== null")
            report['uiLogin'] = True
            # A real gateway rejection exercises the application's refresh/retry path.
            # Refresh credentials remain untouched and are never written to artifacts.
            page.evaluate("""() => {
                const t = JSON.parse(JSON.parse(localStorage.getItem('flutter.tokens')));
                const parts = t.access_token.split('.');
                parts[2] = 'invalid-migration-signature';
                t.access_token = parts.join('.');
                localStorage.setItem('flutter.tokens', JSON.stringify(JSON.stringify(t)));
            }""")
            with page.expect_response(lambda r: r.url.endswith('/api/v1/auth/refresh'), timeout=60000) as refresh:
                page.goto(base + '/#/profile')
                page.reload()
                enable_semantics(page)
            assert refresh.value.status == 200, refresh.value.status
            page.wait_for_function("!JSON.parse(JSON.parse(localStorage.getItem('flutter.tokens'))).access_token.endsWith('.invalid-migration-signature')")
            report['refresh'] = True
            headers = {'Authorization': 'Bearer ' + stored_tokens(page)['access_token']}
            created = session.post(base + '/api/v2/post', headers=headers, json={
                'title': 'CloudWeGo browser migration check', 'content': 'Synthetic migration validation.',
                'status': 1, 'idempotencyKey': uuid.uuid4().hex}, timeout=30)
            assert created.status_code == 200, created.status_code
            post_id = created.json()['postId']
            page.goto(base + '/#/post/' + str(post_id))
            page.reload()
            enable_semantics(page)
            for name, suffix in [('点赞', '/like'), ('收藏', '/favorite')]:
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(suffix), timeout=30000) as action:
                    page.get_by_role('button', name=re.compile('^' + name + ' ')).click()
                assert action.value.status == 200, (name, action.value.status)
            detail = session.get(base + '/api/v1/post/' + str(post_id), headers=headers, timeout=30)
            assert detail.status_code == 200
            assert detail.json()['isLiked'] and detail.json()['isFavorited']
            report['contentActions'] = True
            page.screenshot(path=output / 'gateway-content.png')
            page.goto(base + '/#/messages/assistant')
            page.reload()
            enable_semantics(page)
            message_field = page.get_by_role('textbox', name=re.compile('消息|输入消息'))
            message_field.click()
            message_field.press_sequentially('请只回复：迁移验证完成。', delay=35)
            page.get_by_role('button', name='发送', exact=True).click()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/v2/assistant/messages'), timeout=60000) as posted:
                page.get_by_role('button', name='同意并启用', exact=True).click()
            assert posted.value.status == 200, posted.value.status
            run_id = json.loads(posted.value.text())['runId']
            events_path = f'/api/v2/assistant/runs/{run_id}/events'
            # Reload during the persistent run; subscription must reopen successfully.
            with page.expect_response(lambda r: events_path in r.url, timeout=60000) as reopened:
                page.reload()
                enable_semantics(page)
            assert reopened.value.status == 200, reopened.value.status
            assert reopened.value.headers.get('content-type', '').startswith('text/event-stream')
            report['assistantReload'] = True
            report['runId'] = str(run_id)
            page.get_by_text('请只回复：迁移验证完成。', exact=True).wait_for(timeout=30000)
            page.screenshot(path=output / 'gateway-assistant.png')
            assert not report['pageErrors'], report['pageErrors']
            context.close()
            browser.close()
    finally:
        if post_id:
            detail = session.get(base + '/api/v1/post/' + str(post_id), headers=headers, timeout=30)
            if detail.status_code == 200:
                deleted = session.delete(base + '/api/v2/post/' + str(post_id), headers=headers,
                                         json={'expectedRevision': detail.json()['revision']}, timeout=30)
                report['postCleanup'] = deleted.status_code == 200
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    assert report.get('postCleanup'), 'post cleanup failed'
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:3002')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chromium', default='/home/dev/.local/bin/chromium')
    args = parser.parse_args()
    run(args.base.rstrip('/'), args.output, args.chromium)
