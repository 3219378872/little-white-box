"""Exercise real Flutter file choosers and uploads against the local stack.
Run with a Python environment containing requests and playwright. Output excludes tokens.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


def run(base, output, chromium):
    output.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).with_name('fixtures')
    session = requests.Session()
    session.trust_env = False

    def register():
        response = session.post(base + '/api/v1/auth/register', json={
            'username': 'media' + uuid.uuid4().hex[:12], 'password': 'Media_test_2026!'}, timeout=30)
        assert response.status_code == 200, response.status_code
        return response.json()

    sender, receiver = register(), register()
    headers = {'Authorization': 'Bearer ' + sender['token']}
    initial = session.post(base + '/api/v2/messages', headers=headers, json={
        'receiverId': receiver['userId'], 'content': 'media browser validation',
        'msgType': 1, 'idempotencyKey': uuid.uuid4().hex}, timeout=30)
    assert initial.status_code == 200

    def route(user, target):
        response = session.get(base + '/api/v2/messages/conversations',
                               headers={'Authorization': 'Bearer ' + user['token']}, timeout=30)
        assert response.status_code == 200
        convo = next(c for c in response.json()['conversations'] if c['targetUserId'] == target['userId'])
        return f"{base}/#/messages/{convo['id']}?targetUserId={target['userId']}&targetUserName=MediaTest"

    def login_context(browser, user, width, scheme):
        context = browser.new_context(viewport={'width': width, 'height': 900}, color_scheme=scheme)
        tokens = dict(access_token=user['token'], refresh_token=user.get('refreshToken', ''),
                      access_expire=int(time.time()) + 1800, refresh_expire=int(time.time()) + 86400,
                      refresh_after=int(time.time()) + 1500, session_revision=1)
        script = "localStorage.setItem('flutter.tokens', JSON.stringify(JSON.stringify(%s))); localStorage.setItem('flutter.tokens.session_revision', '1');" % json.dumps(tokens)
        context.add_init_script(script)
        return context

    def enable(page):
        placeholder = page.locator('flt-semantics-placeholder')
        placeholder.wait_for(state='attached', timeout=60000)
        placeholder.evaluate('(element) => element.click()')
        page.get_by_role('button', name='发送视频', exact=True).wait_for(timeout=30000)

    report = {'base': base, 'scenes': []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chromium, args=['--no-sandbox'])
        try:
            for width, scheme in [(320, 'light'), (390, 'dark'), (1440, 'light')]:
                context = login_context(browser, sender, width, scheme)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(route(sender, receiver))
                enable(page)
                uploads = []
                for label, kind, name in [('发送图片','image','image.png'), ('发送视频','video','clip.mp4'), ('发送语音文件','audio','voice.wav')]:
                    with page.expect_file_chooser() as chooser:
                        page.get_by_role('button', name=label, exact=True).click()
                    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/v2/messages'), timeout=60000) as sent:
                        with page.expect_response(lambda r: r.url.endswith('/api/v1/media/' + kind), timeout=60000) as uploaded:
                            chooser.value.set_files(fixtures / name)
                    assert uploaded.value.status == 200, (kind, uploaded.value.status)
                    assert sent.value.status == 200, (kind, sent.value.status)
                    # Parse int64 JSON with Python, avoiding JS rounding in evidence.
                    media = json.loads(uploaded.value.text())
                    uploads.append({'kind': kind, 'mediaId': str(media['mediaId'])})
                    fetched = session.get(media['url'], timeout=30)
                    assert fetched.status_code == 200
                    page.get_by_role('button', name=label, exact=True).wait_for()
                page.reload()
                enable(page)
                page.get_by_role('button', name='打开视频', exact=True).first.wait_for()
                page.get_by_role('button', name='播放语音文件', exact=True).first.wait_for()
                for label in ['打开视频', '播放语音文件']:
                    with page.expect_popup() as opened:
                        page.get_by_role('button', name=label, exact=True).first.click()
                    opened.value.wait_for_load_state()
                    assert '/xbh-media/' in opened.value.url
                    opened.value.close()
                filename = f'media-{width}-{scheme}.png'
                page.screenshot(path=output / filename)
                assert not page.evaluate('document.body.scrollWidth > innerWidth')
                assert not errors, errors
                report['scenes'].append({'width': width, 'scheme': scheme, 'uploads': uploads,
                                         'reloadHistory': True, 'openVideo': True, 'openAudio': True, 'pageErrors': errors, 'screenshot': filename})
                context.close()
            context = login_context(browser, receiver, 390, 'light')
            page = context.new_page()
            page.goto(route(receiver, sender))
            enable(page)
            page.get_by_role('button', name='打开视频', exact=True).first.wait_for()
            page.get_by_role('button', name='播放语音文件', exact=True).first.wait_for()
            page.screenshot(path=output / 'receiver-history.png')
            report['receiverHistory'] = True
            context.close()
        finally:
            browser.close()
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'scenes': len(report['scenes']), 'receiverHistory': report.get('receiverHistory', False)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:3002')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chromium', default='/home/dev/.local/bin/chromium')
    args = parser.parse_args()
    run(args.base, args.output, args.chromium)
