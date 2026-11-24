/** API origin only: endpoint paths already include /api. Empty means the local Vite proxy. */
export function normalizeApiBase(value: string): string {
 const raw=value.trim()
 if(!raw)return ''
 const url=new URL(raw)
 const local=url.hostname==='localhost'||url.hostname==='127.0.0.1'
 if((url.protocol!=='https:'&&!(url.protocol==='http:'&&local))||url.username||url.password||url.search||url.hash||url.pathname!=='/')
  throw new Error('VITE_API_BASE_URL must be an HTTPS backend origin without /api, credentials, query, or fragment. HTTP localhost is allowed for development.')
 return url.origin
}
export function apiUrl(path: string, base: string): string {
 if(!path.startsWith('/api/')||path.startsWith('//')||path.includes('\\')||path.includes('..'))throw new Error('Invalid API endpoint')
 return `${normalizeApiBase(base)}${path}`
}
