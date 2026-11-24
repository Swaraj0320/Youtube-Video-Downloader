from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
from uuid import uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from yt_dlp import YoutubeDL
from app import main, media


@pytest.mark.parametrize('url', ['https://youtu.be/aqz-KE-bpKQ?t=4','https://www.youtube.com/watch?v=aqz-KE-bpKQ&feature=share','https://youtube.com/shorts/aqz-KE-bpKQ','https://youtube.com/live/aqz-KE-bpKQ','https://youtu.be/aqz-KE-bpKQ?list=ABC','https://youtube.com/watch?v=aqz-KE-bpKQ&list=ABC&index=2'])
def test_urls(url):
    assert media.youtube_url(url) == 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'


@pytest.mark.parametrize('url', ['file:///etc/passwd','https://youtube.com.evil.test/watch?v=aqz-KE-bpKQ','https://youtube.com@evil.test/watch?v=aqz-KE-bpKQ','https://youtube.com/watch?v=short','https://youtube.com/playlist?list=ABC','https://youtube.com/watch?list=ABC','https://127.0.0.1/video','https://youtube.com:123/watch?v=aqz-KE-bpKQ'])
def test_bad_urls(url):
    with pytest.raises(ValueError): media.youtube_url(url)


def test_timestamps():
    assert media.clip_range('', '', 120) == (None,None)
    assert media.clip_range('00:00:02','00:00:05',8) == (2,5)
    assert media.timestamp('01:04:50') == 3890
    for a,b in [('00:00:01',''),('00:00:05','00:00:02'),('-00:00:01','00:00:02'),('00:00:01','00:00:09'),('00:70:01','00:00:02'),('1:02','00:00:03')]:
        with pytest.raises(ValueError): media.clip_range(a,b,8)


def test_real_quality_logic():
    formats=[{'format_id':'v4k','height':2160,'vcodec':'vp9','acodec':'none','url':'https://example.test/v'}, {'format_id':'a','vcodec':'none','acodec':'opus','url':'https://example.test/a'}, {'format_id':'b','height':720,'vcodec':'h264','acodec':'aac','url':'https://example.test/b'}, {'height':4320,'vcodec':'vp9','has_drm':True,'url':'https://example.test/drm'}]
    assert media.qualities({'formats':formats}) == ([2160,720],True)
    for f in formats: f['ext']='webm'
    with YoutubeDL({'quiet':True}) as ydl:
        selected=list(ydl.build_format_selector(media.selector(2160))({'formats': formats[:3], 'has_merged_format': True, 'incomplete_formats': False}))
        assert [f['format_id'] for f in selected[0]['requested_formats']] == ['v4k','a']
        assert not list(ydl.build_format_selector(media.selector(1080))({'formats':formats[:3], 'has_merged_format':True, 'incomplete_formats':False}))


def test_names():
    assert '/' not in media.safe_name('../../evil/<name>:"test"')
    assert media.safe_name('...') == 'Video'
    assert len(media.safe_name('वीडियो'*100).encode('utf-8')) <= 150


@pytest.fixture(scope='module')
def sample(tmp_path_factory):
    root=tmp_path_factory.mktemp('media')
    source=root/'sample.mp4'
    subprocess.run([media.binary('ffmpeg'),'-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=25','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','8','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(source)],check=True)
    return root,source


def test_precise_clip_and_mp3(sample):
    root,source=sample
    for audio in (True,False):
        output=root/('clip.mp3' if audio else 'clip.mp4')
        media.process_media(source,output,audio=audio,start=2,end=5)
        data=media.verify_output(output,audio,180 if not audio else None,3)
        assert abs(float(data['format']['duration'])-3)<.15
    full=root/'full.mp3'
    media.process_media(source,full,audio=True)
    assert abs(float(media.verify_output(full,True,expected_duration=8)['format']['duration'])-8)<.15


def test_separate_stream_merge(sample):
    root,source=sample
    video=root/'video.mp4';audio=root/'audio.m4a';merged=root/'merged.mp4'
    for path,flag in ((video,'-an'),(audio,'-vn')):
        subprocess.run([media.binary('ffmpeg'),'-v','error','-i',str(source),flag,'-c','copy',str(path)],check=True)
    subprocess.run([media.binary('ffmpeg'),'-v','error','-i',str(video),'-i',str(audio),'-map','0:v:0','-map','1:a:0','-c','copy',str(merged)],check=True)
    data=media.verify_output(merged,False,180,8)
    assert {s['codec_type'] for s in data['streams']} == {'audio','video'}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, 'analyses', {})
    return TestClient(main.app)


def test_api_errors(client,monkeypatch):
    assert client.get('/api/health').json()['status']=='ok'
    assert client.post('/api/analyze',json={'url':'https://evil.test/video'}).status_code==422
    assert client.post('/api/analyze',json={'url':'https://youtu.be/aqz-KE-bpKQ'},headers={'Origin':'https://evil.test'}).status_code==403
    assert client.get('/api/jobs/missing').status_code==404
    assert client.get('/api/files/missing').status_code==404
    monkeypatch.setattr(media,'analyze',lambda u: (_ for _ in ()).throw(RuntimeError('Video is private')))
    response=client.post('/api/analyze',json={'url':'https://youtu.be/aqz-KE-bpKQ'})
    assert response.status_code==502 and 'did not allow' in response.json()['detail']


def test_analysis_download_validation(client,monkeypatch):
    info={'id':'aqz-KE-bpKQ','url':'https://youtu.be/aqz-KE-bpKQ','title':'Sample','duration':8,'qualities':[180], 'audio_available':True}
    monkeypatch.setattr(media,'analyze',lambda u: info)
    token=client.post('/api/analyze',json={'url':info['url']}).json()['analysis_id']
    for body in ({'quality':'2160'},{'start':'00:00:01'}, {'start':'00:00:05','end':'00:00:03'}):
        assert client.post('/api/download',json={'analysis_id':token,**body}).status_code==422
    monkeypatch.setattr(media,'binary',lambda n:None)
    assert client.post('/api/download',json={'analysis_id':token}).status_code==503


@pytest.mark.parametrize('audio,clip',[(False,False),(False,True),(True,False),(True,True)])
def test_real_job_pipeline(sample,tmp_path,monkeypatch,client,audio,clip):
    root,source=sample
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(root)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    downloads=tmp_path/'downloads';downloads.mkdir()
    temp=tmp_path/'temp';temp.mkdir()
    monkeypatch.setattr(media,'DOWNLOADS',downloads);monkeypatch.setattr(media,'TEMP',temp)
    job_id=str(uuid4())
    main.jobs[job_id]={'id':job_id,'status':'queued','cancel':threading.Event()}
    info={'title':'Test / video','url':f'http://127.0.0.1:{server.server_port}/sample.mp4','duration':8}
    metadata=tmp_path/'info.json'
    metadata.write_text(json.dumps({'id':'local-test','title':'Test / video','duration':8,'extractor':'generic','webpage_url':info['url'],'formats':[{'format_id':'combined','url':info['url'],'ext':'mp4','protocol':'http','height':180,'width':320,'vcodec':'h264','acodec':'aac'}]}))
    info['_download_info'] = json.loads(metadata.read_text())
    info['_extracted_at'] = main.time.time()
    monkeypatch.setattr(media, 'analyze', lambda url: pytest.fail('Fresh cached metadata must skip extraction'))
    if not audio:
        monkeypatch.setattr(media, 'process_media', lambda *a, **k: pytest.fail('Compatible MP4 must skip conversion'))
    try:
        main.worker(job_id,info,180,audio,2 if clip else None,5 if clip else None)
        job=main.jobs[job_id]
        assert job['status']=='ready',job
        assert abs(float(media.probe(job['path'])['format']['duration'])-(3 if clip else 8))<.2
        response=client.get(f'/api/files/{job_id}')
        assert response.status_code==200 and len(response.content)>1000
        assert 'attachment' in response.headers['content-disposition']
        status=client.get(f'/api/jobs/{job_id}').json()
        assert 'path' not in status and 'cancel' not in status
        assert not list(temp.iterdir())
    finally:
        server.shutdown();server.server_close()
        main.jobs.pop(job_id,None)


def test_cancel_process():
    import sys
    job_id=str(uuid4())
    event=threading.Event();event.set()
    main.jobs[job_id]={'cancel':event,'status':'queued'}
    with pytest.raises(InterruptedError):main.run_process(job_id,[sys.executable,'-c','print(1)'])
    main.jobs.pop(job_id)


def test_mp4_audio_compatibility(sample):
    root,source=sample
    unusual=root/'vorbis.mkv';output=root/'compatible.mp4'
    subprocess.run([media.binary('ffmpeg'),'-v','error','-i',str(source),'-c:v','copy','-c:a','libvorbis',str(unusual)],check=True)
    data=media.process_media(unusual,output)
    assert next(s for s in data['streams'] if s['codec_type']=='audio')['codec_name']=='aac'
    media.verify_output(output,False,180,8)


def test_radio_link_is_analyzed_as_one_video(client,monkeypatch):
    canonical='https://www.youtube.com/watch?v=NoHCQEUja4Y'
    def extract(url):
        assert url == canonical
        return {'id':'NoHCQEUja4Y','url':url,'title':'Video','duration':10,'qualities':[720],'audio_available':True}
    monkeypatch.setattr(media,'analyze',extract)
    response=client.post('/api/analyze',json={'url':canonical+'&list=RDNoHCQEUja4Y&start_radio=1'})
    assert response.status_code==200
    assert response.json()['url']==canonical


@pytest.mark.parametrize('url', [
    'https://www.pinterest.com/pin/664281013778109217/?share=1',
    'https://in.pinterest.com/pin/664281013778109217/',
    'https://pinterest.co.uk/pin/origami--664281013778109217/',
])
def test_pinterest_urls(url):
    assert media.source_url(url) == 'https://www.pinterest.com/pin/664281013778109217/'
    assert media.source_url('https://pin.it/AbC123') == 'https://pin.it/AbC123'


@pytest.mark.parametrize('url', [
    'https://pinterest.com.evil.test/pin/123/', 'https://pinterest.com@evil.test/pin/123/',
    'https://pinterest.com:8080/pin/123/', 'https://pinterest.com/user/board/',
    'https://pin.it/../secret', 'https://pinterest.fake/pin/123/',
])
def test_invalid_pinterest_urls(url):
    with pytest.raises(ValueError):
        media.source_url(url)


def test_pinterest_short_link_rejects_external_redirect(monkeypatch):
    def redirect(self, url):
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', redirect)
    with pytest.raises(ValueError):
        media.resolve_pin('https://pin.it/AbC123')


def test_pinterest_photo_analysis_and_validation(client, monkeypatch):
    data = {'id': '123', 'title': 'Photo', 'images': {
        'orig': {'width': 1200, 'height': 1800, 'url': 'https://i.pinimg.com/originals/photo.jpg'},
        'small': {'width': 400, 'height': 600, 'url': 'https://i.pinimg.com/400x/photo.jpg'},
        'unsafe': {'width': 2000, 'height': 3000, 'url': 'https://evil.test/photo.jpg'},
    }}
    monkeypatch.setattr(media, 'pinterest_data', lambda url: data)
    info = client.post('/api/analyze', json={'url': 'https://pinterest.com/pin/123/'}).json()
    assert info['media_type'] == 'image'
    assert info['qualities'] == [1800, 600]
    assert not info['audio_available']
    for settings in ({'output': 'mp3'}, {'output': 'video'}, {'output': 'image', 'quality': '999'}, {'output': 'image', 'start': '00:00:01', 'end': '00:00:02'}):
        assert client.post('/api/download', json={'analysis_id': info['analysis_id'], **settings}).status_code == 422
    submitted = []
    monkeypatch.setattr(main.executor, 'submit', lambda *args: submitted.append(args))
    response = client.post('/api/download', json={'analysis_id': info['analysis_id'], 'output': 'image', 'quality': '600'})
    assert response.status_code == 202
    assert submitted[0][3] == 600
    main.jobs.pop(response.json()['job_id'])


@pytest.mark.parametrize('has_audio', [True, False])
def test_pinterest_missing_codec_metadata(monkeypatch, has_audio):
    metadata = {'id': '123', 'duration': 8, 'formats': [
        {'height': 720, 'url': 'https://v.pinimg.com/video.mp4', 'ext': 'mp4'},
        {'height': 360, 'url': 'https://v.pinimg.com/small.mp4', 'ext': 'mp4'},
    ]}
    monkeypatch.setattr(media.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(metadata)))
    monkeypatch.setattr(media, 'probe', lambda p: {'streams': [{'codec_type': 'video'}] + ([{'codec_type': 'audio'}] if has_audio else [])})
    info = media.analyze_video('https://www.pinterest.com/pin/123/', pinterest=True)
    assert info['qualities'] == [720, 360]
    assert info['audio_available'] == has_audio
    assert info['channel'] == 'Pinterest'
    assert not info['embeddable']


def test_real_photo_job(sample, tmp_path, monkeypatch, client):
    root, source = sample
    photo = root / 'photo.jpg'
    subprocess.run([media.binary('ffmpeg'), '-v', 'error', '-i', str(source), '-frames:v', '1', str(photo)], check=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    downloads = tmp_path / 'downloads'; downloads.mkdir()
    temp = tmp_path / 'temp'; temp.mkdir()
    monkeypatch.setattr(media, 'DOWNLOADS', downloads)
    monkeypatch.setattr(media, 'TEMP', temp)
    job_id = str(uuid4())
    main.jobs[job_id] = {'id': job_id, 'status': 'queued', 'cancel': threading.Event()}
    info = {'id': '123', 'title': 'Photo', 'media_type': 'image', 'url': 'https://www.pinterest.com/pin/123/',
            'image_formats': [{'url': f'http://127.0.0.1:{server.server_port}/photo.jpg', 'width': 320, 'height': 180}]}
    monkeypatch.setattr(main, 'run_process', lambda *a, **k: pytest.fail('Photos must not start yt-dlp or FFmpeg'))
    try:
        main.worker(job_id, info, 180, False, None, None)
        job = main.jobs[job_id]
        assert job['status'] == 'ready', job
        assert job['path'].read_bytes() == photo.read_bytes()
        response = client.get(f'/api/files/{job_id}')
        assert response.headers['content-type'] == 'image/jpeg'
        assert response.content == photo.read_bytes()
        assert not list(temp.iterdir())
    finally:
        server.shutdown(); server.server_close()
        main.jobs.pop(job_id)


def test_silent_video_processing(sample):
    root, source = sample
    silent = root / 'silent.mp4'
    output = root / 'silent-output.mp4'
    subprocess.run([media.binary('ffmpeg'), '-v', 'error', '-i', str(source), '-an', '-c:v', 'copy', str(silent)], check=True)
    media.process_media(silent, output)
    media.verify_output(output, False, 180, 8, require_audio=False)
    formats = [{'format_id': 'silent', 'url': 'https://example.test/silent.mp4', 'ext': 'mp4', 'height': 180, 'vcodec': 'h264', 'acodec': 'none'}]
    with YoutubeDL({'quiet': True}) as ydl:
        selected = list(ydl.build_format_selector(media.selector(180, silent=True))({'formats': formats, 'has_merged_format': True, 'incomplete_formats': False}))
        assert selected[0]['format_id'] == 'silent'


def test_pinterest_share_redirect_chain(monkeypatch):
    targets = {
        'https://pin.it/AbC123': 'https://api.pinterest.com/url_shortener/AbC123/redirect/',
        'https://api.pinterest.com/url_shortener/AbC123/redirect/': 'https://in.pinterest.com/pin/123/?share=1',
    }
    def redirect(self, url):
        return httpx.Response(302, headers={'location': targets[url]}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', redirect)
    assert media.resolve_pin('https://pin.it/AbC123') == 'https://www.pinterest.com/pin/123/'


def test_silent_pinterest_api(client, monkeypatch):
    info = {'id': '123', 'url': 'https://www.pinterest.com/pin/123/', 'title': 'Silent', 'media_type': 'video', 'platform': 'pinterest', 'duration': 8, 'qualities': [720], 'audio_available': False}
    monkeypatch.setattr(media, 'analyze', lambda url: info)
    token = client.post('/api/analyze', json={'url': 'https://pinterest.com/pin/123/'}).json()['analysis_id']
    assert client.post('/api/download', json={'analysis_id': token, 'output': 'mp3'}).status_code == 422
    monkeypatch.setattr(main.executor, 'submit', lambda *a: None)
    response = client.post('/api/download', json={'analysis_id': token, 'output': 'video'})
    assert response.status_code == 202
    main.jobs.pop(response.json()['job_id'])


@pytest.mark.parametrize('url, canonical', [
    ('https://instagram.com/reel/Chunk8-jurw/?igsh=abc', 'https://www.instagram.com/reel/Chunk8-jurw/'),
    ('https://www.instagram.com/p/ABCdef123/?img_index=2&igsh=x', 'https://www.instagram.com/p/ABCdef123/?img_index=2'),
    ('https://m.instagram.com/reels/Chunk8-jurw/', 'https://www.instagram.com/reel/Chunk8-jurw/'),
    ('https://www.instagram.com/instagram/reel/Chunk8-jurw/', 'https://www.instagram.com/reel/Chunk8-jurw/'),
    ('http://instagram.com/tv/ABCdef123/', 'https://www.instagram.com/tv/ABCdef123/'),
])
def test_instagram_urls(url, canonical):
    assert media.source_url(url) == canonical


@pytest.mark.parametrize('url', [
    'https://instagram.com.evil.test/p/ABCdef123/', 'https://instagram.com@127.0.0.1/p/ABCdef123/',
    'https://instagram.com:8080/reel/ABCdef123/', 'https://instagram.com/instagram/',
    'https://instagram.com/stories/user/123/', 'https://instagram.com/p/ABCdef123/?img_index=0',
    'https://instagram.com/p/ABCdef123/?img_index=oops', 'https://instagram.com/p/ABCdef123/extra',
])
def test_invalid_instagram_urls(url):
    with pytest.raises(ValueError):
        media.source_url(url)


def test_instagram_photo_adapter_and_analysis(client, monkeypatch):
    from app.instagram import InstagramIE
    images = [
        {'url': 'https://scontent.cdninstagram.com/photo.jpg?signature=x', 'width': 1080, 'height': 1350},
        {'url': 'https://scontent.fbcdn.net/small.jpg', 'width': 320, 'height': 400},
        {'url': 'https://evil.test/fake.jpg', 'width': 2000, 'height': 2500},
    ]
    with YoutubeDL({'quiet': True}) as ydl:
        raw = InstagramIE(ydl)._extract_product({'pk': '123456789', 'media_type': 1, 'caption': {'text': 'An Instagram photo'},
            'user': {'username': 'creator'}, 'image_versions2': {'candidates': images}}, get_comments=False)
    assert raw['_tubeclip_media_type'] == 'image'
    monkeypatch.setattr(media.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(raw)))
    response = client.post('/api/analyze', json={'url': 'https://instagram.com/p/ABCdef123/'})
    assert response.status_code == 200, response.text
    info = response.json()
    assert info['media_type'] == 'image' and info['platform'] == 'instagram'
    assert info['qualities'] == [1350, 400]
    assert info['channel'] == 'creator'
    assert not info['audio_available'] and not info['embeddable']
    assert '_http_headers' not in info
    assert main.analyses[info['analysis_id']]['info']['_http_headers']['Referer'] == 'https://www.instagram.com/'


def test_instagram_carousel_selection(monkeypatch):
    from app import instagram
    image = {'id': 'photo', '_tubeclip_media_type': 'image', '_tubeclip_images': [{'url': 'https://scontent.cdninstagram.com/photo.jpg', 'width': 1080, 'height': 1350}]}
    other = {**image, 'id': 'second-photo'}
    monkeypatch.setattr(instagram.InstagramIE, 'extract', lambda self, url: {'_type': 'playlist', 'entries': [image, other]})
    selected = instagram.extract('https://www.instagram.com/p/ABCdef123/?img_index=2')
    assert selected['id'] == 'second-photo'
    assert selected['_tubeclip_item_count'] == 2 and selected['_tubeclip_item_index'] == 2
    with pytest.raises(ValueError):
        instagram.extract('https://www.instagram.com/p/ABCdef123/?img_index=3')


def test_instagram_video_dimensions_duration_and_cache(monkeypatch):
    raw = {'id': 'ABCdef123', 'title': 'Reel', 'formats': [
        {'format_id': 'small', 'url': 'https://video.cdninstagram.com/small.mp4', 'ext': 'mp4'},
        {'format_id': 'large', 'url': 'https://video.cdninstagram.com/large.mp4', 'ext': 'mp4'},
    ]}
    def inspect(url):
        height = 1280 if 'large' in url else 640
        return {'streams': [{'codec_type': 'video', 'height': height, 'width': height // 2, 'codec_name': 'h264'},
                            {'codec_type': 'audio', 'codec_name': 'aac'}], 'format': {'duration': '8.5'}}
    monkeypatch.setattr(media, 'probe', inspect)
    info = media.video_info(raw, 'https://www.instagram.com/reel/ABCdef123/', platform='instagram')
    assert info['qualities'] == [1280, 640] and info['audio_available']
    assert info['duration'] == 8.5 and info['_download_info']['formats'][0]['acodec'] == 'aac'
    assert info['_extracted_at'] > 0


def test_cached_metadata_is_server_only(client, monkeypatch):
    raw = {'id': 'abc', 'title': 'Video', 'duration': 8, 'formats': [{'format_id': '720', 'url': 'https://cdn.test/signed?secret=x', 'height': 720, 'vcodec': 'h264', 'acodec': 'aac'}]}
    info = media.video_info(raw, 'https://www.youtube.com/watch?v=aqz-KE-bpKQ')
    monkeypatch.setattr(media, 'analyze', lambda url: info)
    response = client.post('/api/analyze', json={'url': info['url']})
    assert response.status_code == 200
    assert all(not key.startswith('_') for key in response.json())
    assert main.analyses[response.json()['analysis_id']]['info']['_download_info'] == raw


def test_completed_selection_is_reused(client, monkeypatch, tmp_path):
    source = 'https://www.instagram.com/reel/ABCdef123/'
    info = {'url': source, 'duration': 8, 'qualities': [720], 'audio_available': True}
    monkeypatch.setattr(media, 'analyze', lambda url: info)
    token = client.post('/api/analyze', json={'url': source}).json()['analysis_id']
    output = tmp_path / 'ready.mp4'; output.write_bytes(b'completed fixture')
    job_id = str(uuid4())
    main.jobs[job_id] = {'id': job_id, 'status': 'ready', 'path': output,
                         'selection_key': (source, 720, 'video', None, None)}
    monkeypatch.setattr(main.executor, 'submit', lambda *a: pytest.fail('A completed file must not be downloaded twice'))
    try:
        response = client.post('/api/download', json={'analysis_id': token})
        assert response.status_code == 202 and response.json()['job_id'] == job_id
        assert 'selection_key' not in client.get(f'/api/jobs/{job_id}').json()
    finally:
        main.jobs.pop(job_id)


@pytest.mark.parametrize('state', ['fresh', 'aged', 'expired'])
def test_cached_source_refresh_and_mp4_fast_path(sample, tmp_path, monkeypatch, state):
    import shutil
    root, source = sample
    downloads = tmp_path / 'downloads'; downloads.mkdir()
    temp = tmp_path / 'temp'; temp.mkdir()
    monkeypatch.setattr(media, 'DOWNLOADS', downloads)
    monkeypatch.setattr(media, 'TEMP', temp)
    old = {'id': '123', 'url': 'https://cdn.test/old.mp4'}
    fresh = {'id': '123', 'url': 'https://cdn.test/fresh.mp4'}
    info = {'id': '123', 'url': 'https://www.instagram.com/reel/ABCdef123/', 'title': 'Video',
            'duration': 8, 'audio_available': True, 'platform': 'instagram',
            '_download_info': old, '_extracted_at': main.time.time() - (601 if state == 'aged' else 0)}
    refreshes = []
    monkeypatch.setattr(media, 'analyze', lambda url: refreshes.append(url) or {'_download_info': fresh})
    commands = []
    def download(job_id, cmd, on_line=None):
        assert '--load-info-json' in cmd and info['url'] not in cmd
        commands.append(cmd)
        if state == 'expired' and len(commands) == 1:
            raise RuntimeError('HTTP Error 403: signed URL expired')
        metadata = json.loads(Path(cmd[cmd.index('--load-info-json') + 1]).read_text())
        assert metadata == (old if state == 'fresh' else fresh)
        target = temp / f'job-{job_id}' / 'source.mp4'
        shutil.copyfile(source, target)
        on_line('TCFILE:' + str(target))
    monkeypatch.setattr(main, 'run_process', download)
    monkeypatch.setattr(media, 'process_media', lambda *a, **k: pytest.fail('Compatible MP4 must skip conversion'))
    job_id = str(uuid4())
    main.jobs[job_id] = {'id': job_id, 'status': 'queued', 'cancel': threading.Event()}
    try:
        main.worker(job_id, info, 180, False, None, None)
        job = main.jobs[job_id]
        assert job['status'] == 'ready', job
        assert job['path'].read_bytes() == source.read_bytes()
        assert len(refreshes) == (0 if state == 'fresh' else 1)
        assert len(commands) == (2 if state == 'expired' else 1)
    finally:
        main.jobs.pop(job_id)


def test_instagram_video_cover_is_not_a_photo():
    from app.instagram import InstagramIE
    with YoutubeDL({'quiet': True}) as ydl:
        result = InstagramIE(ydl)._extract_product_media({'media_type': 2,
            'image_versions2': {'candidates': [{'url': 'https://scontent.cdninstagram.com/cover.jpg', 'width': 1080, 'height': 1350}]}})
    assert result.get('_tubeclip_media_type') != 'image'


def test_image_download_cancellation(tmp_path):
    job_id = str(uuid4()); event = threading.Event(); event.set()
    main.jobs[job_id] = {'id': job_id, 'status': 'queued', 'cancel': event}
    try:
        with pytest.raises(InterruptedError):
            main.download_image(job_id, 'https://scontent.cdninstagram.com/photo.jpg', tmp_path / 'photo.jpg')
        assert not (tmp_path / 'photo.jpg').exists()
    finally:
        main.jobs.pop(job_id)


def test_instagram_photo_dimensions_come_from_actual_assets(monkeypatch):
    def inspect(url):
        assert '.cdninstagram.com/' in url
        return {'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1350}]}
    monkeypatch.setattr(media, 'probe', inspect)
    formats = media.instagram_image_formats([
        {'url': 'https://scontent.cdninstagram.com/photo.jpg'},
        {'url': 'https://evil.test/photo.jpg'},
    ])
    assert formats == [{'url': 'https://scontent.cdninstagram.com/photo.jpg', 'width': 1080, 'height': 1350}]
