import {apiUrl, normalizeApiBase} from './api-url.ts'
export const API_BASE_URL=normalizeApiBase(import.meta.env?.VITE_API_BASE_URL||'')
export const backendUrl=(path:string)=>apiUrl(path,API_BASE_URL)
let memoryClient=''
function clientId(){
 if(memoryClient)return memoryClient
 try{const existing=localStorage.getItem('tubeclip-client');if(existing&&/^[A-Za-z0-9_-]{32,128}$/.test(existing))return memoryClient=existing}catch{}
 memoryClient=crypto.randomUUID()
 try{localStorage.setItem('tubeclip-client',memoryClient)}catch{}
 return memoryClient
}
export function createApi(base:string, fetcher:typeof fetch=fetch, identity:()=>string=clientId){
 return async function api(path:string,body?:unknown,method?:string){
  const headers:Record<string,string>={}
  if(body!==undefined)headers['Content-Type']='application/json'
  if(path!=='/api/health')headers['X-TubeClip-Client']=identity()
  const response=await fetcher(apiUrl(path,base),{method:method||(body!==undefined?'POST':'GET'),headers,body:body!==undefined?JSON.stringify(body):undefined,credentials:'omit',referrerPolicy:'no-referrer'})
  let data
  try{data=await response.json()}catch{throw new Error('The download engine is starting or unavailable. Wait a moment and retry.')}
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Please check your input and try again.')
  return data
 }
}
export const api=createApi(API_BASE_URL)
