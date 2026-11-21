// Build in the dedicated synthetic preview Site with the generated,
// checksum-verified UI assets and the original safe test fixture.
import preview from './preview_site.mjs';
import fixture from './qa_fixture.generated.mjs';
import {createQaVisualRoute} from './qa_visual_route.mjs';

const graph = createQaVisualRoute(fixture);
export default {
  async fetch(request, env) {
    return (await graph(request)) ?? preview.fetch(request,env);
  },
};
