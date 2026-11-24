import os
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
from app import main, media


def test_public_host_is_allowed_without_accepting_other_origins(monkeypatch):
    domain = 'api.example.com'
    monkeypatch.setattr(main, 'ALLOWED_HOSTS', {domain, '127.0.0.1'})
    monkeypatch.setattr(main, 'ALLOWED_ORIGINS', {f'https://{domain}'})
    client = TestClient(main.app, base_url=f'https://{domain}')
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/health', headers={'Origin': f'https://{domain}'}).status_code == 200
    assert client.get('/api/health', headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.get('/api/health', headers={'Host': 'evil.test'}).status_code == 403


def test_backend_is_api_only_and_does_not_expose_source_files():
    client = TestClient(main.app)
    root = client.get('/')
    assert root.status_code == 404
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/api/unknown').status_code == 404
    assert client.get('/.env').status_code == 404


def test_hosted_cleanup_only_removes_expired_application_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'DOWNLOADS', tmp_path)
    monkeypatch.setattr(main, 'RETENTION_SECONDS', 7200)
    expired = tmp_path / 'Video - 720p - aabbccdd.mp4'
    active = tmp_path / 'Video - 720p - 11223344.mp4'
    fresh = tmp_path / 'Photo - 1080p - aa22bb33.jpg'
    unrelated = tmp_path / 'my-video.mp4'
    for path in (expired, active, fresh, unrelated):
        path.write_bytes(b'fixture')
    old = time.time() - 7201
    for path in (expired, active, unrelated):
        os.utime(path, (old, old))
    monkeypatch.setattr(main, 'jobs', {'active': {'id': '11223344-remaining', 'status': 'processing'}})
    main.cleanup_downloads()
    assert not expired.exists()
    assert all(path.exists() for path in (active, fresh, unrelated))
    monkeypatch.setattr(main, 'RETENTION_SECONDS', 0)
    os.utime(fresh, (old, old))
    main.cleanup_downloads()
    assert fresh.exists()


def test_server_data_directory_is_configurable(tmp_path):
    env = {**os.environ, 'TUBECLIP_DATA_DIR': str(tmp_path / 'server-data')}
    result = subprocess.run([sys.executable, '-c', 'from app import media; print(media.DOWNLOADS)'],
                            capture_output=True, text=True, env=env, check=True)
    assert Path(result.stdout.strip()) == tmp_path / 'server-data' / 'downloads'
    assert (tmp_path / 'server-data' / 'temp').is_dir()
