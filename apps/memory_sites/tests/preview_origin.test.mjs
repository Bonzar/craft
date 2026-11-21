import test from 'node:test';
import assert from 'node:assert/strict';
import {sameOriginPreviewPost} from '../preview_origin.mjs';
const root='https://private.example.test';
const submit=(headers,method='POST')=>sameOriginPreviewPost(new Request(root+'/preview/permanent',{method,headers}));
test('recognizes trusted same-origin form submissions, including opaque Origin',()=>{
  assert.equal(submit({origin:root}),true);
  assert.equal(submit({origin:root,'sec-fetch-site':'same-origin'}),true);
  assert.equal(submit({origin:'null','sec-fetch-site':'same-origin'}),true);
  assert.equal(submit({'sec-fetch-site':'same-origin'}),true);
});
test('rejects cross-site, sibling-site, opaque-unverified and inconsistent metadata',()=>{
  for(const headers of [{},{origin:'null'},{origin:'null','sec-fetch-site':'cross-site'},
     {origin:root,'sec-fetch-site':'same-site'},{origin:'https://different.test'},
     {origin:'https://different.test','sec-fetch-site':'same-origin'}]){
    assert.equal(submit(headers),false);
  }
  assert.equal(submit({origin:root},'GET'),false);
});
