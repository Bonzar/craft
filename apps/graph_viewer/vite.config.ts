import { defineConfig } from 'vite';
const apiTarget=process.env.GRAPH_VIEWER_API_TARGET??'http://127.0.0.1:8791';
const apiOrigin=new URL(apiTarget);
if(apiOrigin.protocol!=='http:'||apiOrigin.hostname!=='127.0.0.1'||apiOrigin.username||apiOrigin.password||apiOrigin.pathname!=='/'||apiOrigin.search||apiOrigin.hash)throw new Error('GRAPH_VIEWER_API_TARGET must be an HTTP loopback origin');
export default defineConfig({root:'web',base:process.env.GRAPH_VIEWER_VISUAL_RELEASE==='1'?'./':'/',build:{outDir:'dist',emptyOutDir:true,sourcemap:false},server:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/api':{target:apiOrigin.origin,changeOrigin:false}}}});
