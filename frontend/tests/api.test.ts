import assert from 'node:assert/strict'
import test from 'node:test'
import {normalizeApiBase,apiUrl} from '../src/api-url.ts'
import {createApi} from '../src/api.ts'

test('local proxy and production backend origin use the same API endpoint paths',()=>{
 assert.equal(apiUrl('/api/analyze',''),'/api/analyze')
 assert.equal(normalizeApiBase('https://api.example.com/'),'https://api.example.com')
 assert.equal(apiUrl('/api/analyze','https://api.example.com/'),'https://api.example.com/api/analyze')
 assert.equal(apiUrl('/api/jobs/job-1','http://127.0.0.1:8000'),'http://127.0.0.1:8000/api/jobs/job-1')
})
test('API config rejects credentials, unsafe protocols and accidental /api suffixes',()=>{
 for(const base of ['http://api.example.com','javascript:alert(1)','https://user:pass@api.example.com','https://api.example.com/api','https://api.example.com?key=secret','https://api.example.com/#test'])
  assert.throws(()=>normalizeApiBase(base))
 for(const path of ['//evil.test','/api/../private','https://evil.test/api/files/job','/api/\\evil.test'])
  assert.throws(()=>apiUrl(path,'https://api.example.com'))
})
test('analysis, status, cancel and signed file paths all address Render without credentialed CORS',async()=>{
 const calls:{url:string;init:RequestInit|undefined}[]=[]
 const mock:typeof fetch=async(input,init)=>{calls.push({url:String(input),init});return new Response(JSON.stringify({status:'ready'}),{headers:{'Content-Type':'application/json'}})}
 const api=createApi('https://backend.example.com',mock,()=> 'c'.repeat(64))
 await api('/api/analyze',{url:'https://youtu.be/aqz-KE-bpKQ'})
 await api('/api/download',{analysis_id:'analysis'})
 await api('/api/jobs/job')
 await api('/api/jobs/job',undefined,'DELETE')
 await api('/api/health')
 assert.deepEqual(calls.map(c=>c.url),['/api/analyze','/api/download','/api/jobs/job','/api/jobs/job','/api/health'].map(p=>'https://backend.example.com'+p))
 assert.equal(calls[0].init?.method,'POST')
 assert.equal((calls[0].init?.headers as Record<string,string>)['X-TubeClip-Client'],'c'.repeat(64))
 assert.equal(calls[0].init?.credentials,'omit')
 assert.equal(calls[3].init?.method,'DELETE')
 assert.equal((calls[4].init?.headers as Record<string,string>)['X-TubeClip-Client'],undefined)
 assert.equal(apiUrl('/api/files/job?expires=123&token=abc','https://backend.example.com'),'https://backend.example.com/api/files/job?expires=123&token=abc')
})
test('cold-start HTML and JSON API errors are reported safely',async()=>{
 const html=createApi('',async()=>new Response('<html>Starting</html>',{status:503}),()=> 'a'.repeat(64))
 await assert.rejects(html('/api/health'),/starting or unavailable/)
 const limited=createApi('',async()=>new Response(JSON.stringify({detail:'Wait and retry'}),{status:429}),()=> 'a'.repeat(64))
 await assert.rejects(limited('/api/analyze',{}),/Wait and retry/)
})
