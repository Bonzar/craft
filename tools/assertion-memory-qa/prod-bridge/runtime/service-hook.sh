#!/bin/bash
set -euo pipefail
umask 077
exec /usr/bin/python3 -I -B /etc/assertion-release/production/prod_service_hook.py "$@"
