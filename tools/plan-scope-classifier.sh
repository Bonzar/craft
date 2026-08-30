#!/usr/bin/env bash
# Compatibility entry point. The classifier contract and provider backends live in core.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/core/classifier/run.sh" "$@"
