from dataclasses import replace
import json
import logging
import subprocess

import pytest
from app import diagnostics, main, media
from app.extraction_probe import self_check

URL = 'https://www.youtube.com/watch?v=Ybayv7RvCXg'


@pytest.fixture
def diagnostic_runtime(monkeypatch):
    monkeypatch.setattr(diagnostics, 'runtime_details', lambda: {'yt-dlp': '2026.10.26', 'yt-dlp-ejs': '0.8.0', 'node': 'v24.18.0'})


def test_failure_logs_context_without_metadata_credentials_or_raw_command(monkeypatch, caplog, diagnostic_runtime):
    stderr = '''[debug] Command-line config: ['--cookies', '/private/cookies.txt']
[debug] Proxy map: {'https': 'https://person:password@proxy.invalid'}
[debug] JS runtimes: node-24.18.0
[debug] [youtube] [jsc] JS Challenge Providers: node, deno (unavailable)
[debug] [youtube] [jsc:node] Using challenge solver core script v0.8.0 (source: python package, variant: minified)
WARNING: Cookie: super-secret-cookie
WARNING: Authorization: Bearer super-secret-token
WARNING: /Users/person/private.py: request https://cdn.invalid/video?sig=hidden-signature
ERROR: [youtube] Ybayv7RvCXg: Failed to extract any player response
'''
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 1, '{"private_metadata":"never-log-this"}', stderr)
    monkeypatch.setattr(media.subprocess, 'run', run)
    with caplog.at_level(logging.ERROR), pytest.raises(diagnostics.ExtractionError) as error:
        media.analyze_video(URL)
    assert error.value.reason == 'player_response'
    assert 'hidden-signature' not in str(error.value)
    event = json.loads(next(r.message for r in caplog.records if r.name == 'tubeclip.extractor'))
    assert event['event'] == 'extraction_failed' and event['returncode'] == 1
    assert event['runtime']['yt-dlp'] == '2026.10.26'
    assert 'JS Challenge Providers: node' in caplog.text
    assert 'Failed to extract any player response' in caplog.text
    for secret in (URL, 'Ybayv7RvCXg', 'super-secret', 'hidden-signature', '/Users/person', 'never-log-this', 'proxy.invalid', 'cookies.txt'):
        assert secret not in caplog.text
    assert len(calls) == 1  # Failure diagnostics must not trigger another source request.
    cmd, kwargs = calls[0]
    assert '--verbose' in cmd and '--no-warnings' not in cmd
    assert '--skip-download' in cmd and kwargs['timeout'] == 90
    assert not any(x in cmd for x in ('--print-traffic', '--write-pages', '--dump-pages', '--no-check-certificates', '--cookies'))
    message = main.friendly(error.value)
    assert 'may be restricting' in message and 'server' in message
    assert 'IP is blocked' not in message and 'signature' not in message


def test_timeout_is_sanitized_without_losing_failure_context(monkeypatch, caplog, diagnostic_runtime):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 90, stderr=b'WARNING: timed out at https://cdn.invalid/?secret=value')
    monkeypatch.setattr(media.subprocess, 'run', run)
    with caplog.at_level(logging.ERROR), pytest.raises(diagnostics.ExtractionError) as error:
        media.analyze_video(URL)
    assert error.value.reason == 'timeout'
    assert URL not in str(error.value) and 'secret=value' not in caplog.text
    assert 'too long' in main.friendly(error.value)


def test_partial_solver_failure_is_logged_without_dropping_formats(monkeypatch, caplog, diagnostic_runtime):
    info = {'id': 'Ybayv7RvCXg', 'duration': 8, 'formats': [{'height': 720, 'vcodec': 'h264', 'acodec': 'aac', 'url': 'https://cdn.invalid/video'}]}
    stderr = 'WARNING: [youtube] n challenge solving failed: Some formats may be missing.\nTypeError: a.addOnDisposeCallback is not a function'
    monkeypatch.setattr(media.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(info), stderr))
    with caplog.at_level(logging.WARNING):
        result = media.analyze_video(URL)
    assert result['qualities'] == [720] and result['audio_available']
    assert result['_download_info'] == info
    assert 'extraction_warning' in caplog.text and 'addOnDisposeCallback' in caplog.text
    assert 'https://cdn.invalid' not in caplog.text


@pytest.mark.parametrize('stderr,reason', [
    ('HTTP Error 429: Too Many Requests; Failed to extract any player response', 'access_refused'),
    ('All player responses are invalid. Your IP is likely being blocked', 'access_refused'),
    ('Failed to extract any player response', 'player_response'),
    ('Private video', 'unavailable'),
    ("[debug] Command-line config: ['--socket-timeout', '20']\nERROR: This video is unavailable", 'unavailable'),
    ('Error running node process (returncode: 1)', 'javascript'),
    ('No supported JavaScript runtime', 'javascript'),
    ('Unknown extractor failure', 'extractor'),
])
def test_failure_reasons(stderr, reason):
    assert diagnostics.failure_reason(stderr) == reason


def test_diagnostics_are_bounded_and_sensitive_payload_lines_are_omitted():
    text = '\n'.join(['WARNING: challenge=' + 'sensitive-data'] * 1000 + ['ERROR: ' + 'x' * 2000] * 1000)
    lines = diagnostics.diagnostic_lines(text)
    assert len(lines) <= 24 and all(len(line) <= 600 for line in lines)
    assert 'sensitive-data' not in '\n'.join(lines)


def test_production_command_keeps_security_and_resource_options(monkeypatch):
    monkeypatch.setattr(media, 'settings', replace(media.settings, production=True))
    cmd = media.base_command()
    assert cmd[cmd.index('--use-extractors') + 1] == 'youtube.*,pinterest,instagram'
    assert cmd[cmd.index('--concurrent-fragments') + 1] == '2'
    assert cmd[cmd.index('--js-runtimes') + 1] == 'node'
    assert cmd[cmd.index('--socket-timeout') + 1] == '20'
    assert '--ignore-config' in cmd and '--no-playlist' in cmd


def test_offline_ejs_and_restricted_node_self_check():
    result = self_check()
    assert result['ejs_integrity'] == result['restricted_node'] == 'ok'


def test_storyboards_are_removed_before_production_validation_without_allowing_image_hosts(monkeypatch):
    import socket
    monkeypatch.setattr(media, 'settings', replace(media.settings, production=True))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    storyboard = {'protocol': 'mhtml', 'ext': 'mhtml', 'vcodec': 'none', 'acodec': 'none',
                  'height': 90, 'url': 'https://i.ytimg.com/storyboard'}
    video = {'height': 720, 'vcodec': 'h264', 'acodec': 'aac', 'url': 'https://video.googlevideo.com/video'}
    raw = {'id': 'Ybayv7RvCXg', 'duration': 8, 'formats': [storyboard, video]}
    result = media.video_info(raw, URL)
    assert result['qualities'] == [720] and result['audio_available']
    assert result['_download_info']['formats'] == [video]
    assert raw['formats'] == [storyboard, video]  # Do not mutate caller metadata.
    with pytest.raises(ValueError, match='outside'):
        media.validate_media_url(storyboard['url'])
    # A real media URL on an image host still fails closed.
    raw['formats'] = [{**video, 'url': storyboard['url']}]
    with pytest.raises(ValueError, match='outside'):
        media.video_info(raw, URL)
