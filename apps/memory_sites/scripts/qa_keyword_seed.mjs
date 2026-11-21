// Build-time SQL generation only; caller imports this into the separate synthetic preview D1.
import {createHash} from 'node:crypto';
const sql = value => {
  if (typeof value==='number'&&Number.isSafeInteger(value))return String(value);
  if (typeof value!=='string'||value.includes('\0'))throw Error('Unexpected synthetic FTS value');
  return "'"+value.replaceAll("'","''")+"'";
};
const insert = (table,rows)=>rows.map(row=>`INSERT INTO ${table} VALUES (${row.map(sql).join(',')});`);
const parts = value => Array.from(value).reduce((chunks,c,i)=>{
  if(i%8000===0)chunks.push('');chunks[chunks.length-1]+=c;return chunks;
},[]);
function nodeInserts(nodes,search){
  return nodes.flatMap(([id,key,text,flags],i)=>{
    const [body,...bodyTail]=parts(text),[indexed,...indexedTail]=parts(search[i][1]);
    return [...insert('qa_kw_nodes',[[id,key,body??'',flags,indexed??'']]),
      ...bodyTail.map(chunk=>`UPDATE qa_kw_nodes SET text=text||${sql(chunk)} WHERE ordinal=${id};`),
      ...indexedTail.map(chunk=>`UPDATE qa_kw_nodes SET indexed_text=indexed_text||${sql(chunk)} WHERE ordinal=${id};`)];
  });
}
export function keywordSeed(snapshot) {
  const p=snapshot.keyword_projection;
  if(!p||!snapshot.capabilities?.readiness?.some(x=>x.code==='synthetic_fixture')||
    p.revision!==snapshot.manifest.revision||p.nodes.length!==snapshot.manifest.counts.nodes||p.search.length!==p.nodes.length||
    p.nodes.some((row,i)=>row.length!==4||row[0]!==i||p.search[i][0]!==i)||
    p.maps.some(row=>row.length!==5||row[0]!==row[3]+1||snapshot.overviews?.[row[4]]?.text!==row[2])||
    p.maps.length!==Object.keys(snapshot.overviews??{}).length)throw Error('Complete pinned synthetic keyword projection required');
  const index={revision:p.revision,sha256:createHash('sha256').update(JSON.stringify(p)).digest('hex'),nodes:p.nodes.length,maps:p.maps.length};
  const statements=[
    'CREATE TABLE IF NOT EXISTS qa_kw_marker (id INTEGER PRIMARY KEY CHECK(id=1),revision TEXT NOT NULL,sha256 TEXT NOT NULL);',
    'CREATE TABLE IF NOT EXISTS qa_kw_nodes (ordinal INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,text TEXT NOT NULL,flags INTEGER NOT NULL,indexed_text TEXT NOT NULL);',
    'CREATE VIRTUAL TABLE IF NOT EXISTS qa_kw_fts USING fts5(text);',
    'CREATE VIRTUAL TABLE IF NOT EXISTS qa_kw_overviews_fts USING fts5(question,text,topic UNINDEXED,topic_id UNINDEXED);',
    'DELETE FROM qa_kw_marker;', 'DELETE FROM qa_kw_nodes;', 'DELETE FROM qa_kw_fts;', 'DELETE FROM qa_kw_overviews_fts;',
    ...nodeInserts(p.nodes,p.search),
    'INSERT INTO qa_kw_fts(rowid,text) SELECT ordinal,indexed_text FROM qa_kw_nodes ORDER BY ordinal;',
    ...insert('qa_kw_overviews_fts(rowid,question,text,topic,topic_id)',p.maps),
    ...insert('qa_kw_marker',[[1,index.revision,index.sha256]]),
  ];
  if(statements.some(s=>Buffer.byteLength(s)>80000))throw Error('Synthetic projection has an oversized D1 SQL statement');
  return {index,sql:statements.join('\n--> statement-breakpoint\n')+'\n'};
}
