"""Exercise real Flutter file choosers and uploads against the local stack.
Run with a Python environment containing requests and playwright. Output excludes tokens.
"""
import json
import uuid
from pathlib import Path

from browser_support import (chromium_browser, direct_session, enable_semantics,
                             logged_in_context, parse_args, register_user)

FIXTURES = Path(__file__).with_name('fixtures')
PASSWORD = 'Media_test_2026!'
# (button label, media kind, fixture file) for each composer upload.
UPLOADS = [('发送图片', 'image', 'image.png'), ('发送视频', 'video', 'clip.mp4'),
           ('发送语音文件', 'audio', 'voice.wav')]
# Viewports cover narrow mobile, mobile dark and desktop layouts.
SCENES = [(320, 'light'), (390, 'dark'), (1440, 'light')]


def conversation_url(session, base, user, target):
    """Deep link to USER's conversation with TARGET."""
    response = session.get(base + '/api/v2/messages/conversations',
                           headers={'Authorization': 'Bearer ' + user['token']}, timeout=30)
    assert response.status_code == 200
    convo = next(c for c in response.json()['conversations'] if c['targetUserId'] == target['userId'])
    return f"{base}/#/messages/{convo['id']}?targetUserId={target['userId']}&targetUserName=MediaTest"


def open_thread(page, url):
    """Load the thread and wait until the composer's upload buttons exist."""
    page.goto(url)
    enable_semantics(page)
    page.get_by_role('button', name='发送视频', exact=True).wait_for(timeout=30000)


def upload_through_chooser(page, session, label, kind, name):
    """Pick a fixture via the real file chooser; returns the uploaded media record."""
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name=label, exact=True).click()
    # Upload and the follow-up message send must both succeed.
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/v2/messages'), timeout=60000) as sent:
        with page.expect_response(lambda r: r.url.endswith('/api/v1/media/' + kind), timeout=60000) as uploaded:
            chooser.value.set_files(FIXTURES / name)
    assert uploaded.value.status == 200, (kind, uploaded.value.status)
    assert sent.value.status == 200, (kind, sent.value.status)
    # Parse int64 JSON with Python, avoiding JS rounding in evidence.
    media = json.loads(uploaded.value.text())
    fetched = session.get(media['url'], timeout=30)
    assert fetched.status_code == 200
    page.get_by_role('button', name=label, exact=True).wait_for()
    return {'kind': kind, 'mediaId': str(media['mediaId'])}


def assert_history_opens_media(page):
    """After reload, video and audio bubbles open same-origin media URLs."""
    page.get_by_role('button', name='打开视频', exact=True).first.wait_for()
    page.get_by_role('button', name='播放语音文件', exact=True).first.wait_for()
    for label in ['打开视频', '播放语音文件']:
        with page.expect_popup() as opened:
            page.get_by_role('button', name=label, exact=True).first.click()
        opened.value.wait_for_load_state()
        assert '/xbh-media/' in opened.value.url
        opened.value.close()


def run_scene(browser, session, base, sender, receiver, output, width, scheme, current):
    """Upload all media kinds at one viewport, reload, and verify history.

    CURRENT['page'] tracks the open page so a failure can be screenshotted.
    """
    context = logged_in_context(browser, sender, width, scheme)
    page = current['page'] = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append('console: ' + m.text[:300]) if m.type == 'error' else None)
    page.on('response', lambda r: print('response', r.status, r.url.split('?')[0], flush=True) if '/api/' in r.url else None)
    url = conversation_url(session, base, sender, receiver)
    open_thread(page, url)
    uploads = [upload_through_chooser(page, session, *upload) for upload in UPLOADS]
    page.reload()
    enable_semantics(page)
    page.get_by_role('button', name='发送视频', exact=True).wait_for(timeout=30000)
    assert_history_opens_media(page)
    filename = f'media-{width}-{scheme}.png'
    page.screenshot(path=output / filename)
    # No horizontal overflow at any width, and no page errors.
    assert not page.evaluate('document.body.scrollWidth > innerWidth')
    assert not errors, errors
    context.close()
    return {'width': width, 'scheme': scheme, 'uploads': uploads, 'reloadHistory': True,
            'openVideo': True, 'openAudio': True, 'pageErrors': errors, 'screenshot': filename}


def run(base, output, chromium):
    output.mkdir(parents=True, exist_ok=True)
    session = direct_session()

    # Two fresh users with an existing conversation to attach media to.
    _, sender = register_user(session, base, 'media', PASSWORD)
    _, receiver = register_user(session, base, 'media', PASSWORD)
    initial = session.post(base + '/api/v2/messages', headers={'Authorization': 'Bearer ' + sender['token']}, json={
        'receiverId': receiver['userId'], 'content': 'media browser validation',
        'msgType': 1, 'idempotencyKey': uuid.uuid4().hex}, timeout=30)
    assert initial.status_code == 200

    report = {'base': base, 'scenes': []}
    current = {'page': None}
    with chromium_browser(chromium) as browser:
        try:
            for width, scheme in SCENES:
                report['scenes'].append(run_scene(
                    browser, session, base, sender, receiver, output, width, scheme, current))

            # The receiver sees the same media in its own history.
            context = logged_in_context(browser, receiver, 390, 'light')
            page = current['page'] = context.new_page()
            open_thread(page, conversation_url(session, base, receiver, sender))
            page.get_by_role('button', name='打开视频', exact=True).first.wait_for()
            page.get_by_role('button', name='播放语音文件', exact=True).first.wait_for()
            page.screenshot(path=output / 'receiver-history.png')
            report['receiverHistory'] = True
            context.close()
        finally:
            # Leave a screenshot and the semantics text of a failed page behind.
            page = current['page']
            if page is not None and not page.is_closed():
                page.screenshot(path=output / 'last-state.png')
                print(page.locator('flt-semantics-host').inner_text()[:2000], flush=True)
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'scenes': len(report['scenes']), 'receiverHistory': report.get('receiverHistory', False)}))


if __name__ == '__main__':
    args = parse_args(__doc__)
    run(args.base, args.output, args.chromium)
