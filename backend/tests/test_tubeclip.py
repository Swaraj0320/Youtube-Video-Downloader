from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
from uuid import uuid4

import pytest
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
def client():
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
    original=media.base_command
    monkeypatch.setattr(media,'base_command',lambda:original()+['--load-info-json',str(metadata)])
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
