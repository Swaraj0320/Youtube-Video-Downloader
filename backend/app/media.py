from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import ipaddress
import subprocess
import sys
import time
import httpx
from urllib.parse import urlparse, parse_qs, urljoin
from .config import settings
from .diagnostics import ExtractionError, failure_reason, log_extraction

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get('TUBECLIP_DATA_DIR', str(ROOT))).resolve()
DOWNLOADS = DATA_ROOT / 'downloads'
TEMP = DATA_ROOT / 'temp'
for directory in (DOWNLOADS, TEMP):
    directory.mkdir(parents=True, exist_ok=True)


def validate_duration(duration):
    if settings.max_duration and duration > settings.max_duration:
        raise ValueError(f'This personal demo accepts sources up to {settings.max_duration} seconds. Use a shorter source or run the app locally.')


def validate_media_url(value, resolved_hosts=None):
    if not settings.production:
        return
    parsed = urlparse(value)
    host = parsed.hostname or ''
    trusted = ('googlevideo.com', 'pinimg.com', 'cdninstagram.com', 'fbcdn.net')
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == suffix or host.endswith('.' + suffix) for suffix in trusted)):
        raise ValueError('The source points outside the supported public media CDNs.')
    if resolved_hosts is not None and host in resolved_hosts:
        return
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise ValueError('Private or reserved network media addresses are not allowed.')
    except OSError:
        raise ValueError('The source media hostname could not be resolved.') from None
    if resolved_hosts is not None:
        resolved_hosts.add(host)


def validate_download_info(info):
    if not settings.production:
        return
    resolved_hosts = set()
    for f in [info, *(info.get('formats') or []), *(info.get('requested_formats') or []), *(info.get('requested_downloads') or [])]:
        for key in ('url', 'manifest_url', 'fragment_base_url'):
            if f.get(key):
                validate_media_url(f[key], resolved_hosts)
        for fragment in f.get('fragments') or []:
            if fragment.get('url'):
                validate_media_url(urljoin(f.get('fragment_base_url') or f.get('url') or '', fragment['url']), resolved_hosts)


def binary(name):
    local = ROOT / 'tools' / 'node_modules'
    candidates = [local / 'ffmpeg-static' / name] if name == 'ffmpeg' else [local / 'ffprobe-static' / 'bin' / 'darwin' / arch / 'ffprobe' for arch in ('arm64', 'x64')]
    candidates.insert(0, ROOT / 'tools' / 'bin' / name)
    return next((str(p) for p in candidates if p.is_file()), None) or shutil.which(name)


def youtube_url(value):
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError()
        host = (parsed.hostname or '').lower()
        query = parse_qs(parsed.query)
        if host not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'}:
            raise ValueError()
        if parsed.path.rstrip('/') == '/playlist':
            raise ValueError('Playlist-only links are not supported. Paste a link to one video.')
        parts = parsed.path.strip('/').split('/')
        if host.endswith('youtu.be'):
            video_id = parts[0]
        elif parsed.path == '/watch':
            video_id = query.get('v', [''])[0]
        elif len(parts) == 2 and parts[0] in ('shorts', 'live', 'embed'):
            video_id = parts[1]
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
        # Drop radio/playlist/share context; the extractor receives only one video.
        return f'https://www.youtube.com/watch?v={video_id}'
    except (ValueError, IndexError) as exc:
        raise ValueError(str(exc) or 'Enter a valid single-video YouTube URL.') from None


# Match only supported Pinterest domains and individual pins, never boards.
PINTEREST_SUFFIXES = set('com fr de ch jp cl ca it co.uk nz ru com.au at pt co.kr es com.mx dk ph th com.uy co nl info kr ie vn com.vn ec mx in pe co.at hu co.in co.nz id com.ec com.py tw be uk com.bo com.pe'.split())


def source_url(value):
    parsed = urlparse(value.strip())
    host = (parsed.hostname or '').lower()
    if host in ('instagram.com', 'www.instagram.com', 'm.instagram.com'):
        return instagram_url(value)
    if 'pinterest.' not in host and host not in ('pin.it', 'www.pin.it'):
        return youtube_url(value)
    try:
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError()
        if host in ('pin.it', 'www.pin.it'):
            if not re.fullmatch(r'/[A-Za-z0-9]+/?', parsed.path):
                raise ValueError()
            return f'https://pin.it{parsed.path}'
        domain = re.fullmatch(r'(?:[a-z]+\.)?pinterest\.(.+)', host)
        match = re.fullmatch(r'/pin/(?:[\w-]+--)?(\d+)/?', parsed.path)
        if not domain or domain[1] not in PINTEREST_SUFFIXES or not match:
            raise ValueError()
        return f'https://www.pinterest.com/pin/{match[1]}/'
    except ValueError:
        raise ValueError('Enter a single Pinterest pin link or a YouTube video link.') from None


def instagram_url(value):
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError()
        if parsed.hostname not in ('instagram.com', 'www.instagram.com', 'm.instagram.com'):
            raise ValueError()
        match = re.fullmatch(r'/(?:[A-Za-z0-9_.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]{5,64})/?', parsed.path)
        if not match:
            raise ValueError()
        kind = 'reel' if match[1] == 'reels' else match[1]
        index = parse_qs(parsed.query).get('img_index', ['1'])[0]
        if not index.isdigit() or not 1 <= int(index) <= 100:
            raise ValueError()
        return f'https://www.instagram.com/{kind}/{match[2]}/' + (f'?img_index={int(index)}' if int(index) > 1 else '')
    except ValueError:
        raise ValueError('Enter a public Instagram post or reel link. Profiles and stories are not supported.') from None


def instagram_image_formats(images):
    candidates = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = image.get('url', '')
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not host.endswith(('.cdninstagram.com', '.fbcdn.net')) or parsed.username or parsed.password or parsed.port not in (None, 443):
            continue
        if not any(f['url'] == url for f in candidates):
            candidates.append(dict(image))
    # Logged-out responses can omit every image dimension. Inspect the CDN asset
    # rather than inventing resolutions from its filename or URL parameters.
    def dimensions(image):
        width, height = image.get('width'), image.get('height')
        if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
            try:
                stream = next((s for s in probe(image['url']).get('streams', []) if s.get('codec_type') == 'video'), {})
                width, height = stream.get('width'), stream.get('height')
            except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                return None
        if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
            return None
        return {'url': image['url'], 'width': width, 'height': height}
    with ThreadPoolExecutor(max_workers=4) as inspectors:
        inspected = list(inspectors.map(dimensions, candidates[:24]))
    result = {}
    for image in inspected:
        if image and (image['height'] not in result or image['width'] > result[image['height']]['width']):
            result[image['height']] = image
    return sorted(result.values(), key=lambda f: f['height'], reverse=True)


def analyze_instagram(url):
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('instagram.py')), url],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    info = json.loads(result.stdout)
    if info.get('_tubeclip_media_type') != 'image':
        return video_info(info, url, platform='instagram')
    images = instagram_image_formats(info.get('_tubeclip_images') or [])
    if not images:
        raise ValueError('No accessible photo resolutions were found in this Instagram post.')
    return {'id': str(info['id']), 'url': url, 'platform': 'instagram', 'media_type': 'image',
            'title': str(info.get('description') or info.get('title') or 'Instagram photo')[:300],
            'channel': str(info.get('channel') or info.get('uploader') or 'Instagram')[:200],
            'duration': 0, 'thumbnail': images[0]['url'], 'qualities': [f['height'] for f in images],
            'image_formats': images, 'audio_available': False, 'embeddable': False,
            'item_count': info.get('_tubeclip_item_count', 1), 'item_index': info.get('_tubeclip_item_index', 1),
            '_http_headers': info.get('http_headers') or {'Referer': 'https://www.instagram.com/'}}


def resolve_pin(url):
    # Validate every redirect before requesting it; short links may not lead to arbitrary hosts.
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for _ in range(6):
            if urlparse(url).hostname == 'www.pinterest.com':
                return url
            response = client.get(url)
            if response.status_code >= 400:
                response.raise_for_status()
            location = response.headers.get('location')
            if not location:
                break
            target = urljoin(url, location)
            parsed = urlparse(target)
            if parsed.scheme == 'https' and parsed.hostname == 'api.pinterest.com' and parsed.port in (None, 443) and not parsed.username and not parsed.password and parsed.path.startswith('/url_shortener/'):
                url = target
                continue
            url = source_url(target)
            if 'pinterest.com/pin/' not in url and urlparse(url).hostname != 'pin.it':
                break
    raise ValueError('This Pinterest short link does not lead to an accessible individual pin.')


def pinterest_data(url):
    pin_id = urlparse(url).path.strip('/').split('/')[-1]
    response = httpx.get('https://www.pinterest.com/resource/PinResource/get/',
        params={'data': json.dumps({'options': {'field_set_key': 'unauth_react_main_pin', 'id': pin_id}})},
        headers={'X-Pinterest-PWS-Handler': 'www/[username].js'}, timeout=30)
    response.raise_for_status()
    data = response.json().get('resource_response', {}).get('data')
    if not isinstance(data, dict):
        raise ValueError('This Pinterest pin is unavailable or requires signing in.')
    return data


def image_formats(data):
    result = []
    for image in (data.get('images') or {}).values():
        if not isinstance(image, dict):
            continue
        url = image.get('url', '')
        host = urlparse(url).hostname or ''
        width, height = image.get('width'), image.get('height')
        if urlparse(url).scheme != 'https' or not host.endswith('.pinimg.com') or not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
            continue
        if any(f['height'] == height for f in result):
            continue
        result.append({'url': url, 'width': width, 'height': height})
    return sorted(result, key=lambda f: f['height'], reverse=True)


def analyze_pinterest(url):
    url = resolve_pin(url)
    data = pinterest_data(url)
    if len((data.get('story_pin_data') or {}).get('pages') or []) > 1:
        raise ValueError('Multi-page Pinterest pins are not supported. Paste a single photo or video pin.')
    # Do not mistake video cover images for photo pins.
    if data.get('videos') or data.get('story_pin_data') or (data.get('embed') or {}).get('src'):
        return analyze_video(url, pinterest=True)
    images = image_formats(data)
    if not images:
        raise ValueError('No downloadable photo was found in this pin.')
    return {'id': str(data['id']), 'url': url, 'platform': 'pinterest', 'media_type': 'image',
        'title': str(data.get('title') or data.get('grid_title') or 'Pinterest photo')[:300],
        'channel': str((data.get('closeup_attribution') or {}).get('full_name') or 'Pinterest')[:200],
        'duration': 0, 'thumbnail': images[0]['url'], 'qualities': [f['height'] for f in images],
        'image_formats': images, 'audio_available': False, 'embeddable': False}


def analyze(url):
    if urlparse(url).hostname == 'www.instagram.com':
        info = analyze_instagram(url)
    elif urlparse(url).hostname in ('pin.it', 'www.pinterest.com'):
        info = analyze_pinterest(url)
    else:
        info = analyze_video(url)
    info['_extracted_at'] = time.time()
    return info


def timestamp(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{2,}:\d{2}:\d{2}', value):
        raise ValueError('Use HH:MM:SS for timestamps, for example 00:04:50.')
    h, m, s = map(int, value.split(':'))
    if m > 59 or s > 59:
        raise ValueError('Minutes and seconds must be between 00 and 59.')
    return h * 3600 + m * 60 + s


def clip_range(start, end, duration):
    a, b = timestamp(start), timestamp(end)
    if (a is None) != (b is None):
        raise ValueError('Enter both start and end, or leave both empty for the full video.')
    if a is not None and not (0 <= a < b <= duration):
        raise ValueError('End must be after start and within the video duration.')
    return a, b


def qualities(info):
    formats = info.get('formats') or []
    audio = any(f.get('acodec') not in (None, 'none') and f.get('url') and not f.get('has_drm') for f in formats)
    heights = sorted({int(f['height']) for f in formats if f.get('height') and f.get('vcodec') not in (None, 'none') and f.get('url') and not f.get('has_drm')}, reverse=True)
    return heights, audio


def selector(height, silent=False):
    # Never substitute a lower resolution; silent fallback is enabled only for sources that support it.
    return f'bv[height={height}]+ba/b[height={height}]/bv[height={height}]' if silent else f'bv[height={height}]+ba/b[height={height}][acodec!=none]'


def base_command():
    cmd = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-warnings', '--js-runtimes', 'node', '--socket-timeout', '20', '--retries', '2', '--fragment-retries', '2', '--no-cache-dir', '--no-colors', '--concurrent-fragments', '2' if settings.production else '4']
    if settings.production:
        cmd += ['--use-extractors', 'youtube.*,pinterest,instagram', '--downloader-args', 'ffmpeg:-threads 1', '--postprocessor-args', 'ffmpeg:-threads 1']
    if binary('ffmpeg'):
        cmd += ['--ffmpeg-location', str(Path(binary('ffmpeg')).parent)]
    return cmd


def analyze_video(url, pinterest=False):
    # Capture verbose diagnostics on this single attempt, without adding a second
    # network extraction or enabling HTTP traffic / player / challenge dumps.
    cmd = [arg for arg in base_command() if arg != '--no-warnings']
    cmd += ['--verbose', '--dump-single-json', '--skip-download', url]
    started = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (subprocess.TimeoutExpired, OSError) as exc:
        reason = 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'runtime'
        stderr = getattr(exc, 'stderr', '') or ''
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', errors='replace')
        log_extraction(url, stderr, production=settings.production, returncode=None,
                       elapsed=time.monotonic() - started, reason=reason)
        raise ExtractionError(reason) from None
    stderr = result.stderr or ''
    if result.returncode:
        reason = failure_reason(stderr)
        log_extraction(url, stderr, production=settings.production, returncode=result.returncode,
                       elapsed=time.monotonic() - started, reason=reason)
        raise ExtractionError(reason)
    if any(term in stderr.lower() for term in ('challenge solving failed', 'error solving', 'error running node')):
        log_extraction(url, stderr, production=settings.production, returncode=0,
                       elapsed=time.monotonic() - started)
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        log_extraction(url, stderr, production=settings.production, returncode=0,
                       elapsed=time.monotonic() - started, reason='invalid_metadata')
        raise ExtractionError('invalid_metadata') from None
    return video_info(info, url, platform='pinterest' if pinterest else 'youtube')


def video_info(info, url, platform='youtube'):
    # yt-dlp also returns mhtml storyboards from image hosts. They cannot be
    # selected by our video/audio formats and must not enter download metadata.
    # Remove them rather than widening the production media CDN allowlist.
    info = {**info, 'formats': [f for f in (info.get('formats') or [])
                              if not (f.get('protocol') == 'mhtml' and f.get('ext') == 'mhtml'
                                      and f.get('vcodec') == 'none' and f.get('acodec') == 'none')]}
    if info.get('is_live') or info.get('_type') == 'playlist':
        raise ValueError('Ongoing live streams and playlists are not supported. Try a completed single video.')
    duration = info.get('duration')
    if platform in ('pinterest', 'instagram'):
        formats = info.get('formats') or []
        # Social CDNs often omit dimensions, duration, and codecs. Inspect the actual
        # direct files once during Analyze, then reuse this metadata for Download.
        incomplete = [f for f in formats if f.get('url') and not f.get('has_drm')
                      and (not f.get('height') and f.get('vcodec') != 'none'
                           or f.get('height') and f.get('acodec') in (None, 'unknown'))][:6]
        def inspect_format(f):
            try:
                data = probe(f['url'])
                streams = data.get('streams', [])
                video = next((s for s in streams if s.get('codec_type') == 'video'), {})
                audio = next((s for s in streams if s.get('codec_type') == 'audio'), {})
                if video:
                    f.update(height=video.get('height') or f.get('height'), width=video.get('width') or f.get('width'), vcodec=video.get('codec_name') or 'unknown')
                f['acodec'] = audio.get('codec_name', 'unknown') if audio else 'none'
                return float(data.get('format', {}).get('duration') or 0)
            except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                return 0
        if incomplete:
            with ThreadPoolExecutor(max_workers=3) as inspectors:
                durations = list(inspectors.map(inspect_format, incomplete))
            duration = duration or max(durations, default=0)
        if not duration:
            available = [f for f in formats if f.get('height') and f.get('url') and not f.get('has_drm')]
            if available:
                duration = inspect_format(max(available, key=lambda f: f['height']))
    if not duration or not math.isfinite(duration):
        raise ValueError('This video has no usable duration.')
    validate_duration(duration)
    validate_download_info(info)
    heights, audio = qualities(info)
    if not heights and not audio:
        raise ValueError('No accessible media formats were found for this video.')
    return {'id': info['id'], 'url': url, 'title': str(info.get('title') or 'Untitled video')[:300],
            'channel': str(info.get('channel') or info.get('uploader') or platform.title())[:200],
            'duration': duration, 'platform': platform, 'media_type': 'video',
            'thumbnail': f'https://i.ytimg.com/vi/{info["id"]}/hqdefault.jpg' if platform == 'youtube' else info.get('thumbnail') or next((t['url'] for t in reversed(info.get('thumbnails') or []) if t.get('url')), ''),
            'qualities': heights, 'audio_available': audio,
            'embeddable': platform == 'youtube' and info.get('playable_in_embed', True),
            'item_count': info.get('_tubeclip_item_count', 1), 'item_index': info.get('_tubeclip_item_index', 1),
            '_download_info': info, '_extracted_at': time.time()}


def safe_name(title):
    value = re.sub(r'[^\w\s.()\-]', '', title, flags=re.UNICODE).strip(' .')[:100]
    return value.encode('utf-8')[:150].decode('utf-8', errors='ignore').strip(' .') or 'Video'


def probe(path):
    if urlparse(str(path)).scheme in ('https', 'http', 'file'):
        validate_media_url(str(path))
    result = subprocess.run([binary('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Output could not be inspected with FFprobe.')
    return json.loads(result.stdout)


def process_media(source, output, audio=False, start=None, end=None, runner=None, source_data=None):
    cmd = [binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source)]
    if start is not None:
        cmd += ['-ss', str(start), '-t', str(end - start)]
    if audio:
        cmd += ['-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-q:a', '2']
    elif start is not None:
        cmd += ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']
    else:
        streams = (source_data if source_data is not None else probe(source)).get('streams', [])
        audio_codec = next((s.get('codec_name') for s in streams if s.get('codec_type') == 'audio'), None)
        cmd += ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy', '-c:a', 'copy' if audio_codec in ('aac', 'alac', 'mp3') else 'aac', '-movflags', '+faststart']
        if audio_codec not in ('aac', 'alac', 'mp3'):
            cmd += ['-b:a', '192k']
    if settings.production:
        cmd += ['-threads', '1', '-fs', str(settings.output_bytes)]
    cmd += [str(output)]
    if runner:
        runner(cmd)
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
    return probe(output)


def verify_output(path, audio, height=None, expected_duration=None, require_audio=True, data=None):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('Processing produced an empty file.')
    data = data if data is not None else probe(path)
    streams = data.get('streams', [])
    if (audio or require_audio) and not any(s['codec_type'] == 'audio' for s in streams):
        raise RuntimeError('Output is missing its audio track.')
    videos = [s for s in streams if s['codec_type'] == 'video']
    if audio and videos:
        raise RuntimeError('Audio output unexpectedly contains video.')
    if not audio and (not videos or (height and videos[0].get('height') != height)):
        raise RuntimeError('The requested video resolution was not preserved.')
    if expected_duration and abs(float(data['format']['duration']) - expected_duration) > 1.5:
        raise RuntimeError('The clip duration could not be verified. Please retry.')
    return data
