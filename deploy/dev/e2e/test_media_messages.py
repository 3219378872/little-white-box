"""Real proxy -> Gateway -> Media -> Message upload/send/replay regressions."""
import struct
from pathlib import Path

import pytest
from api_client import assert_error
from support import unique_key

FIXTURES = Path(__file__).with_name('fixtures')


def upload(client, kind, name, data=None, key=None):
    """Upload fixture NAME (or DATA) as KIND with a generic content type."""
    content = data if data is not None else (FIXTURES / name).read_bytes()
    return client.upload_media(kind, (name, content, 'application/octet-stream'),
                               key or unique_key('upload'))


@pytest.mark.parametrize('kind,name,msg_type', [
    ('image', 'image.png', 2), ('video', 'clip.mp4', 3), ('audio', 'voice.wav', 4),
    ('audio', 'voice.m4a', 4), ('audio', 'voice.mp3', 4),
])
def test_media_upload_send_replay_and_receiver_history(make_user, anon, kind, name, msg_type):
    sender, receiver = make_user(), make_user()
    key = unique_key('upload')
    first = upload(sender.client, kind, name, key=key)
    assert first.status_code == 200, first.text[:200]
    media = first.json()
    replay = upload(sender.client, kind, name, key=key)
    assert replay.status_code == 200, replay.text[:200]
    assert replay.json()['mediaId'] == media['mediaId']
    if kind != 'image':
        assert media['fileType'] == kind
        assert media['fileSize'] == (FIXTURES / name).stat().st_size
    payload = dict(receiverId=receiver.id, content='https://untrusted.invalid/forged',
                   msgType=msg_type, mediaId=media['mediaId'], idempotencyKey=unique_key('dm'))
    sent = sender.client.send_message(payload)
    assert sent.status_code == 200, sent.text[:200]
    repeated = sender.client.send_message(payload)
    assert repeated.status_code == 200, repeated.text[:200]
    assert repeated.json()['messageId'] == sent.json()['messageId']
    conversations = receiver.client.conversations(pageSize=50).json()['conversations']
    conversation = next(c for c in conversations if c['targetUserId'] == sender.id)
    for _ in range(2):
        messages = receiver.client.conversation_messages(conversation['id']).json()['messages']
        row = next(m for m in messages if m['id'] == sent.json()['messageId'])
        assert row['mediaId'] == media['mediaId']
        assert row['content'] == media['url']
        assert row['msgType'] == msg_type
    fetched = anon.get(media['url'])
    assert fetched.status_code == 200
    if kind != 'image':
        assert fetched.content == (FIXTURES / name).read_bytes()
    else:
        assert len(fetched.content) > 0
    stolen = receiver.client.send_message(dict(payload, receiverId=sender.id, idempotencyKey=unique_key('dm')))
    assert_error(stolen, 400, 2)
    mismatch = sender.client.send_message(dict(payload, msgType=3 if msg_type == 2 else 2, idempotencyKey=unique_key('dm')))
    assert_error(mismatch, 400, 2)


def test_video_larger_than_old_proxy_limit(user):
    clip = (FIXTURES / 'clip.mp4').read_bytes()
    padding = 21 * 1024 * 1024
    data = clip + struct.pack('>I4s', padding + 8, b'free') + bytes(padding)
    response = upload(user.client, 'video', 'large.mp4', data)
    assert response.status_code == 200, response.text[:200]
    assert response.json()['fileSize'] == len(data)


@pytest.mark.parametrize('kind,name', [('video','voice.m4a'), ('audio','clip.mp4')])
def test_container_track_mismatch_rejected(user, kind, name):
    assert_error(upload(user.client, kind, name), 400, 4002)


def test_audio_limit_and_key_conflict(user):
    wave = (FIXTURES / 'voice.wav').read_bytes()
    key = unique_key('upload')
    assert upload(user.client,'audio','a.wav',wave,key).status_code == 200
    conflict = upload(user.client,'audio','a.wav',wave+b'changed',key)
    assert conflict.status_code == 409, conflict.text[:200]
    for size in [10 * 1024 * 1024, 10 * 1024 * 1024 + 1]:
        response = upload(user.client,'audio','boundary.wav',wave+bytes(size-len(wave)))
        if size == 10 * 1024 * 1024:
            assert response.status_code == 200, response.text[:200]
        else:
            assert_error(response, 413, 4001)


@pytest.mark.parametrize('kind,name',[('video','clip.mp4'),('audio','voice.wav')])
def test_new_upload_requires_auth(anon, kind, name):
    assert_error(upload(anon,kind,name),401,1006)
