from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from app import main, media
from app.config import Settings
from app.security import RateLimiter, owner_id

ORIGIN = 'https://app.example.com'
API = 'https://api.example.com'
A, B = 'a' * 64, 'b' * 64


@pytest.fixture
def production(monkeypatch, tmp_path):
    monkeypatch.setenv('TUBECLIP_ENV', 'production')
    monkeypatch.setenv('ALLOWED_ORIGINS', ORIGIN)
    monkeypatch.setenv('ALLOWED_HOSTS', 'api.example.com')
    cfg = replace(Settings.from_env(), requests_per_minute=0, analyses_per_minute=0, downloads_per_minute=0)
    monkeypatch.setattr(main, 'settings', cfg)
    monkeypatch.setattr(media, 'settings', cfg)
    monkeypatch.setattr(main, 'ALLOWED_HOSTS', cfg.allowed_hosts)
    monkeypatch.setattr(main, 'ALLOWED_ORIGINS', cfg.allowed_origins)
    monkeypatch.setattr(main, 'RETENTION_SECONDS', cfg.retention)
    monkeypatch.setattr(main, 'limiter', RateLimiter())
    monkeypatch.setattr(main, 'jobs', {})
    monkeypatch.setattr(main, 'analyses', {})
    for name in ('DOWNLOADS', 'TEMP'):
        path = tmp_path / name; path.mkdir()
        monkeypatch.setattr(media, name, path)
    # Exercise actual browser CORS semantics in addition to the origin policy.
    app = CORSMiddleware(main.app, allow_origins=[ORIGIN], allow_methods=['GET', 'POST', 'DELETE'],
                         allow_headers=['Content-Type', 'X-TubeClip-Client'], expose_headers=['Retry-After', 'Content-Disposition'])
    return TestClient(app, base_url=API, headers={'Origin': ORIGIN, 'X-TubeClip-Client': A})


def sample_info():
    return {'id': 'aqz-KE-bpKQ', 'url': 'https://www.youtube.com/watch?v=aqz-KE-bpKQ',
            'title': 'Video', 'duration': 8, 'qualities': [720], 'audio_available': True}


def test_exact_cors_preflight_and_no_wildcard(production):
    headers = {'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type,x-tubeclip-client'}
    response = production.options('/api/analyze', headers=headers)
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == ORIGIN
    assert 'access-control-allow-credentials' not in response.headers
    denied = production.options('/api/analyze', headers={**headers, 'Origin': 'https://evil.test'})
    assert denied.status_code == 400 and 'access-control-allow-origin' not in denied.headers
    assert production.get('/api/health', headers={'Host': 'evil.test'}).status_code == 403
    assert production.get('/api/health', headers={'Origin': 'https://app.example.com.evil.test'}).status_code == 403


def test_real_production_import_uses_env_and_render_hostname(tmp_path):
    script = """
from fastapi.testclient import TestClient
from app.main import app
c=TestClient(app,base_url='https://sample-api.onrender.com')
r=c.get('/api/health',headers={'Origin':'https://app.example.com'})
assert r.status_code==200, r.text
assert r.headers['access-control-allow-origin']=='https://app.example.com'
assert r.json()['hosting_mode']=='hosted'
assert c.get('/api/health',headers={'Origin':'https://preview.vercel.app'}).status_code==403
assert c.options('/api/analyze',headers={'Host':'evil.test','Origin':'https://app.example.com','Access-Control-Request-Method':'POST'}).status_code==403
assert c.post('/api/analyze',headers={'Origin':'https://app.example.com'},json={}).headers['access-control-allow-origin']=='https://app.example.com'
assert c.options('/api/download',headers={'Origin':'https://app.example.com','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'x-tubeclip-client,content-type'}).status_code==200
"""
    subprocess.run([sys.executable, '-c', script], env={**os.environ, 'TUBECLIP_ENV': 'production',
        'ALLOWED_ORIGINS': ORIGIN, 'ALLOWED_HOSTS': '', 'RENDER_EXTERNAL_HOSTNAME': 'sample-api.onrender.com',
        'TUBECLIP_DATA_DIR': str(tmp_path)}, capture_output=True, text=True, check=True)


@pytest.mark.parametrize('key,value', [
    ('ALLOWED_ORIGINS', '*'), ('ALLOWED_ORIGINS', 'https://*.vercel.app'),
    ('ALLOWED_ORIGINS', 'https://app.example.com/path'), ('ALLOWED_ORIGINS', 'http://app.example.com'),
    ('ALLOWED_HOSTS', '*'), ('ALLOWED_HOSTS', 'https://api.example.com'), ('ALLOWED_HOSTS', 'api.example.com:10000'),
    ('MAX_CONCURRENT_JOBS', '99'), ('JOB_TIMEOUT_SECONDS', 'oops'),
])
def test_invalid_production_configuration_fails_closed(monkeypatch, key, value):
    monkeypatch.setenv('TUBECLIP_ENV', 'production')
    monkeypatch.setenv('ALLOWED_ORIGINS', ORIGIN)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_production_requires_origin_and_client_identifier(production):
    production.headers.pop('X-TubeClip-Client')
    assert production.post('/api/analyze', json={'url': sample_info()['url']}).status_code == 401
    assert production.get('/api/health').status_code == 200
    assert production.post('/api/analyze', headers={'X-TubeClip-Client': 'short'}, json={}).status_code == 400


def test_request_size_is_bounded_even_without_content_length(production):
    response = production.post('/api/analyze', content=iter([b'x' * 4096, b'x' * 4097]))
    assert response.status_code == 413
    assert response.headers['access-control-allow-origin'] == ORIGIN


def test_client_ownership_and_signed_file_download(production, monkeypatch):
    monkeypatch.setattr(media, 'analyze', lambda url: sample_info())
    monkeypatch.setattr(main.executor, 'submit', lambda *a: None)
    token = production.post('/api/analyze', json={'url': sample_info()['url']}).json()['analysis_id']
    assert production.post('/api/download', headers={'X-TubeClip-Client': B}, json={'analysis_id': token}).status_code == 404
    response = production.post('/api/download', json={'analysis_id': token})
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    path = media.DOWNLOADS / 'Video - 720p - deadbeef.mp4'; path.write_bytes(b'finished file')
    main.update(job_id, status='ready', path=path, filename=path.name)
    assert production.get(f'/api/jobs/{job_id}', headers={'X-TubeClip-Client': B}).status_code == 404
    assert production.delete(f'/api/jobs/{job_id}', headers={'X-TubeClip-Client': B}).status_code == 404
    status = production.get(f'/api/jobs/{job_id}').json()
    assert all(k not in status for k in ('path', 'cancel', 'owner', 'deadline', 'selection_key'))
    assert production.get(f'/api/files/{job_id}').status_code == 403
    signed = status['download_url']
    saved = production.get(signed, headers={'X-TubeClip-Client': ''})
    assert saved.status_code == 200 and saved.content == b'finished file'
    assert saved.headers['cache-control'] == 'no-store'
    assert 'attachment' in saved.headers['content-disposition']
    assert production.get(signed + 'bad').status_code == 403
    assert production.get(signed[:-1] + '%C3%A9').status_code == 403
    assert production.get(signed.replace('expires=', 'expires=1')).status_code == 403


def test_production_rate_limits_and_retry_after(production, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, analyses_per_minute=1))
    monkeypatch.setattr(media, 'analyze', lambda url: sample_info())
    assert production.post('/api/analyze', json={'url': sample_info()['url']}).status_code == 200
    limited = production.post('/api/analyze', headers={'X-TubeClip-Client': B, 'X-Forwarded-For': '8.8.8.8'}, json={'url': sample_info()['url']})
    assert limited.status_code == 429 and int(limited.headers['retry-after']) > 0
    assert limited.headers['access-control-allow-origin'] == ORIGIN


def test_limit_window_recovers_and_memory_is_bounded():
    limiter = RateLimiter(max_keys=4)
    assert limiter.check(['peer'], 1, now=0) == 0
    assert limiter.check(['peer'], 1, now=1) > 0
    assert limiter.check(['peer'], 1, now=61) == 0
    for i in range(20):
        limiter.check([f'client:{i}'], 1, now=62)
    assert len(limiter.buckets) <= 4


def test_health_returns_503_when_media_dependencies_are_missing(production, monkeypatch):
    monkeypatch.setattr(media, 'binary', lambda name: None)
    response = production.get('/api/health')
    assert response.status_code == 503
    assert response.json()['status'] == 'degraded'
    assert not response.json()['dependencies']['ffmpeg']


def test_health_handles_missing_ejs_package(production, monkeypatch):
    original = main.importlib.metadata.version
    def version(name):
        if name == 'yt-dlp-ejs':
            raise main.importlib.metadata.PackageNotFoundError(name)
        return original(name)
    monkeypatch.setattr(main.importlib.metadata, 'version', version)
    assert production.get('/api/health').status_code == 503


def test_analysis_and_job_records_expire_with_their_files(production):
    path = media.DOWNLOADS / 'Video - 720p - deadbeef.mp4'; path.write_bytes(b'file')
    main.analyses['old'] = {'created': time.time() - 901}
    main.jobs['old'] = {'id': 'old', 'status': 'ready', 'finished': time.time() - 1801, 'path': path}
    main.prune_records()
    assert not main.analyses and not main.jobs and not path.exists()


def test_public_limits_reject_long_sources_clips_and_exhausted_storage(production, monkeypatch):
    info = {**sample_info(), 'duration': 601}
    monkeypatch.setattr(media, 'analyze', lambda url: info)
    assert production.post('/api/analyze', json={'url': info['url']}).status_code == 422
    info['duration'] = 300
    token = production.post('/api/analyze', json={'url': info['url']}).json()['analysis_id']
    response = production.post('/api/download', json={'analysis_id': token, 'start': '00:00:00', 'end': '00:02:01'})
    assert response.status_code == 422 and '120 seconds' in response.text
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_bytes=1))
    (media.DOWNLOADS / 'existing').write_bytes(b'full')
    assert production.post('/api/download', json={'analysis_id': token}).status_code == 422


def test_unsupported_and_private_cdn_addresses_are_rejected(production, monkeypatch):
    for url in ('file:///etc/passwd', 'http://i.pinimg.com/photo.jpg', 'https://evil.test/video.mp4', 'https://i.pinimg.com.evil.test/file'):
        with pytest.raises(ValueError):
            media.validate_media_url(url)
    monkeypatch.setattr(media.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='Private'):
        media.validate_media_url('https://i.pinimg.com/photo.jpg')


def test_oversized_output_cannot_be_marked_ready(production):
    path = media.DOWNLOADS / 'file.mp4'; path.write_bytes(b'xx')
    main.jobs['job'] = {'id': 'job', 'status': 'processing'}
    original = main.settings
    try:
        main.settings = replace(original, output_bytes=1)
        with pytest.raises(ValueError, match='file size'):
            main.enforce_output_limit('job', path)
    finally:
        main.settings = original


def test_daily_job_budget_is_enforced(production, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, downloads_per_day=1))
    monkeypatch.setattr(media, 'analyze', lambda url: sample_info())
    monkeypatch.setattr(main.executor, 'submit', lambda *a: None)
    token = production.post('/api/analyze', json={'url': sample_info()['url']}).json()['analysis_id']
    result = production.post('/api/download', json={'analysis_id': token}).json()
    main.jobs[result['job_id']]['status'] = 'cancelled'
    response = production.post('/api/download', json={'analysis_id': token})
    assert response.status_code == 429 and 'daily' in response.text


def test_global_budget_cannot_be_evicted_by_client_id_rotation():
    limiter = RateLimiter(max_keys=4)
    assert limiter.check(['files:daily:global'], 1, window=86400, now=0) == 0
    for i in range(20):
        limiter.check([f'client:{i}'], 1, now=1)
    assert limiter.check(['files:daily:global'], 1, window=86400, now=2) > 0
    assert len(limiter.buckets) <= 4


def test_production_budgets_cannot_be_disabled(monkeypatch):
    monkeypatch.setenv('TUBECLIP_ENV', 'production')
    monkeypatch.setenv('ALLOWED_ORIGINS', ORIGIN)
    monkeypatch.setenv('FILE_RETENTION_SECONDS', '0')
    with pytest.raises(ValueError, match='greater than zero'):
        Settings.from_env()


def test_retention_starts_when_an_old_source_file_is_finalized(production):
    path = media.DOWNLOADS / 'Video - 720p - deadbeef.mp4'; path.write_bytes(b'video')
    os.utime(path, (1, 1))
    main.jobs['job'] = {'id': 'job', 'status': 'processing'}
    main.enforce_output_limit('job', path)
    main.cleanup_downloads()
    assert path.exists() and path.stat().st_mtime > time.time() - 5


def test_watchdog_cancels_overdue_work_and_working_files(production, tmp_path):
    class OnePass:
        def wait(self, timeout):
            return False
    for deadline, content, source_bytes in [(time.monotonic() - 1, b'', 100), (time.monotonic() + 60, b'big', 1)]:
        work = tmp_path / str(uuid4()); work.mkdir(); (work / 'source').write_bytes(content)
        main.jobs['job'] = {'id': 'job', 'status': 'downloading', 'deadline': deadline, 'cancel': threading.Event()}
        original = main.settings
        try:
            main.settings = replace(original, source_bytes=source_bytes)
            main.watch_job('job', work, OnePass())
        finally:
            main.settings = original
        assert main.jobs['job']['cancel'].is_set()
        assert main.jobs['job']['limit_error']


def test_expired_file_is_not_advertised_as_downloadable(production):
    main.jobs['job'] = {'id': 'job', 'status': 'ready', 'owner': owner_id(A),
                        'path': media.DOWNLOADS / 'removed.mp4', 'finished': time.time()}
    response = production.get('/api/jobs/job').json()
    assert response['status'] == 'failed' and response['phase'] == 'Expired'
    assert 'download_url' not in response


def test_blueprint_is_backend_only_and_free():
    root = Path(__file__).resolve().parents[2]
    blueprint = (root / 'render.yaml').read_text()
    docker = (root / 'Dockerfile').read_text()
    assert 'plan: free' in blueprint and 'numInstances: 1' in blueprint
    assert 'sync: false' in blueprint and 'ALLOWED_ORIGINS' in blueprint
    assert 'disk:' not in blueprint and 'databases:' not in blueprint
    assert 'npm ' not in docker and 'COPY frontend' not in docker
    assert 'ffmpeg' in docker and 'ffprobe -version' in docker and 'node --version' in docker
    assert '0.0.0.0' in docker and '${PORT:-10000}' in docker
    assert 'USER tubeclip' in docker


def test_production_extractor_filter_rejects_other_providers_without_fetching(production):
    result = subprocess.run(media.base_command() + ['--skip-download', '--dump-single-json', 'https://vimeo.com/111691128'],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and 'No suitable extractor' in result.stderr


def test_photo_redirect_cannot_reach_a_private_address(production, monkeypatch, tmp_path):
    import httpx
    sent = []
    def transport(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://127.0.0.1/private'})
    original = httpx.Client
    monkeypatch.setattr(main.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    monkeypatch.setattr(media.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])
    main.jobs['job'] = {'id': 'job', 'status': 'downloading', 'cancel': threading.Event()}
    target = tmp_path / 'photo.jpg'
    with pytest.raises(ValueError, match='supported public media CDNs'):
        main.download_image('job', 'https://i.pinimg.com/photo.jpg', target)
    assert sent == ['https://i.pinimg.com/photo.jpg']
    assert not target.exists()



def test_actual_source_size_is_checked_when_estimates_are_missing(production, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, source_bytes=1))
    def download(job_id, cmd, on_line=None):
        source = media.TEMP / f'job-{job_id}' / 'source.mp4'
        source.write_bytes(b'xx')
        on_line('TCFILE:' + str(source))
    monkeypatch.setattr(main, 'run_process', download)
    main.jobs['job'] = {'id': 'job', 'status': 'queued', 'cancel': threading.Event()}
    main.worker('job', sample_info(), 720, False, None, None)
    assert main.jobs['job']['status'] == 'failed'
    assert 'source-file limit' in main.jobs['job']['error']
    assert not list(media.TEMP.iterdir()) and not list(media.DOWNLOADS.iterdir())
