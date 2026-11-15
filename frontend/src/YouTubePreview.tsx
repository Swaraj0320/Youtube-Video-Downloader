import {useEffect, useRef, useState} from 'react'
type Player = {loadVideoById:(options:{videoId:string;startSeconds:number;endSeconds:number})=>void;pauseVideo:()=>void;getCurrentTime:()=>number;destroy:()=>void}
type Options = {videoId:string;playerVars:Record<string,string|number>;events:{onReady:()=>void;onError:()=>void;onStateChange:(event:{data:number})=>void}}
declare global {interface Window {YT?:{Player:new(element:HTMLElement,options:Options)=>Player};onYouTubeIframeAPIReady?:()=>void}}
let apiReady:Promise<void>|undefined
function loadAPI(){
 if(window.YT?.Player)return Promise.resolve()
 if(!apiReady)apiReady=new Promise<void>((resolve,reject)=>{
  window.onYouTubeIframeAPIReady=()=>resolve()
  const script=document.createElement('script');script.src='https://www.youtube.com/iframe_api';script.async=true;script.onerror=()=>reject(new Error('Player unavailable'));document.head.appendChild(script)
 })
 return apiReady
}
export function YouTubePreview({id,start,end,preview,onStop,onFailed}:{id:string;start:number;end:number;preview:boolean;onStop:()=>void;onFailed:()=>void}){
 const host=useRef<HTMLDivElement>(null),player=useRef<Player|null>(null),callbacks=useRef({onStop,onFailed})
 callbacks.current={onStop,onFailed}
 const [ready,setReady]=useState(false)
 useEffect(()=>{
  let disposed=false,loaded=false
  setReady(false)
  const timer=setTimeout(()=>{if(!loaded)callbacks.current.onFailed()},20000)
  loadAPI().then(()=>{
   if(disposed||!host.current||!window.YT)return
   const element=document.createElement('div');host.current.appendChild(element)
   player.current=new window.YT.Player(element,{videoId:id,playerVars:{origin:window.location.origin,playsinline:1,rel:0},events:{onReady:()=>{loaded=true;clearTimeout(timer);if(!disposed)setReady(true)},onError:()=>callbacks.current.onFailed(),onStateChange:event=>{if(event.data===0)callbacks.current.onStop()}}})
  }).catch(()=>{if(!disposed)callbacks.current.onFailed()})
  return()=>{disposed=true;clearTimeout(timer);player.current?.destroy();player.current=null;host.current?.replaceChildren()}
 },[id])
 useEffect(()=>{
  if(!ready||!player.current)return
  if(preview)player.current.loadVideoById({videoId:id,startSeconds:start,endSeconds:end})
  else player.current.pauseVideo()
  if(!preview)return
  const timer=setInterval(()=>{if(player.current&&player.current.getCurrentTime()>=end){player.current.pauseVideo();callbacks.current.onStop()}},100)
  return()=>clearInterval(timer)
 },[id,start,end,preview,ready])
 return <div className="youtube-host" ref={host}/>
}
