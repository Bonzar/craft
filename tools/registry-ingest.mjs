#!/usr/bin/env node
// Compatibility entrypoint. The implementation and all provider-neutral
// ingest logic live in core/classifier/registry-ingest.mjs.
await import('../core/classifier/registry-ingest.mjs');
