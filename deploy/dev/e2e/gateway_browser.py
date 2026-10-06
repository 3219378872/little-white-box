"""Verify UI login, real token refresh, content actions and Assistant reconnection.

Requires requests, Playwright and a real local stack. Registers an isolated user,
cleans its post, and writes only non-secret summaries and synthetic screenshots.
"""
import json
import re
import uuid

from browser_support import (chromium_browser, direct_session, enable_semantics,
                             parse_args, register_user, stored_tokens)

PASSWORD = 'Gateway_test_2026!'
ASSISTANT_PROMPT = '请只回复：迁移验证完成。'


def login_through_ui(page, base, username):
    """Log in with the real form and wait for the stored session."""
    page.goto(base + '/#/auth/login')
    enable_semantics(page)
    page.get_by_role('textbox', name='用户名', exact=True).fill(username)
    # Flutter replaces its password input when focus changes. Keyboard events
    # after focusing reach the controller; a one-shot DOM fill can be lost.
    password_field = page.get_by_label('密码', exact=True)
    password_field.click()
    password_field.press_sequentially(PASSWORD, delay=35)
    with page.expect_response(lambda r: r.url.endswith('/api/v1/auth/login')) as login:
        page.get_by_role('button', name='登录', exact=True).click()
    assert login.value.status == 200, login.value.status
    page.wait_for_function("localStorage.getItem('flutter.tokens') !== null")


def force_token_refresh(page, base):
    """Corrupt the access token so the gateway rejects it and the app refreshes.

    A real gateway rejection exercises the application's refresh/retry path.
    Refresh credentials remain untouched and are never written to artifacts.
    """
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


def verify_content_actions(page, session, base, headers, post_id):
    """Like and favorite through the UI, then confirm both via the API."""
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


def verify_assistant_reload(page, base):
    """Start an Assistant run, reload mid-run, and expect the SSE to reopen.

    Returns the run id.
    """
    page.goto(base + '/#/messages/assistant')
    page.reload()
    enable_semantics(page)
    message_field = page.get_by_role('textbox', name=re.compile('消息|输入消息'))
    message_field.click()
    message_field.press_sequentially(ASSISTANT_PROMPT, delay=35)
    page.get_by_role('button', name='发送', exact=True).click()
    # The first send asks for agent consent; accepting it posts the message.
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
    return run_id


def delete_post(session, base, headers, post_id):
    """Delete the synthetic post at its current revision; True on success."""
    detail = session.get(base + '/api/v1/post/' + str(post_id), headers=headers, timeout=30)
    if detail.status_code != 200:
        return None
    deleted = session.delete(base + '/api/v2/post/' + str(post_id), headers=headers,
                             json={'expectedRevision': detail.json()['revision']}, timeout=30)
    return deleted.status_code == 200


def run(base, output, chromium):
    output.mkdir(parents=True, exist_ok=True)
    session = direct_session()
    username, registered = register_user(session, base, 'gateway', PASSWORD)
    report = {'base': base, 'trigger': 'invalid access token with valid refresh token',
              'uiLogin': False, 'refresh': False, 'contentActions': False,
              'assistantReload': False, 'pageErrors': []}
    post_id = None
    headers = {'Authorization': 'Bearer ' + registered['token']}
    try:
        with chromium_browser(chromium) as browser:
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            page = context.new_page()
            page.on('pageerror', lambda e: report['pageErrors'].append(type(e).__name__))

            login_through_ui(page, base, username)
            report['uiLogin'] = True

            force_token_refresh(page, base)
            report['refresh'] = True
            # Continue API calls with the refreshed token.
            headers = {'Authorization': 'Bearer ' + stored_tokens(page)['access_token']}

            created = session.post(base + '/api/v2/post', headers=headers, json={
                'title': 'CloudWeGo browser migration check', 'content': 'Synthetic migration validation.',
                'status': 1, 'idempotencyKey': uuid.uuid4().hex}, timeout=30)
            assert created.status_code == 200, created.status_code
            post_id = created.json()['postId']
            verify_content_actions(page, session, base, headers, post_id)
            report['contentActions'] = True
            page.screenshot(path=output / 'gateway-content.png')

            report['runId'] = str(verify_assistant_reload(page, base))
            report['assistantReload'] = True
            page.get_by_text(ASSISTANT_PROMPT, exact=True).wait_for(timeout=30000)
            page.screenshot(path=output / 'gateway-assistant.png')
            assert not report['pageErrors'], report['pageErrors']
            context.close()
    finally:
        # Always remove the synthetic post and persist the (token-free) summary.
        if post_id:
            cleaned = delete_post(session, base, headers, post_id)
            if cleaned is not None:
                report['postCleanup'] = cleaned
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    assert report.get('postCleanup'), 'post cleanup failed'
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    args = parse_args(__doc__)
    run(args.base, args.output, args.chromium)
