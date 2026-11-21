// Build in the dedicated synthetic preview Site with the generated,
// checksum-verified UI assets and the original safe test fixture.
import preview from './preview_site.mjs';
import {permanentPreview} from './permanent_preview_route.mjs';
import fixture from './qa_fixture.generated.mjs';
import {createQaVisualRoute} from './qa_visual_route.mjs';
import {createSavedExplorerRoute} from './saved_explorer_route.mjs';
import {createModelPreviewRoute} from './model_preview_route.mjs';
import {createLiveSourceProvider} from './live_source_provider.mjs';
import {createPreviewMcpRoute} from './preview_mcp_route.mjs';

const graph = createQaVisualRoute(fixture);
const savedGraph = createSavedExplorerRoute(fixture);
const modelPreview = createModelPreviewRoute({buildProvider:createLiveSourceProvider});
// This exact fixture-only build is deployed exclusively behind the verified
// Sites owner-only access policy. Never reuse this identity gate for a shared
// site or an origin accepting untrusted identity headers.
const mcpPreview = createPreviewMcpRoute({isOwner:request=>Boolean(request.headers.get('oai-authenticated-user-id'))});
export default {
  async fetch(request, env) {
    return (await mcpPreview(request,env)) ?? (await modelPreview(request,env)) ?? (await savedGraph(request,env)) ?? (await graph(request,env)) ?? (await permanentPreview(request,env)) ?? preview.fetch(request,env);
  },
};
