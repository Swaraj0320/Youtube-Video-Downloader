from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
import importlib.metadata
import json
import logging
import mimetypes
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import httpx
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from . import media
from .config import settings
from .security import RateLimiter, owner_id, file_signature, valid_file_signature

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('tubeclip')
if settings.production:
    logging.getLogger('httpx').setLevel(logging.WARNING)
lock = threading.RLock()
jobs = {}
analyses = {}
analysis_pending = {}
ANALYSIS_CACHE_SECONDS = 120
executor = ThreadPoolExecutor(max_workers=settings.jobs)
analysis_slots = threading.BoundedSemaphore(settings.analyses)
TERMINAL = {'ready', 'failed', 'cancelled'}

PUBLIC_HOSTS = set(settings.allowed_hosts) - {'localhost', '127.0.0.1', 'testserver'}
ALLOWED_HOSTS = settings.allowed_hosts
ALLOWED_ORIGINS = settings.allowed_origins
RETENTION_SECONDS = settings.retention
limiter = RateLimiter()

def request_owner(request):
    return getattr(request.state, 'owner', 'local')

def directory_size(path):
    total = 0
    for p in path.rglob('*'):
        try:
            if not p.is_symlink() and p.is_file():
                total += p.stat().st_size
        except FileNotFoundError:
            pass
    return total

def check_capacity():
    if shutil.disk_usage(media.DOWNLOADS).free < settings.free_bytes:
        raise ValueError('Not enough free server disk space. Please retry later.')
    if settings.storage_bytes and directory_size(media.DOWNLOADS) + directory_size(media.TEMP) >= settings.storage_bytes:
        raise ValueError('Temporary server storage is full. Please wait for expired files to be removed.')

def prune_records():
    now = time.time()
    with lock:
        for key in list(analyses):
            if now - analyses[key]['created'] > settings.analysis_ttl:
                del analyses[key]
        for key in list(jobs):
            job = jobs[key]
            if job['status'] in TERMINAL and now - job.get('finished', job.get('created', now)) > settings.job_ttl:
                if job.get('path') and (settings.production or RETENTION_SECONDS > 0):
                    job['path'].unlink(missing_ok=True)
                del jobs[key]


def cleanup_downloads():
    if RETENTION_SECONDS <= 0:
        return
    cutoff = time.time() - RETENTION_SECONDS
    with lock:
        active_suffixes = {job['id'][:8] for job in jobs.values() if job['status'] not in TERMINAL}
    for path in media.DOWNLOADS.iterdir():
        match = re.search(r' - ([0-9a-f]{8})\.(?:mp4|mp3|mkv|webm|jpg|jpeg|png|webp|gif|avif)$', path.name)
        if not match or match[1] in active_suffixes or path.is_symlink():
            continue
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            log.warning('Could not clean up expired output', exc_info=True)


@asynccontextmanager
async def lifespan(app):
    # Only application-created, stale working directories are removed.
    for path in media.TEMP.glob('job-*'):
        if path.is_dir() and not path.is_symlink() and time.time() - path.stat().st_mtime > (0 if settings.production else 86400):
            shutil.rmtree(path, ignore_errors=True)
    cleanup_downloads()
    prune_records()
    stop_cleanup = threading.Event()
    def clean_periodically():
        while not stop_cleanup.wait(60):
            cleanup_downloads()
            prune_records()
    if RETENTION_SECONDS > 0 or settings.production:
        threading.Thread(target=clean_periodically, daemon=True).start()
    yield
    stop_cleanup.set()
    with lock:
        for job in jobs.values():
            job['cancel'].set()
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='TubeClip', lifespan=lifespan)

# CORS handles allowed preflights; the policy below remains outermost for host validation.
app.add_middleware(CORSMiddleware, allow_origins=sorted(ALLOWED_ORIGINS),
                   allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type', 'X-TubeClip-Client'],
                   expose_headers=['Retry-After', 'Content-Disposition'], allow_credentials=False)


def policy_error(detail, status_code, headers=None, origin=None):
    cors = {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin', 'Access-Control-Expose-Headers': 'Retry-After'} if origin in ALLOWED_ORIGINS else {}
    return JSONResponse({'detail': detail}, status_code=status_code, headers={
        'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff', **cors, **(headers or {})})


@app.middleware('http')
async def request_policy(request: Request, call_next):
    origin = request.headers.get('origin')
    try:
        parsed_host = urlparse('//' + request.headers.get('host', ''))
        host = None if parsed_host.username or parsed_host.password or parsed_host.path or parsed_host.query or parsed_host.fragment else parsed_host.hostname
    except ValueError:
        host = None
    if host not in ALLOWED_HOSTS or (origin and origin not in ALLOWED_ORIGINS):
        return policy_error('This host or request origin is not allowed.', 403, origin=origin)
    if request.method == 'OPTIONS':
        return await call_next(request)
    path = request.url.path
    token = request.headers.get('x-tubeclip-client', '')
    if token and not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        return policy_error('Invalid client identifier.', 400, origin=origin)
    if settings.production and (path in ('/api/analyze', '/api/download') or path.startswith('/api/jobs/')):
        if not token:
            return policy_error('A client identifier is required.', 401, origin=origin)
    request.state.owner = owner_id(token) if token else 'local'
    peer = request.client.host if request.client else 'unknown'
    # Forwarded IP headers are deliberately not trusted. Peer quotas are conservative
    # behind shared proxies; the global budget also applies to clients that rotate IDs.
    retry = limiter.check([f'requests:{peer}', f'client:{request.state.owner}'], settings.requests_per_minute)
    if path in ('/api/analyze', '/api/download') and request.method == 'POST':
        action = path.rsplit('/', 1)[-1]
        maximum = settings.analyses_per_minute if action == 'analyze' else settings.downloads_per_minute
        retry = max(retry, limiter.check([f'{action}:global', f'{action}:{peer}', f'{action}:{request.state.owner}'], maximum))
        if not retry:
            # Buffer only small API JSON bodies, including requests without Content-Length.
            chunks, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > 8192:
                    return policy_error('Request body exceeds 8 KB.', 413, origin=origin)
                chunks.append(chunk)
            request._body = b''.join(chunks)  # Starlette's cached request replays this to the route.
    if retry:
        return policy_error('Request limit reached. Please wait and retry.', 429, {'Retry-After': str(retry)}, origin=origin)
    response = await call_next(request)
    if path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2048)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analysis_id: str = Field(max_length=36)
    quality: str = Field(default='best', max_length=12)
    output: str = Field(default='video', pattern='^(video|mp3|image)$')
    start: str | None = Field(default=None, max_length=16)
    end: str | None = Field(default=None, max_length=16)

def friendly(exc):
    text = str(exc).lower()
    if isinstance(exc, ValueError):
        return str(exc)
    if 'filesize' in text or 'file is larger' in text:
        return 'The source exceeds the configured demo file size limit. Try a smaller source or lower resolution.'
    if 'space' in text:
        return 'Not enough disk space. Free some space and retry.'
    if 'timed out' in text or isinstance(exc, subprocess.TimeoutExpired):
        return 'The operation timed out. Check your connection and retry.'
    if 'format' in text and ('not available' in text or 'requested' in text):
        return 'The selected quality is no longer available. Analyze the link again.'
    if any(t in text for t in ('private', 'unavailable', 'sign in', 'bot', 'restricted', '403', '429', 'removed', 'login', 'log in', 'not granting access', 'empty media response', 'rate-limit')):
        return 'The source did not allow access to this media. It may be restricted, unavailable, or temporarily blocked. Try another public video later.'
    return 'Media processing failed. Check your connection and dependencies, then retry. Details are in the backend log.'


@app.get('/api/health')
def health():
    deps = {name: bool(media.binary(name)) for name in ('ffmpeg', 'ffprobe')}
    deps['node'] = bool(shutil.which('node'))
    versions = {}
    for package in ('yt-dlp', 'yt-dlp-ejs'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    deps['yt_dlp_ejs'] = versions['yt-dlp-ejs']
    healthy = all(deps.values()) and bool(versions['yt-dlp'])
    data = {'status': 'ok' if healthy else 'degraded', 'dependencies': deps, 'yt_dlp': versions['yt-dlp'],
            'hosting_mode': 'hosted' if settings.production else 'local', 'file_retention_seconds': RETENTION_SECONDS}
    return JSONResponse(data, status_code=200 if healthy else 503)


def analysis_response(info, url, owner):
    token = str(uuid4())
    with lock:
        if len(analyses) >= (20 if settings.production else 100):
            del analyses[next(iter(analyses))]
        analyses[token] = {'info': info, 'source_url': url, 'created': time.time(), 'owner': owner}
    return {**{key: value for key, value in info.items() if not key.startswith('_')}, 'analysis_id': token}


@app.post('/api/analyze')
def analyze(body: AnalyzeRequest, request: Request):
    try:
        url = media.source_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    owner = request_owner(request)
    key = (owner, url)
    prune_records()
    with lock:
        for record in list(analyses.values()):
            info = record['info']
            # Keep the extraction timestamp unchanged on hits so signed URLs and
            # metadata cannot be kept alive indefinitely by repeated requests.
            age = time.time() - info.get('_extracted_at', 0)
            if (record['owner'] == owner and record.get('source_url') == url
                    and 0 <= age < min(ANALYSIS_CACHE_SECONDS, settings.analysis_ttl)):
                return analysis_response(info, url, owner)
        pending = analysis_pending.get(key)
        leader = pending is None
        if leader:
            if not analysis_slots.acquire(blocking=False):
                raise HTTPException(429, 'Analysis is busy. Please try again shortly.')
            pending = analysis_pending[key] = Future()
    try:
        if leader:
            info = media.analyze(url)
            media.validate_duration(info.get('duration', 0))
            response = analysis_response(info, url, owner)
            pending.set_result(info)
            return response
        # A second click/tab for this client shares the running extraction.
        return analysis_response(pending.result(timeout=95), url, owner)
    except Exception as exc:
        if leader:
            pending.set_exception(exc)
        log.exception('Analysis failed')
        raise HTTPException(422 if isinstance(exc, ValueError) else 502, friendly(exc))
    finally:
        if leader:
            with lock:
                analysis_pending.pop(key, None)
            analysis_slots.release()

def update(job_id, **values):
    with lock:
        if values.get('status') in TERMINAL:
            values['finished'] = time.time()
        jobs[job_id].update(values)

def run_process(job_id, cmd, on_line=None, timeout=1800):
    event = jobs[job_id]['cancel']
    if event.is_set():
        raise InterruptedError()
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
    lines = queue.Queue()
    def reader():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=reader, daemon=True).start()
    errors = []
    deadline = min(time.monotonic() + timeout, jobs[job_id].get('deadline', float('inf')))
    try:
        while True:
            if event.is_set():
                raise InterruptedError()
            if time.monotonic() > deadline:
                raise TimeoutError('Operation timed out')
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            errors.append(line)
            errors = errors[-30:]
            if on_line:
                on_line(line.strip())
        if process.wait(timeout=5):
            raise RuntimeError(''.join(errors))
    finally:
        if process.poll() is None:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        process.stdout.close()

def download_image(job_id, url, destination, headers=None):
    # Images need no extractor subprocess or conversion: stream the source bytes directly.
    event = jobs[job_id]['cancel']
    limit = min(100 * 1024**2, settings.source_bytes)
    deadline = jobs[job_id].get('deadline', time.monotonic() + settings.timeout)
    downloaded = 0
    began = time.monotonic()
    if event.is_set():
        raise InterruptedError()
    def validate_redirect(request):
        media.validate_media_url(str(request.url))
    with httpx.Client(follow_redirects=True, max_redirects=3, timeout=20, event_hooks={'request': [validate_redirect]}) as client:
        with client.stream('GET', url, headers=headers or {}) as response:
            response.raise_for_status()
            total = int(response.headers.get('content-length') or 0)
            if total > limit:
                raise ValueError(f'This photo exceeds the {limit // 1024**2} MB demo download limit.')
            with destination.open('wb') as target:
                for chunk in response.iter_bytes(64 * 1024):
                    if event.is_set():
                        raise InterruptedError()
                    if time.monotonic() > deadline:
                        raise TimeoutError('Image download timed out')
                    downloaded += len(chunk)
                    if downloaded > limit:
                        raise ValueError(f'This photo exceeds the {limit // 1024**2} MB demo download limit.')
                    target.write(chunk)
                    update(job_id, percent=min(99.9, downloaded * 100 / total) if total else None,
                           speed=downloaded / max(.001, time.monotonic() - began))
    if not downloaded:
        raise RuntimeError('The source returned an empty photo.')

def enforce_output_limit(job_id, output):
    if jobs[job_id].get('limit_error'):
        raise ValueError(jobs[job_id]['limit_error'])
    if output.stat().st_size > settings.output_bytes:
        raise ValueError('The output exceeds the demo file size limit.')
    # yt-dlp can preserve an old CDN modification date; retention starts at completion.
    os.utime(output, None)

def watch_job(job_id, work, stopped):
    while not stopped.wait(.25):
        job = jobs.get(job_id)
        if not job or job['status'] in TERMINAL:
            return
        reason = None
        try:
            if time.monotonic() > job['deadline']:
                reason = 'The demo processing time limit was reached. Try a shorter clip or lower resolution.'
            elif directory_size(work) > settings.source_bytes * 2:
                reason = 'The source exceeds the demo working-file limit.'
            else:
                check_capacity()
        except (ValueError, OSError) as exc:
            reason = str(exc)
        if reason:
            job['limit_error'] = reason
            job['cancel'].set()
            return

def worker(job_id, info, height, audio, start, end):
    work = media.TEMP / f'job-{job_id}'
    output = None
    stop_watch = threading.Event()
    try:
        work.mkdir()
        check_capacity()
        media.validate_duration(info.get('duration', 0))
        jobs[job_id].setdefault('deadline', time.monotonic() + settings.timeout)
        watchdog = threading.Thread(target=watch_job, args=(job_id, work, stop_watch), daemon=True)
        watchdog.start()
        update(job_id, phase='Preparing', status='processing')
        source = []
        def progress(line):
            if line.startswith('TCFILE:'):
                source.append(Path(line[7:]))
            elif line.startswith('TCPROGRESS:'):
                try:
                    data = json.loads(line[11:])
                    total = data.get('total_bytes') or data.get('total_bytes_estimate')
                    downloaded = data.get('downloaded_bytes')
                    percent = min(99.9, 100 * downloaded / total) if total and downloaded is not None else None
                    update(job_id, phase='Downloading', status='downloading', percent=percent, speed=data.get('speed'), eta=data.get('eta'))
                except (ValueError, TypeError):
                    pass
            elif line.startswith('[Merger]'):
                update(job_id, phase='Merging audio and video', status='processing', percent=None)
            elif line.startswith('[Video') or line.startswith('[Fixup'):
                update(job_id, phase='Trimming clip' if start is not None else 'Processing media', status='processing', percent=None)
        image = info.get('media_type') == 'image'
        update(job_id, phase='Downloading', status='downloading')
        if image:
            selected = next(f for f in info['image_formats'] if f['height'] == height)
            ext = Path(urlparse(selected['url']).path).suffix.lstrip('.').lower()
            if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'avif'):
                raise ValueError('Unsupported image format.')
            source.append(work / f'source.{ext}')
            download_image(job_id, selected['url'], source[-1], info.get('_http_headers'))
        else:
            cmd = media.base_command() + ['--newline', '--progress', '--progress-template', 'download:TCPROGRESS:%(progress)j', '--print', 'after_move:TCFILE:%(filepath)s', '--no-simulate', '--max-filesize', str(settings.source_bytes), '--format', 'bestaudio/best[acodec!=none]' if audio else media.selector(height, silent=info.get('platform') in ('pinterest', 'instagram')), '--merge-output-format', 'mkv', '--output', str(work / 'source.%(ext)s')]
            if start is not None:
                cmd += ['--download-sections', f'*{start}-{end}', '--force-keyframes-at-cuts']
            cached = info.get('_download_info')
            if cached and time.time() - info.get('_extracted_at', 0) > 600:
                cached = media.analyze(info['url']).get('_download_info')
            metadata = work / 'media.json'
            if cached:
                media.validate_download_info(cached)
                metadata.write_text(json.dumps(cached))
                command = cmd + ['--load-info-json', str(metadata)]
            else:
                command = cmd + [info['url']]
            try:
                run_process(job_id, command, progress)
            except RuntimeError as exc:
                if not cached or not any(term in str(exc).lower() for term in ('403', '410', 'expired', 'signature')):
                    raise
                # Signed CDN URLs can expire. Refresh once rather than repeating extraction on every click.
                update(job_id, phase='Refreshing source link', percent=None)
                refreshed = media.analyze(info['url']).get('_download_info')
                if not refreshed:
                    raise
                media.validate_download_info(refreshed)
                metadata.write_text(json.dumps(refreshed))
                for partial in work.glob('source.*'):
                    partial.unlink(missing_ok=True)
                source.clear()
                run_process(job_id, cmd + ['--load-info-json', str(metadata)], progress)
        if not source or not source[-1].is_file() or work.resolve() not in source[-1].resolve().parents:
            raise RuntimeError('Downloaded source file was not found.')
        if settings.production and source[-1].stat().st_size > settings.source_bytes:
            raise ValueError('The downloaded source exceeds the demo source-file limit.')
        if image:
            if jobs[job_id].get('limit_error'):
                raise ValueError(jobs[job_id]['limit_error'])
            output = media.DOWNLOADS / f'{media.safe_name(info["title"])} - {height}p - {job_id[:8]}.{ext}'
            shutil.move(source[-1], output)
            data = media.probe(output)
            stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), {})
            if stream.get('height') != height or stream.get('width') != selected['width']:
                raise RuntimeError('The requested photo resolution could not be verified.')
            if jobs[job_id]['cancel'].is_set():
                raise InterruptedError()
            enforce_output_limit(job_id, output)
            update(job_id, status='ready', phase='Ready', percent=100, filename=output.name, path=output, size=output.stat().st_size)
            return
        tag = 'Audio' if audio else f'{height}p'
        if start is not None:
            def stamp(s):
                return f'{s//3600:02d}-{s//60%60:02d}-{s%60:02d}'
            tag += f' - {stamp(start)} to {stamp(end)}'
        filename = f'{media.safe_name(info["title"])} - {tag} - {job_id[:8]}.{"mp3" if audio else "mp4"}'
        output = media.DOWNLOADS / filename
        update(job_id, phase='Converting audio' if audio else 'Finalizing video', status='processing', percent=None)
        source_data = media.probe(source[-1])
        audio_codec = next((s.get('codec_name') for s in source_data.get('streams', []) if s.get('codec_type') == 'audio'), None)
        if not audio and source[-1].suffix.lower() == '.mp4' and audio_codec in (None, 'aac', 'alac', 'mp3'):
            # A completed compatible MP4 is already the requested output. Renaming is immediate.
            shutil.move(source[-1], output)
            output_data = source_data
        else:
            try:
                output_data = media.process_media(source[-1], output, audio=audio, source_data=source_data,
                                                  runner=lambda c: run_process(job_id, c))
            except RuntimeError:
                if audio:
                    raise
                output.unlink(missing_ok=True)
                output = output.with_suffix(source[-1].suffix)
                shutil.move(source[-1], output)
                output_data = source_data
        update(job_id, phase='Verifying output', percent=None)
        media.verify_output(output, audio, height, end-start if start is not None else info['duration'],
                            require_audio=info.get('audio_available', True), data=output_data)
        if jobs[job_id]['cancel'].is_set():
            raise InterruptedError()
        enforce_output_limit(job_id, output)
        update(job_id, status='ready', phase='Ready', percent=100, filename=output.name, path=output, size=output.stat().st_size)
    except InterruptedError:
        if output:
            output.unlink(missing_ok=True)
        reason = jobs[job_id].get('limit_error')
        update(job_id, status='failed' if reason else 'cancelled', phase='Failed' if reason else 'Cancelled', percent=None, **({'error': reason} if reason else {}))
    except Exception as exc:
        log.exception('Job %s failed', job_id)
        if output:
            output.unlink(missing_ok=True)
        update(job_id, status='failed', phase='Failed', percent=None, error=friendly(exc))
    finally:
        stop_watch.set()
        shutil.rmtree(work, ignore_errors=True)


@app.post('/api/download', status_code=202)
def download(body: DownloadRequest, request: Request):
    prune_records()
    if not all(media.binary(b) for b in ('ffmpeg', 'ffprobe')):
        raise HTTPException(503, 'FFmpeg or FFprobe is missing. Run ./setup.sh.')
    with lock:
        cached = analyses.get(body.analysis_id)
        if not cached or time.time() - cached['created'] > settings.analysis_ttl:
            raise HTTPException(422, 'This analysis expired. Analyze the link again.')
        if cached.get('owner', 'local') != request_owner(request):
            raise HTTPException(404, 'Analysis not found.')
        info = cached['info']
        try:
            check_capacity()
            image = info.get('media_type') == 'image'
            if image and (body.output != 'image' or body.start or body.end):
                raise ValueError('Photos support image downloads only, without timestamps.')
            if not image and body.output == 'image':
                raise ValueError('Choose video or MP3 for this source.')
            start, end = media.clip_range(body.start, body.end, info['duration'])
            if start is not None and settings.max_clip and end - start > settings.max_clip:
                raise ValueError(f'Clips are limited to {settings.max_clip} seconds on this demo.')
            media.validate_duration(info['duration'])
            if body.output == 'mp3' and not info['audio_available']:
                raise ValueError('This video has no accessible audio stream.')
            height = max(info['qualities']) if body.quality == 'best' and info['qualities'] else int(body.quality) if body.quality.isdigit() else None
            if body.output in ('video', 'image') and height not in info['qualities']:
                raise ValueError('Choose an available media quality.')
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        selection_key = (info['url'], None if body.output == 'mp3' else height, body.output, start, end)
        for previous in jobs.values():
            if previous.get('owner', 'local') == request_owner(request) and previous.get('selection_key') == selection_key and previous['status'] == 'ready' and previous.get('path') and previous['path'].is_file():
                return {'job_id': previous['id']}
        if sum(j['status'] not in TERMINAL for j in jobs.values()) >= settings.jobs:
            raise HTTPException(429, 'The download engine is busy. Wait for the running job to finish.')
        # Bound in-memory history; completed files remain on disk.
        if len(jobs) >= (50 if settings.production else 200):
            for key in list(jobs):
                if jobs[key]['status'] in TERMINAL:
                    del jobs[key]
                    break
        retry = limiter.check(['downloads:daily:global'], settings.downloads_per_day, window=86400)
        if retry:
            raise HTTPException(429, 'The daily demo download budget has been reached.', headers={'Retry-After': str(retry)})
        job_id = str(uuid4())
        jobs[job_id] = {'id': job_id, 'status': 'queued', 'phase': 'Preparing', 'percent': None, 'cancel': threading.Event(), 'selection_key': selection_key, 'owner': request_owner(request), 'created': time.time(), 'deadline': time.monotonic() + settings.timeout}
        executor.submit(worker, job_id, info.copy(), height, body.output == 'mp3', start, end)
    return {'job_id': job_id}

def get_job(job_id, request=None):
    prune_records()
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download job not found. Files from previous sessions remain in downloads/.')
        if request is not None and job.get('owner', 'local') != request_owner(request):
            raise HTTPException(404, 'Download job not found.')
        return job.copy()


@app.get('/api/jobs/{job_id}')
def status(job_id: str, request: Request):
    job = get_job(job_id, request)
    if job['status'] == 'ready' and (not job.get('path') or not job['path'].is_file()):
        update(job_id, status='failed', phase='Expired', error='This file has expired. Analyze the link again.')
        job = get_job(job_id, request)
    public = {k: v for k, v in job.items() if k not in ('cancel', 'path', 'selection_key', 'owner', 'deadline', 'limit_error')}
    if job['status'] == 'ready':
        expires = int(min(time.time() + 300, job.get('finished', time.time()) + settings.job_ttl))
        token = file_signature(job_id, job.get('owner', 'local'), expires)
        public['download_url'] = f'/api/files/{job_id}?expires={expires}&token={token}'
    return public


@app.delete('/api/jobs/{job_id}')
def cancel(job_id: str, request: Request):
    job = get_job(job_id, request)
    if job['status'] not in TERMINAL:
        job['cancel'].set()
    return {'message': 'Cancellation requested'}


@app.get('/api/files/{job_id}')
def file(job_id: str, request: Request, expires: int = 0, token: str = ''):
    job = get_job(job_id)
    if settings.production or token:
        if not valid_file_signature(job_id, job.get('owner', 'local'), expires, token):
            raise HTTPException(403, 'Download link expired or invalid. Refresh the job status and retry.')
    elif job.get('owner', 'local') != request_owner(request):
        raise HTTPException(404, 'Download job not found.')
    if job['status'] != 'ready':
        raise HTTPException(409, 'Your file is not ready yet.')
    retry = limiter.check(['files:daily:global'], settings.downloads_per_day * 2, window=86400)
    if retry:
        raise HTTPException(429, 'The daily demo file-transfer budget has been reached.', headers={'Retry-After': str(retry)})
    path = job['path'].resolve()
    if path.parent != media.DOWNLOADS.resolve() or not path.is_file():
        raise HTTPException(404, 'The downloaded file has expired or is no longer available.')
    return FileResponse(path, filename=job['filename'], media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
