// Build in the dedicated synthetic preview Site with the generated,
// checksum-verified UI assets and the original safe test fixture.
import preview from './preview_site.mjs';
import {permanentPreview} from './permanent_preview_route.mjs';
import fixture from './qa_fixture.generated.mjs';
import {createQaVisualRoute} from './qa_visual_route.mjs';
import {createSavedExplorerRoute} from './saved_explorer_route.mjs';

const graph = createQaVisualRoute(fixture);
const savedGraph = createSavedExplorerRoute(fixture);
export default {
  async fetch(request, env) {
    return (await savedGraph(request,env)) ?? (await graph(request)) ?? (await permanentPreview(request,env)) ?? preview.fetch(request,env);
  },
};
