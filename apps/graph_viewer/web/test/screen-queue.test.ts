import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ScreenMetricQueue } from '../src/screen-queue.ts';
function request(camera:number,revision='r1',lod=1){return{revision,lod_generation:lod,camera_generation:camera,view_projection:new Float32Array(16),viewport:[375,812] as [number,number]};}
function reply(camera:number,revision='r1',lod=1){return{...request(camera,revision,lod),onscreen:new Uint8Array(1),external_visible:new Uint32Array(1),canonical_self:new Uint32Array(1),proxy_internal:new Uint32Array(1),star_intensity:new Float32Array(1),proxy_intensity:new Float32Array(1),unique_external_edges:0,stats:{onscreen_stars:0,onscreen_proxies:0,star_scale_cap:1,proxy_scale_cap:1},diagnostics:{changed_representatives:0,visited_projected_edges:0,full_rebuilds:0}};}
const settle=()=>new Promise<void>(resolve=>setImmediate(resolve));
test('actual delayed worker queue never applies camera3 after camera4 has been requested and coalesces intermediate work',async()=>{
  const applied:number[]=[];const dispatched:number[]=[];const resolve=new Map<number,(value:ReturnType<typeof reply>)=>void>();
  const queue=new ScreenMetricQueue(value=>applied.push(value.camera_generation),error=>{throw error;});
  const query=(value:ReturnType<typeof request>)=>{dispatched.push(value.camera_generation);return new Promise<ReturnType<typeof reply>>(done=>resolve.set(value.camera_generation,done));};
  queue.bind('r1',1);queue.request(query,request(3));queue.request(query,request(4));queue.request(query,request(5));
  assert.deepEqual(dispatched,[3]);
  resolve.get(3)?.(reply(3));await settle();
  assert.deepEqual(applied,[]);
  assert.deepEqual(dispatched,[3,5]);
  resolve.get(5)?.(reply(5));await settle();
  assert.deepEqual(applied,[5]);
});
test('pending worker result from an old source or LOD cannot mutate current marker styles',async()=>{
  const applied:string[]=[];let finish:((value:ReturnType<typeof reply>)=>void)|null=null;
  const queue=new ScreenMetricQueue(value=>applied.push(value.revision),error=>{throw error;});queue.bind('old',1);
  queue.request(()=>new Promise<ReturnType<typeof reply>>(done=>{finish=done;}),request(1,'old',1));queue.bind('new',2);
  const complete=finish;if(complete)complete(reply(1,'old',1));await settle();
  assert.deepEqual(applied,[]);
});
