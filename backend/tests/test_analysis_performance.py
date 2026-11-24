from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from app import main, media

URL = 'https://www.youtube.com/watch?v=Ybayv7RvCXg'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, 'analyses', {})
    monkeypatch.setattr(main, 'analysis_pending', {})
    monkeypatch.setattr(main, 'analysis_slots', threading.BoundedSemaphore(1))
    return TestClient(main.app)


def metadata():
    return {'url': URL, 'duration': 10, 'qualities': [1080, 720, 360],
            'audio_available': True, '_extracted_at': time.time(), '_download_info': {'private': True}}


def test_repeated_canonical_link_reuses_metadata_with_fresh_private_token(client, monkeypatch):
    calls = []
    info = metadata()
    monkeypatch.setattr(media, 'analyze', lambda url: calls.append(url) or info)
    first = client.post('/api/analyze', json={'url': URL}).json()
    second = client.post('/api/analyze', json={'url': 'https://youtu.be/Ybayv7RvCXg'}).json()
    assert calls == [URL]
    assert first['analysis_id'] != second['analysis_id']
    assert {k: v for k, v in first.items() if k != 'analysis_id'} == {k: v for k, v in second.items() if k != 'analysis_id'}
    assert not any(k.startswith('_') for k in second)
    # Hits also work while another source occupies the extractor slot.
    assert main.analysis_slots.acquire(False)
    try:
        assert client.post('/api/analyze', json={'url': URL}).status_code == 200
    finally:
        main.analysis_slots.release()


def test_cache_expiration_and_client_isolation(client, monkeypatch):
    calls = []
    monkeypatch.setattr(media, 'analyze', lambda url: calls.append(url) or metadata())
    monkeypatch.setattr(main, 'request_owner', lambda request: request.headers.get('test-owner', 'a'))
    first = client.post('/api/analyze', json={'url': URL}).json()
    info = main.analyses[first['analysis_id']]['info']
    extracted_at = info['_extracted_at']
    client.post('/api/analyze', json={'url': URL})
    assert info['_extracted_at'] == extracted_at
    client.post('/api/analyze', json={'url': URL}, headers={'test-owner': 'b'})
    assert len(calls) == 2
    info['_extracted_at'] -= main.ANALYSIS_CACHE_SECONDS + 1
    assert client.post('/api/analyze', json={'url': URL}).status_code == 200
    assert len(calls) == 3


def test_concurrent_clicks_share_extraction_and_failures_can_retry(client, monkeypatch):
    started, release, follower = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original_result = main.Future.result
    def waiting_result(self, *args, **kwargs):
        if self in main.analysis_pending.values():
            follower.set()
        return original_result(self, *args, **kwargs)
    monkeypatch.setattr(main.Future, 'result', waiting_result)
    def extract(url):
        calls.append(url)
        started.set()
        assert release.wait(5)
        return metadata()
    monkeypatch.setattr(media, 'analyze', extract)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client.post, '/api/analyze', json={'url': URL})
        assert started.wait(5)
        second = pool.submit(client.post, '/api/analyze', json={'url': URL})
        assert follower.wait(5)
        release.set()
        a, b = first.result(), second.result()
    assert a.status_code == b.status_code == 200
    assert len(calls) == 1
    assert a.json()['analysis_id'] != b.json()['analysis_id']
    assert not main.analysis_pending
    main.analyses.clear()
    monkeypatch.setattr(media, 'analyze', lambda url: (_ for _ in ()).throw(ValueError('Unavailable')))
    assert client.post('/api/analyze', json={'url': URL}).status_code == 422
    monkeypatch.setattr(media, 'analyze', lambda url: metadata())
    assert client.post('/api/analyze', json={'url': URL}).status_code == 200


def test_cdn_dns_is_deduplicated_only_within_one_validation(monkeypatch):
    monkeypatch.setattr(media, 'settings', replace(media.settings, production=True))
    calls = []
    def resolve(host, *args, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    info = {'formats': [{'url': f'https://video.googlevideo.com/{i}',
                         'fragments': [{'url': 'segment'}]} for i in range(100)]}
    media.validate_download_info(info)
    assert calls == ['video.googlevideo.com']
    # A subsequent download must resolve again, rejecting changed private addresses.
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='Private or reserved'):
        media.validate_download_info(info)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    info['formats'].append({'url': 'http://video.googlevideo.com/unsafe'})
    with pytest.raises(ValueError, match='outside'):
        media.validate_download_info(info)
