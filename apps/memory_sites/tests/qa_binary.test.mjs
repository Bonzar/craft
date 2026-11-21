import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { encodeQABuffers, qaFormats } from "../qa_binary.mjs";
const nodes = [
 {ordinal:0,position:[1.5,-3,0.25],flags:7},
 {ordinal:1,position:[-0.0,2.0,0.333],flags:0x80000000},
];
const edges = [
 {ordinal:0,from:1,to:0},
 {ordinal:1,from:1,to:0},
 {ordinal:2,from:0,to:1},
];
test("QA binaries are byte-identical to original Python struct formats",()=>{
 const reference=spawnSync("python3",["-c",
   "import json,struct,sys; q=json.load(sys.stdin); n=b''.join(struct.pack('<fffI',*v['position'],v['flags']) for v in q['nodes']); e=b''.join(struct.pack('<II',v['from'],v['to']) for v in q['edges']); print(json.dumps([n.hex(),e.hex()]))"
 ],{input:JSON.stringify({nodes,edges}).replace('"position":[0,2,0.333]','"position":[-0.0,2,0.333]'),encoding:"utf8"});
 assert.equal(reference.status,0,reference.stderr);
 const got=encodeQABuffers(nodes,edges);
 assert.deepEqual([Buffer.from(got.nodes).toString("hex"),Buffer.from(got.edges).toString("hex")],JSON.parse(reference.stdout));
 assert.equal(got.nodes.byteLength,nodes.length*qaFormats.nodes.stride);
 assert.equal(got.edges.byteLength,edges.length*qaFormats.edges.stride);
});
test("rejects discontinuous ordinals and unrepresentable or dangling slots",()=>{
 assert.throws(()=>encodeQABuffers([nodes[1],nodes[0]],edges));
 assert.throws(()=>encodeQABuffers(nodes,[{ordinal:0,from:2,to:1}]));
 assert.throws(()=>encodeQABuffers([{ordinal:0,position:[1e50,0,0],flags:0}],[]));
 assert.throws(()=>encodeQABuffers([{ordinal:0,position:[0,,0],flags:0}],[]));
 assert.throws(()=>encodeQABuffers([{ordinal:0,position:[0,0,0],flags:-1}],[]));
});
