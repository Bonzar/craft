#!/bin/bash
# Inspect only; no pull, import, configuration writes or deployment.
set -euo pipefail
[[ $# == 2 ]]
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" |
  python3 -B "$2/stage_035_oci_layout.py" check-repo-digest "$1"
