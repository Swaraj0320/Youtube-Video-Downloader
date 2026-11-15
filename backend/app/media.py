import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS = ROOT / 'downloads'
TEMP = ROOT / 'temp'
for directory in (DOWNLOADS, TEMP):
    directory.mkdir(exist_ok=True)


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


def selector(height):
    # Never substitute a lower resolution; combined fallback must contain audio.
    return f'bv[height={height}]+ba/b[height={height}][acodec!=none]'


def base_command():
    cmd = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-warnings', '--js-runtimes', 'node', '--socket-timeout', '20', '--retries', '2', '--fragment-retries', '2', '--no-cache-dir', '--no-colors']
    if binary('ffmpeg'):
        cmd += ['--ffmpeg-location', str(Path(binary('ffmpeg')).parent)]
    return cmd


def analyze(url):
    result = subprocess.run(base_command() + ['--dump-single-json', '--skip-download', url], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    info = json.loads(result.stdout)
    if info.get('is_live') or info.get('_type') == 'playlist':
        raise ValueError('Ongoing live streams and playlists are not supported. Try a completed single video.')
    duration = info.get('duration')
    if not duration or not math.isfinite(duration):
        raise ValueError('This video has no usable duration.')
    heights, audio = qualities(info)
    if not heights and not audio:
        raise ValueError('No accessible media formats were found for this video.')
    return {'id': info['id'], 'url': url, 'title': str(info.get('title') or 'Untitled video')[:300], 'channel': str(info.get('channel') or info.get('uploader') or 'YouTube')[:200], 'duration': duration, 'thumbnail': f'https://i.ytimg.com/vi/{info["id"]}/hqdefault.jpg', 'qualities': heights, 'audio_available': audio, 'embeddable': info.get('playable_in_embed', True)}


def safe_name(title):
    value = re.sub(r'[^\w\s.()\-]', '', title, flags=re.UNICODE).strip(' .')[:100]
    return value.encode('utf-8')[:150].decode('utf-8', errors='ignore').strip(' .') or 'Video'


def probe(path):
    result = subprocess.run([binary('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Output could not be inspected with FFprobe.')
    return json.loads(result.stdout)


def process_media(source, output, audio=False, start=None, end=None, runner=None):
    cmd = [binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source)]
    if start is not None:
        cmd += ['-ss', str(start), '-t', str(end - start)]
    if audio:
        cmd += ['-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-q:a', '2']
    elif start is not None:
        cmd += ['-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']
    else:
        streams = probe(source).get('streams', [])
        audio_codec = next((s.get('codec_name') for s in streams if s.get('codec_type') == 'audio'), None)
        cmd += ['-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy', '-c:a', 'copy' if audio_codec in ('aac', 'alac', 'mp3') else 'aac', '-movflags', '+faststart']
        if audio_codec not in ('aac', 'alac', 'mp3'):
            cmd += ['-b:a', '192k']
    cmd += [str(output)]
    if runner:
        runner(cmd)
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
    return probe(output)


def verify_output(path, audio, height=None, expected_duration=None):
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('Processing produced an empty file.')
    data = probe(path)
    streams = data.get('streams', [])
    if not any(s['codec_type'] == 'audio' for s in streams):
        raise RuntimeError('Output is missing its audio track.')
    videos = [s for s in streams if s['codec_type'] == 'video']
    if audio and videos:
        raise RuntimeError('Audio output unexpectedly contains video.')
    if not audio and (not videos or (height and videos[0].get('height') != height)):
        raise RuntimeError('The requested video resolution was not preserved.')
    if expected_duration and abs(float(data['format']['duration']) - expected_duration) > 1.5:
        raise RuntimeError('The clip duration could not be verified. Please retry.')
    return data
