// Bounded graph-node keyword SQL. Caller must provide a prepared, revision-isolated
// projection with original FTS5 tokenizer and node ordinals. This is not memory_query.
export function graphNodeLexical({query,history=false,offset=0,limit=50}) {
  if (typeof query !== 'string' || !query.trim() || [...query].length>8000) throw new RangeError('invalid_query');
  if (!Number.isSafeInteger(offset) || offset<0 || !Number.isSafeInteger(limit) || limit<1 || limit>200)
    throw new RangeError('invalid_pagination');
  const terms=query.match(/[\p{L}\p{N}_]+/gu) ?? [];
  if (!terms.length) return null;
  const expression=terms.map(term=>`"${term}"`).join(' AND ');
  const hidden=' AND n.flags&4=0'+(history?'':' AND n.flags&3=0');
  return {
    sql:'SELECT n.id,n.ordinal,n.text,bm25(search) AS score FROM search JOIN nodes n ON n.ordinal=search.rowid WHERE search MATCH ?'+hidden+' ORDER BY score,n.ordinal LIMIT ? OFFSET ?',
    values:[expression,limit,offset],
  };
}
