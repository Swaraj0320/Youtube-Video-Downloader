import React, {useEffect, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ArrowDownToLine, ArrowRight, AudioLines, Check, CheckCheck, ChevronRight, Clipboard, Clock3, Film, ImageIcon, Link2, LoaderCircle, Music2, Play, RotateCcw, Scissors, ShieldCheck, Sun, Moon, X} from 'lucide-react'
import '@fontsource-variable/manrope'
import '@fontsource-variable/newsreader/standard-italic.css'
import './style.css'
import {YouTubePreview} from './YouTubePreview'
import {api, backendUrl} from './api'

type Video = {url:string;item_count?:number;item_index?:number;platform?:string;media_type?:string;image_formats?:{width:number;height:number}[];analysis_id:string;id:string;title:string;channel:string;duration:number;thumbnail:string;qualities:number[];audio_available:boolean;embeddable:boolean}
type Job = {download_url?:string;id:string;status:string;phase:string;percent:number|null;error?:string;filename?:string;size?:number;speed?:number;eta?:number}
const time = (n:number) => {n=Math.floor(n);return `${String(Math.floor(n/3600)).padStart(2,'0')}:${String(Math.floor(n/60)%60).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`}
const seconds = (s:string) => {if(!/^\d{2,}:\d{2}:\d{2}$/.test(s))return NaN;const [h,m,v]=s.split(':').map(Number);return m<60&&v<60?h*3600+m*60+v:NaN}
const size = (n:number) => n>1e9?`${(n/1e9).toFixed(1)} GB`:`${(n/1e6).toFixed(1)} MB`
async function saveFile(job:Job){
 const fresh:Job=await api(`/api/jobs/${encodeURIComponent(job.id)}`)
 if(fresh.status!=='ready'||!fresh.download_url)throw new Error('This download is no longer available. Analyze the link again.')
 const link=document.createElement('a')
 link.href=backendUrl(fresh.download_url)
 link.download=fresh.filename||''
 link.referrerPolicy='no-referrer'
 document.body.appendChild(link)
 link.click()
 link.remove()
}
function App(){
 const [theme,setTheme]=useState<'dark'|'light'>(()=>document.documentElement.dataset.theme==='light'?'light':'dark')
 useEffect(()=>{
  document.documentElement.dataset.theme=theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#100E10':'#FFFAEF')
  try{localStorage.setItem('tubeclip-theme',theme)}catch{}
 },[theme])
 const [url,setUrl]=useState(''),[busy,setBusy]=useState(false),[video,setVideo]=useState<Video|null>(null),[quality,setQuality]=useState('best'),[mode,setMode]=useState('video'),[start,setStart]=useState(''),[end,setEnd]=useState(''),[error,setError]=useState(''),[job,setJob]=useState<Job|null>(null),[submitting,setSubmitting]=useState(false),[preview,setPreview]=useState(false),[embedFailed,setEmbedFailed]=useState(false),[online,setOnline]=useState<boolean|null>(null)
 const results=useRef<HTMLDivElement>(null)
 const downloadedJob=useRef<string|null>(null),submittedSelection=useRef<string|null>(null)
 const selection=JSON.stringify([video?.analysis_id,quality,mode,start,end])
 const canSaveAgain=job?.status==='ready'&&submittedSelection.current===selection
 const active=!!job&&!['ready','failed','cancelled'].includes(job.status)
 const isImage=video?.media_type==='image'
 const selectedPhoto=video?.image_formats?.find(f=>f.height===(quality==='best'?video.qualities[0]:Number(quality)))
 const photoDimensions=selectedPhoto?`${selectedPhoto.width} × ${selectedPhoto.height}`:''
 const a=start?seconds(start):0,b=end?seconds(end):(video?.duration||0)
 const clipError=(start||end)&&(!start||!end)?'Enter both timestamps, or reset to the full video.':(start||end)&&(!Number.isFinite(a)||!Number.isFinite(b))?'Use HH:MM:SS for both timestamps.':(start||end)&&(a<0||b<=a||b>(video?.duration||0))?'End must be after start and within the video duration.':''
 useEffect(()=>{let stopped=false,attempts=0;let timer:ReturnType<typeof setTimeout>;async function check(){try{const d=await api('/api/health');if(!stopped)setOnline(d.status==='ok')}catch{if(stopped)return;if(++attempts<8)timer=setTimeout(check,Math.min(15000,attempts*3000));else setOnline(false)}}void check();return()=>{stopped=true;clearTimeout(timer)}},[])
 useEffect(()=>{if(!job||!active)return;let stopped=false;let timer:ReturnType<typeof setTimeout>;async function poll(){try{const next=await api(`/api/jobs/${job!.id}`);if(stopped)return;setJob(next);if(!['ready','failed','cancelled'].includes(next.status))timer=setTimeout(poll,350)}catch(e){if(!stopped){setError((e as Error).message);timer=setTimeout(poll,1500)}}}void poll();return()=>{stopped=true;clearTimeout(timer)}},[job?.id,active])
 useEffect(()=>{
  if(job?.status!=='ready'||downloadedJob.current===job.id)return
  downloadedJob.current=job.id
  void saveFile(job).catch(e=>setError((e as Error).message))
 },[job?.id,job?.status])
 async function analyze(e:React.FormEvent, source=url){e.preventDefault();if(busy||active)return;setError('');setBusy(true);setPreview(false);try{const v=await api('/api/analyze',{url:source});setVideo(v);setQuality('best');setMode(v.media_type==='image'?'image':v.qualities.length?'video':'mp3');setStart('');setEnd('');setJob(null);setEmbedFailed(false);setTimeout(()=>results.current?.scrollIntoView({behavior:'smooth',block:'start'}),100)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function paste(){try{setUrl(await navigator.clipboard.readText());setError('')}catch{setError('Clipboard access is unavailable. Paste into the link field with ⌘V.')}}
 async function download(){if(!video||busy||clipError||active||submitting)return;if(canSaveAgain&&job){try{await saveFile(job)}catch(e){setError((e as Error).message)}return}setError('');setSubmitting(true);try{const d=await api('/api/download',{analysis_id:video.analysis_id,quality,output:mode,start:start||null,end:end||null});submittedSelection.current=selection;setJob({id:d.job_id,status:'queued',phase:'Preparing',percent:null})}catch(e){setError((e as Error).message)}finally{setSubmitting(false)}}
 async function selectItem(e:React.ChangeEvent<HTMLSelectElement>){if(!video)return;const link=new URL(video.url);link.searchParams.set('img_index',e.target.value);setUrl(link.toString());await analyze(e,link.toString())}
 const reset=()=>{setStart('');setEnd('');setPreview(false)}
 return (
  <div className={`app ${video?'has-video':''}`}>
   <a className="skip-link" href="#source">Skip to media link</a>
   <header className="nav">
    <a className="brand" href="/" aria-label="TubeClip By Swaraj home">
     <span className="brand-icon"><img src="/swaraj-flower-logo.png" alt="" width="60" height="60"/></span>
     <span className="brand-name">TubeClip <span className="brand-credit">By Swaraj</span></span>
    </a>
    <div className="nav-caption">A small studio for your favorite moments.</div>
    <div className="nav-actions"><button className="theme-toggle" aria-label={`Switch to ${theme==='dark'?'light':'dark'} theme`} title={`Switch to ${theme==='dark'?'light':'dark'} theme`} onClick={()=>setTheme(theme==='dark'?'light':'dark')}>{theme==='dark'?<Sun size={18}/>:<Moon size={18}/>}<span>{theme==='dark'?'Light mode':'Dark mode'}</span></button><span className="health nav-health"><i className={online?'healthy':online===false?'unhealthy':''}/>{online?'Download engine ready':online===false?'Engine unavailable':'Connecting to engine'}</span></div>
   </header>
   <main>
    <section className="hero" aria-labelledby="main-heading">
     <div className="hero-copy">
      <div className="eyebrow"><span className="tiny-cross">✳</span> THE VIDEO, PHOTO & AUDIO WORKBENCH</div>
      <h1 id="main-heading">Good moments.<br/><span>Yours to keep.</span></h1>
      <p>Save YouTube videos and Pinterest or Instagram photos and videos. Keep the audio. Clip your favorite part.</p>
      <div className="benefits"><span><Check size={16}/>Original quality</span><span><Check size={16}/>Precise clips</span><span><Check size={16}/>One-click download</span></div>
     </div>
     <div className="source-card" id="source">
      <div className="source-heading"><span className="step">01 / SOURCE</span><span className="source-format"><Film size={15}/> YOUTUBE + PINTEREST + INSTAGRAM</span></div>
      <h2>It starts with a link.</h2>
      <p>Drop in a YouTube, Pinterest or Instagram link. We’ll find the available qualities.</p>
      <form className="url-form" onSubmit={analyze}>
       <div className="url-field"><Link2 size={20}/><input aria-label="YouTube, Pinterest or Instagram URL" placeholder="Paste a YouTube, Pinterest or Instagram link…" value={url} onChange={e=>setUrl(e.target.value)} required disabled={busy||active}/><button className="paste" type="button" onClick={paste} disabled={busy||active}><Clipboard size={17}/><span>Paste</span></button></div>
       <button className="primary analyze" disabled={busy||active||!url.trim()}>{busy?<><LoaderCircle className="spin" size={19}/>Analyzing link…</>:<>Analyze Link<ArrowRight size={19}/></>}</button>
      </form>
      <p className="permission"><ShieldCheck size={15}/>For content you own or have permission to save.</p>
     </div>
    </section>
    {error&&<div role="alert" className="error">{error}<button aria-label="Dismiss error" onClick={()=>setError('')}><X size={19}/></button></div>}
    {!video&&<section className="intro" aria-labelledby="possibilities-heading">
     <div className="intro-heading"><h2 id="possibilities-heading">One link. Three possibilities.</h2><span>A little toolkit. A lot of possibilities.</span></div>
     <div className="intro-grid">
      <article className="feature-video"><div className="feature-top"><span className="feature-icon"><Film size={23}/></span><span className="feature-number">01</span></div><div className="video-visual" aria-hidden="true"><div className="frame frame-back"/><div className="frame frame-front"><Play size={24} fill="currentColor"/><span>ORIGINAL QUALITY</span><i/></div><span className="visual-note">Every pixel counts.</span></div><h3>The whole picture.</h3><p>From a quick watch to full 4K. Save videos and photos in the best quality the source offers.</p><span className="tag">VIDEO & PHOTO DOWNLOADS <ArrowDownToLine size={15}/></span></article>
      <article className="feature-audio"><div className="feature-top"><span className="feature-icon"><AudioLines size={24}/></span><span className="feature-number">02</span></div><div className="audio-visual" aria-hidden="true"><div className="waveform">{Array.from({length:35},(_,i)=><i key={i} style={{height:`${14+((i*17+i*i*7)%64)}%`}}/>)}</div><span className="visual-note">Less screen. More sound.</span></div><h3>Just the good sounds.</h3><p>Extract high-quality MP3 audio. Keep the conversation, the melody, or the idea.</p><span className="tag">AUDIO EXTRACTION <Music2 size={15}/></span></article>
      <article className="feature-clip"><div className="feature-top"><span className="feature-icon"><Scissors size={23}/></span><span className="feature-number">03</span></div><div className="clip-visual" aria-hidden="true"><div className="mini-timeline"><div className="mini-selection"><span/><Scissors size={23}/><span/></div></div><div className="mini-times"><span>00:04:00</span><span>00:04:50</span></div><span className="visual-note">Find your fifty seconds.</span></div><h3>Only the part you love.</h3><p>Set a start. Pick an end. Download that exact moment as a video or an MP3.</p><span className="tag">PRECISION CLIPPING <Scissors size={15}/></span></article>
     </div>
    </section>}
    {video&&<div className="workspace" ref={results}>
     <section className="video-summary"><img src={video.thumbnail} alt="Source preview"/><div><span className="eyebrow">YOUR SOURCE, READY TO GO</span><h2>{video.title}</h2><p>{video.channel}<span>·</span>{isImage?<>Photo</>:<><Clock3 size={16}/>{time(video.duration)}</>}</p></div><span className="analyzed"><CheckCheck size={18}/>Analyzed</span></section>
     {(video.item_count||1)>1&&<label className="post-items">This Instagram post has {video.item_count} items<select aria-label="Instagram post item" value={video.item_index||1} disabled={busy||active||submitting} onChange={selectItem}>{Array.from({length:video.item_count||1},(_,i)=><option key={i+1} value={i+1}>Item {i+1}</option>)}</select></label>}
     <div className="editor-grid">
      <section className="panel quality">
       <div className="section-title"><span className="step">02</span><h2>Make it yours.</h2></div>
       <div className="segmented">{isImage?<button className="selected" disabled={active}><ImageIcon size={18}/>Photo</button>:<><button className={mode==='video'?'selected':''} disabled={active||!video.qualities.length} onClick={()=>setMode('video')}><Film size={18}/>Video</button><button className={mode==='mp3'?'selected':''} disabled={active||!video.audio_available} onClick={()=>setMode('mp3')}><Music2 size={18}/>MP3 Audio</button></>}</div>
       {mode!=='mp3'?<><p className="field-label">CHOOSE YOUR QUALITY</p><div className="qualities">{['best',...video.qualities.map(String)].map(q=><button key={q} disabled={active} aria-pressed={quality===q} className={quality===q?'quality-option chosen':'quality-option'} onClick={()=>setQuality(q)}><span className="radio"/><span>{q==='best'?'Best Available':isImage?`${video.image_formats?.find(f=>f.height===Number(q))?.width} × ${q}`:`${q}p`}</span><small>{q==='best'?(isImage?`${video.image_formats?.[0]?.width} × ${video.qualities[0]}`:`${video.qualities[0]}p`):isImage?'PHOTO':Number(q)>=2160?'4K UHD':Number(q)>=1440?'2K':Number(q)>=1080?'FULL HD':Number(q)>=720?'HD':''}</small>{quality===q&&<Check size={18}/>}</button>)}</div><p className="hint"><ShieldCheck size={16}/>{isImage?'Available source resolutions. No upscaling.':video.audio_available?'Actual source quality. Audio included.':'Actual source quality. Silent video.'}</p></>:<div className="audio-info"><AudioLines size={64} strokeWidth={1.3}/><h3>Sound, on its own.</h3><p>The best source audio, converted to a high-quality MP3. Available for videos and reels with sound.</p><span className="tag">FULL VIDEO OR SELECTED CLIP</span></div>}
      </section>
      {isImage?<section className="panel photo-preview"><div className="section-title"><span className="step">03</span><h2>Your photo.</h2></div><img src={video.thumbnail} alt={video.title}/><p className="hint">Save the highest available quality or choose a smaller source resolution.</p></section>:<section className="panel clipper">
       <div className="section-title"><span className="step">03</span><h2>Find your moment.</h2><span className="optional">OPTIONAL CLIP</span></div>
       <div className="clipper-body">
        <div className="preview-column"><div className="player">{video.embeddable&&!embedFailed?<YouTubePreview id={video.id} start={a} end={b} preview={preview} onStop={()=>setPreview(false)} onFailed={()=>{setEmbedFailed(true);setPreview(false)}}/>:<><img src={video.thumbnail} alt="Video thumbnail — embedded preview unavailable"/><p>Preview unavailable. You can still select and download a clip.</p></>}</div><span className="preview-caption"><Play size={14}/>YOUR VIDEO PREVIEW<span>{time(video.duration)}</span></span></div>
        <div className="clip-controls">
         <div className="clip-controls-heading"><Scissors size={20}/><h3>A little less. Just enough.</h3></div><p className="clip-description">Drag the handles or enter your timestamps. Leave both empty to keep the whole video.</p>
         <div className="timeline-label"><span>{start||'00:00:00'}</span><span>{end||time(video.duration)}</span></div>
         <div className="timeline"><div className="timeline-track"/><div className="timeline-selection" style={{left:`${Math.max(0,a/video.duration*100)||0}%`,right:`${Math.max(0,100-b/video.duration*100)||0}%`}}/><input aria-label="Clip start handle" type="range" min="0" max={Math.floor(video.duration)} step="1" value={Number.isFinite(a)?a:0} disabled={active} onChange={e=>{const n=Number(e.target.value);setStart(time(Math.min(n,Math.floor(b)-1)));if(!end)setEnd(time(video.duration));setPreview(false)}}/><input aria-label="Clip end handle" type="range" min="0" max={Math.floor(video.duration)} step="1" value={Number.isFinite(b)?Math.floor(b):Math.floor(video.duration)} disabled={active} onChange={e=>{setEnd(time(Math.max(Number(e.target.value),a+1)));if(!start)setStart(time(0));setPreview(false)}}/></div>
         <div className="time-inputs"><label>START TIME<input aria-label="Start time" placeholder="00:00:00" value={start} disabled={active} onChange={e=>{setStart(e.target.value);setPreview(false)}}/></label><ChevronRight size={20}/><label>END TIME<input aria-label="End time" placeholder={time(video.duration)} value={end} disabled={active} onChange={e=>{setEnd(e.target.value);setPreview(false)}}/></label></div>
         {clipError&&<p className="validation" role="alert">{clipError}</p>}
         <div className="duration-label"><Clock3 size={18}/><span>{start?'Selected duration':'Full video'}<strong>{!clipError?time(b-a):'—'}</strong></span></div>
         <div className="clip-actions"><button disabled={!!clipError||active||embedFailed||!video.embeddable} onClick={()=>setPreview(!preview)}><Play size={16}/>{preview?'Stop Preview':'Preview Clip'}</button><button disabled={active} onClick={reset}><RotateCcw size={15}/>Reset</button></div>
        </div>
       </div>
      </section>}
     </div>
     <section className="download-bar">
      <div className="download-summary"><span className="feature-icon">{mode==='mp3'?<Music2 size={25}/>:<ArrowDownToLine size={25}/>}</span><div><h3>{isImage?`Photo · ${photoDimensions}`:mode==='mp3'?'MP3 Audio':quality==='best'?`Best Available · ${video.qualities[0]}p`:`${quality}p Video`}</h3><p>{isImage?'Source photo quality':start&&!clipError?`${time(b-a)} clip`:'Full-length download'}<span>·</span>Automatically saved when ready</p></div></div>
      <button className="primary download" onClick={download} disabled={busy||active||submitting||!!clipError||(mode==='mp3'&&!video.audio_available)}>{active||submitting?<LoaderCircle className="spin" size={20}/>:<ArrowDownToLine size={20}/>} {active||submitting?'Processing…':canSaveAgain?'Download again':'Download'}{!active&&!submitting&&<ArrowRight size={20}/>}</button>
     </section>
     {job&&<section className={`progress-card ${job.status}`} aria-live="polite"><div className="progress-heading">{job.status==='ready'?<span className="success-icon"><Check size={22}/></span>:active?<LoaderCircle className="spin" size={22}/>:<X size={22}/>}<div><h3>{job.status==='ready'?'All set. Your browser download is starting.':job.phase}</h3><p>{job.error||(job.status==='ready'?`${job.filename} · ${size(job.size||0)}`:job.status==='cancelled'?'You can start a new download.':job.status==='downloading'?'Receiving your source media. Each stream reports its own progress.':'Preparing your selected file. Larger files and audio conversion take longer.')}</p></div>{active&&<button onClick={()=>api(`/api/jobs/${job.id}`,undefined,'DELETE').catch(e=>setError(e.message))}>Cancel download<X size={15}/></button>}</div>{active&&<><div className={`progress-track ${job.percent===null?'indeterminate':''}`}><div style={{width:job.percent===null?'35%':`${job.percent}%`}}/></div><div className="progress-stats"><span>{job.percent===null?'Processing…':`${job.percent.toFixed(1)}% of current stream`}</span><span>{job.speed?`${size(job.speed)}/s`:''}{job.eta?` · ${Math.ceil(job.eta)}s remaining`:''}</span></div></>}</section>}
    </div>}
    <footer><span className="footer-brand"><img src="/swaraj-flower-logo.png" alt="" width="29" height="29"/>TubeClip By Swaraj<span>Made for the moments worth keeping.</span></span><span className="footer-note"><ShieldCheck size={15}/>Save files to your device.</span></footer>
   </main>
  </div>
 )
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
