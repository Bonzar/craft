"""Optional neutral operations on the existing release-managed loopback listener."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import pwd
import sys

SCOPES = {
    'assertion-test-deploy': ('test','test'),
    'assertion-production-deploy': ('prod','production'),
}


def configured_neutral_factory(config_path, legacy_token):
    selected=SCOPES.get(pwd.getpwuid(os.getuid()).pw_name)
    if selected is None:
        return None
    stage,scope=selected
    root=Path('/srv/assertion-release')/scope
    config=Path(config_path)
    if config.parent!=root/'state' or config.resolve(strict=True)!=config:
        return None
    protected=Path('/etc/assertion-release')/scope
    marker=protected/'private-operations.json'
    if not marker.exists() and not marker.is_symlink():
        return None
    directory=Path(__file__).resolve().parents[2]/'tools/assertion-memory-hosted-mcp'
    expected=directory/'private_host.py'
    if not expected.is_file() or expected.is_symlink():
        raise ValueError('Private operation binding has no verified release launcher')
    sys.path.insert(0,str(directory))
    for name in ('private_host','compile_functional_release','policy','vm_relay'):
        module=importlib.import_module(name)
        if Path(module.__file__).resolve()!=directory/(name+'.py'):
            raise ValueError('Wrong private operation source identity')
    return sys.modules['private_host'].factory(stage,root,protected,legacy_token)
