import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import {normalizeApiBase} from './src/api-url'
export default defineConfig(({mode})=>{
 const env=loadEnv(mode,process.cwd(),'VITE_')
 const base=normalizeApiBase(env.VITE_API_BASE_URL||'')
 if(process.env.VERCEL==='1'&&!base)throw new Error('Set VITE_API_BASE_URL to your Render backend HTTPS origin, then redeploy.')
 return {plugins:[react(),tailwindcss()],server:{proxy:{'/api':'http://127.0.0.1:8000'}}}
})
