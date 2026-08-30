#!/usr/bin/env bash
# Compatibility launcher. The graph and all agent identifiers live in core.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$repo/core/workflows/review-pipeline.mjs"
