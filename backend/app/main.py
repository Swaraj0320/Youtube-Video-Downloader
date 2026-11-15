from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from . import media

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('tubeclip')
lock = threading.RLock()
jobs = {}
analyses = {}
executor = ThreadPoolExecutor(max_workers=2)
analysis_slots = threading.BoundedSemaphore(2)
TERMINAL = {'ready', 'failed', 'cancelled'}


@asynccontextmanager
async def lifespan(app):
    # Only application-created, stale working directories are removed.
    for path in media.TEMP.glob('job-*'):
        if path.is_dir() and not path.is_symlink() and time.time() - path.stat().st_mtime > 86400:
            shutil.rmtree(path, ignore_errors=True)
    yield
    with lock:
        for job in jobs.values():
            job['cancel'].set()
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='TubeClip', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'], allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    host = request.headers.get('host', '').split(':')[0]
    if host not in ('127.0.0.1', 'localhost', 'testserver') or (origin and origin not in ('http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8000')):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Only local application requests are allowed.'}, status_code=403)
    return await call_next(request)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2048)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analysis_id: str = Field(max_length=36)
    quality: str = Field(default='best', max_length=12)
    output: str = Field(default='video', pattern='^(video|mp3)$')
    start: str | None = Field(default=None, max_length=16)
    end: str | None = Field(default=None, max_length=16)


def friendly(exc):
    text = str(exc).lower()
    if isinstance(exc, ValueError):
        return str(exc)
    if 'space' in text:
        return 'Not enough disk space. Free some space and retry.'
    if 'timed out' in text or isinstance(exc, subprocess.TimeoutExpired):
        return 'The operation timed out. Check your connection and retry.'
    if 'format' in text and ('not available' in text or 'requested' in text):
        return 'The selected quality is no longer available. Analyze the video again.'
    if any(t in text for t in ('private', 'unavailable', 'sign in', 'bot', 'restricted', '403', '429', 'removed')):
        return 'YouTube did not allow access to this video. It may be restricted, unavailable, or temporarily blocked. Try another public video later.'
    return 'Media processing failed. Check your connection and dependencies, then retry. Details are in the backend log.'


@app.get('/api/health')
def health():
    deps = {name: bool(media.binary(name)) for name in ('ffmpeg', 'ffprobe')}
    deps['node'] = bool(shutil.which('node'))
    deps['yt_dlp_ejs'] = importlib.metadata.version('yt-dlp-ejs')
    return {'status': 'ok' if all(deps.values()) else 'degraded', 'dependencies': deps, 'yt_dlp': importlib.metadata.version('yt-dlp')}


@app.post('/api/analyze')
def analyze(body: AnalyzeRequest):
    try:
        url = media.youtube_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not analysis_slots.acquire(blocking=False):
        raise HTTPException(429, 'Analysis is busy. Please try again shortly.')
    try:
        info = media.analyze(url)
        token = str(uuid4())
        with lock:
            now = time.time()
            for key in list(analyses):
                if now - analyses[key]['created'] > 3600:
                    del analyses[key]
            if len(analyses) >= 100:
                del analyses[next(iter(analyses))]
            analyses[token] = {'info': info, 'created': now}
        return {**info, 'analysis_id': token}
    except Exception as exc:
        log.exception('Analysis failed')
        raise HTTPException(422 if isinstance(exc, ValueError) else 502, friendly(exc))
    finally:
        analysis_slots.release()


def update(job_id, **values):
    with lock:
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
    deadline = time.monotonic() + timeout
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


def worker(job_id, info, height, audio, start, end):
    work = media.TEMP / f'job-{job_id}'
    output = None
    try:
        work.mkdir()
        if shutil.disk_usage(media.ROOT).free < 512 * 1024**2:
            raise ValueError('Not enough disk space. At least 512 MB free is required.')
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
        cmd = media.base_command() + ['--newline', '--progress', '--progress-template', 'download:TCPROGRESS:%(progress)j', '--print', 'after_move:TCFILE:%(filepath)s', '--no-simulate', '--max-filesize', '20G', '--format', 'bestaudio/best[acodec!=none]' if audio else media.selector(height), '--merge-output-format', 'mkv', '--output', str(work / 'source.%(ext)s')]
        if start is not None:
            cmd += ['--download-sections', f'*{start}-{end}', '--force-keyframes-at-cuts']
        cmd += [info['url']]
        update(job_id, phase='Downloading', status='downloading')
        run_process(job_id, cmd, progress)
        if not source or not source[-1].is_file() or work.resolve() not in source[-1].resolve().parents:
            raise RuntimeError('Downloaded source file was not found.')
        tag = 'Audio' if audio else f'{height}p'
        if start is not None:
            def stamp(s):
                return f'{s//3600:02d}-{s//60%60:02d}-{s%60:02d}'
            tag += f' - {stamp(start)} to {stamp(end)}'
        filename = f'{media.safe_name(info["title"])} - {tag} - {job_id[:8]}.{"mp3" if audio else "mp4"}'
        output = media.DOWNLOADS / filename
        update(job_id, phase='Converting audio' if audio else 'Finalizing video', status='processing', percent=None)
        try:
            media.process_media(source[-1], output, audio=audio, runner=lambda c: run_process(job_id, c))
        except RuntimeError:
            # Preserve streams losslessly in MKV if their codecs cannot be remuxed to MP4.
            if audio:
                raise
            output.unlink(missing_ok=True)
            output = output.with_suffix('.mkv')
            shutil.copyfile(source[-1], output)
        update(job_id, phase='Verifying output', percent=None)
        media.verify_output(output, audio, height, end-start if start is not None else info['duration'])
        if jobs[job_id]['cancel'].is_set():
            raise InterruptedError()
        update(job_id, status='ready', phase='Ready', percent=100, filename=output.name, path=output, size=output.stat().st_size)
    except InterruptedError:
        if output:
            output.unlink(missing_ok=True)
        update(job_id, status='cancelled', phase='Cancelled', percent=None)
    except Exception as exc:
        log.exception('Job %s failed', job_id)
        if output:
            output.unlink(missing_ok=True)
        update(job_id, status='failed', phase='Failed', percent=None, error=friendly(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post('/api/download', status_code=202)
def download(body: DownloadRequest):
    if not all(media.binary(b) for b in ('ffmpeg', 'ffprobe')):
        raise HTTPException(503, 'FFmpeg or FFprobe is missing. Run ./setup.sh.')
    with lock:
        cached = analyses.get(body.analysis_id)
        if not cached or time.time() - cached['created'] > 3600:
            raise HTTPException(422, 'This analysis expired. Analyze the video again.')
        info = cached['info']
        try:
            start, end = media.clip_range(body.start, body.end, info['duration'])
            if not info['audio_available']:
                raise ValueError('This video has no accessible audio stream.')
            height = max(info['qualities']) if body.quality == 'best' and info['qualities'] else int(body.quality) if body.quality.isdigit() else None
            if body.output == 'video' and height not in info['qualities']:
                raise ValueError('Choose an available video quality.')
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if sum(j['status'] not in TERMINAL for j in jobs.values()) >= 2:
            raise HTTPException(429, 'Two downloads are already running. Wait for one to finish.')
        # Bound in-memory history; completed files remain on disk.
        if len(jobs) >= 200:
            for key in list(jobs):
                if jobs[key]['status'] in TERMINAL:
                    del jobs[key]
                    break
        job_id = str(uuid4())
        jobs[job_id] = {'id': job_id, 'status': 'queued', 'phase': 'Preparing', 'percent': None, 'cancel': threading.Event()}
        executor.submit(worker, job_id, info.copy(), height, body.output == 'mp3', start, end)
    return {'job_id': job_id}


def get_job(job_id):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, 'Download job not found. Files from previous sessions remain in downloads/.')
        return job.copy()


@app.get('/api/jobs/{job_id}')
def status(job_id: str):
    return {k: v for k, v in get_job(job_id).items() if k not in ('cancel', 'path')}


@app.delete('/api/jobs/{job_id}')
def cancel(job_id: str):
    job = get_job(job_id)
    if job['status'] not in TERMINAL:
        job['cancel'].set()
    return {'message': 'Cancellation requested'}


@app.get('/api/files/{job_id}')
def file(job_id: str):
    job = get_job(job_id)
    if job['status'] != 'ready':
        raise HTTPException(409, 'Your file is not ready yet.')
    path = job['path'].resolve()
    if path.parent != media.DOWNLOADS.resolve() or not path.is_file():
        raise HTTPException(404, 'The downloaded file is no longer available.')
    return FileResponse(path, filename=job['filename'], media_type='audio/mpeg' if path.suffix == '.mp3' else 'video/mp4' if path.suffix == '.mp4' else 'video/x-matroska')
