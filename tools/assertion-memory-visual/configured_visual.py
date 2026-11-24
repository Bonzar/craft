"""Configured Visual authority; never falls back to legacy credentials or account."""
import json
import os
from pathlib import Path
import sys

from native_visual import CONFIGURED_VISUAL, IDENTITY_KEYS, RECEIPT_FILE, lifecycle
from native_visual import require_context, resolve


def run(action, env):
    mode = require_context(env, CONFIGURED_VISUAL)
    if action == 'resolve':
        proof = resolve(lifecycle.ci.api, env, CONFIGURED_VISUAL)
        with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf8') as out:
            for key in IDENTITY_KEYS:
                out.write(f'{key}={proof[key]}\n')
            encoded = json.dumps(proof, sort_keys=True, separators=(',', ':'))
            out.write(f'proof={encoded}\n')
    elif action == 'send':
        if env.get('CONFIGURED_VISUAL_DEPLOY_ENABLED') != 'true':
            raise ValueError('Configured visual transport disabled')
        for key in ("VISUAL_HOST_PIN", "VISUAL_SSH_KEY", "VISUAL_SIGNING_KEY"):
            if not env.get(key, "").strip():
                raise ValueError("Missing stage-specific visual credential: " + key)
        from visual_send import deploy
        deploy(env, lane='configured')
    elif action == 'receipt' and mode == 'test':
        proof = json.loads(env['NATIVE_VISUAL_TEST_PROOF'])
        if (proof.get('profile') != CONFIGURED_VISUAL.profile
                or proof.get('control_workflow') != CONFIGURED_VISUAL.filename):
            raise ValueError('Wrong configured proof authority')
        with (Path(env['VISUAL_DOWNLOAD']) / RECEIPT_FILE).open('x', encoding='utf8') as out:
            json.dump(proof, out, sort_keys=True)
    else:
        raise ValueError('Invalid configured visual stage')


if __name__ == '__main__':
    run(sys.argv[1], os.environ)
