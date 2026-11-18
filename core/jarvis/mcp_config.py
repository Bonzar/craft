"""Optional module-owned remote MCP bindings. No network or secret resolution.

A module's mcp/connection.json supplies source defaults or names an existing
generic private configuration. Credential values are never resolved here.
Ownership is validated before mutation; native config and its ledger remain
consistent before hook trust is requested. Unrelated settings are preserved.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

OPEN = '# jarvis:mcp:begin'
CLOSE = '# jarvis:mcp:end'
DESCRIPTOR = 'connection.json'
BINDING = 'installed.json'
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
SERVER_NAME = re.compile(r'^[a-zA-Z0-9_-]+$')


def _object(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'MCP configuration must be an object: {path}')
    return value


def repository_env(directory):
    """Resolve the main checkout's dotenv without reading credential values."""
    try:
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0: return None
    path = Path(result.stdout.strip()).parent / '.env'
    return path if path.is_file() else None


def dotenv_declares(path, name):
    """Check assignment names only; leave credential parsing to the transport."""
    pattern = re.compile(r'^\s*(?:export\s+)?' + re.escape(name) + r'\s*=')
    try:
        with path.open(encoding='utf-8') as stream:
            return any(pattern.match(line) for line in stream)
    except OSError:
        return False


def _descriptor_config(path,descriptor,previous_binding=None):
    """Choose source defaults or an existing generic file-backed descriptor.

    Inline source connections never consult the former per-machine memory JSON.
    Only a nonsecret URL override is read; bearer environment values stay opaque.
    """
    if set(descriptor)=={'server_name','connection','url_env'}:
        config=descriptor['connection'];url_env=descriptor['url_env']
        if not isinstance(config,dict) or not isinstance(url_env,str) or not IDENTIFIER.fullmatch(url_env):
            raise ValueError(f'Invalid source MCP connection: {path}')
        auth_env=config.get('bearer_token_env_var')
        if not isinstance(auth_env,str) or not IDENTIFIER.fullmatch(auth_env) or 'bearer_token_file' in config:
            raise ValueError(f'Source MCP connection requires environment authentication: {path}')
        if url_env==auth_env:raise ValueError(f'URL and credential environment names must differ: {path}')
        if type(config.get('enabled',True)) is not bool:raise ValueError(f'Invalid source MCP enable flag: {path}')
        if config.get('bearer_token_env_file') == '.env':
            # The installer records only a path. The transport reads the secret at runtime.
            env_file = repository_env(path.parent)
            config = {key:value for key,value in config.items() if key != 'bearer_token_env_file'}
            if env_file is not None and dotenv_declares(env_file, auth_env):
                config = {**config, 'bearer_token_env_file': str(env_file)}
        if config.get('enabled',True) and auth_env not in set(os.environ) and not config.get('bearer_token_env_file'):
            if previous_binding is None:return None,path
            prior=_object(previous_binding)
            if prior.get('server_name')!=descriptor['server_name'] or not isinstance(prior.get('connection'),dict):
                raise ValueError('Existing MCP binding is invalid; refusing credential-free migration')
            return {**prior['connection'],'capture':prior.get('capture',{'enabled':False})},previous_binding
        return {**config,'url':os.environ.get(url_env,config.get('url'))},path
    if set(descriptor)!={'server_name','config_file','config_file_env'}:
        raise ValueError(f'Invalid MCP descriptor fields: {path}')
    env,default=descriptor['config_file_env'],descriptor['config_file']
    if not isinstance(env,str) or not IDENTIFIER.fullmatch(env) or not isinstance(default,str) or not default:
        raise ValueError(f'Invalid MCP configuration source: {path}')
    config_path=Path(os.environ.get(env,default)).expanduser()
    if not config_path.is_absolute():raise ValueError(f'MCP config_file must be absolute: {path}')
    return (_object(config_path),config_path) if config_path.is_file() else (None,config_path)


def _connection(module,previous_binding=None):
    path=module/'mcp'/DESCRIPTOR
    if not path.is_file():return None
    descriptor=_object(path);name=descriptor.get('server_name')
    if not isinstance(name,str) or not SERVER_NAME.fullmatch(name):raise ValueError(f'Invalid MCP server_name: {path}')
    config,config_path=_descriptor_config(path,descriptor,previous_binding)
    if config is None:return None
    if set(config) - {'enabled', 'url', 'bearer_token_env_var', 'bearer_token_file', 'bearer_token_env_file', 'capture', 'tool_timeout_sec'}:
        raise ValueError(f'Unsupported MCP connection fields: {config_path}')
    if type(config.get('enabled', True)) is not bool:
        raise ValueError(f'MCP enabled must be boolean: {config_path}')
    if not config.get('enabled', True):
        return None
    url = config.get('url')
    if not isinstance(url, str) or len(url) > 2048 or any(char.isspace() for char in url):
        raise ValueError(f'MCP URL must be a bounded URL: {config_path}')
    parsed = urlsplit(url)
    try:
        loopback = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        loopback = False
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.scheme not in ('http', 'https')
            or parsed.scheme == 'http' and not loopback):
        raise ValueError(f'MCP URL requires HTTPS (HTTP only on loopback), without URL credentials/query/fragment: {config_path}')
    # Validate port without echoing a possibly secret-bearing value.
    try:
        parsed.port
    except ValueError:
        raise ValueError(f'Invalid MCP URL port: {config_path}') from None
    tool_timeout = config.get('tool_timeout_sec', 1800)
    if type(tool_timeout) is not int or not 1 <= tool_timeout <= 1800:
        raise ValueError(f'MCP tool_timeout_sec must be an integer in 1..1800: {config_path}')
    native = {'url': url, 'tool_timeout_sec': tool_timeout}
    token_file = config.get('bearer_token_file')
    if token_file is not None:
        if not isinstance(token_file, str) or not Path(token_file).expanduser().is_absolute():
            raise ValueError(f'MCP bearer_token_file must be absolute: {config_path}')
        if config.get('bearer_token_env_var') is not None or config.get('bearer_token_env_file') is not None:
            raise ValueError(f'Use only one MCP credential source: {config_path}')
        native['bearer_token_file'] = str(Path(token_file).expanduser())
    capture = config.get('capture', {})
    if not isinstance(capture, dict) or set(capture) - {'enabled','actor_id','scope','state_dir'}:
        raise ValueError(f'Invalid MCP capture settings: {config_path}')
    if type(capture.get('enabled', False)) is not bool:
        raise ValueError(f'MCP capture.enabled must be boolean: {config_path}')
    if capture.get('enabled'):
        for field in ('actor_id','scope'):
            value = capture.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f'MCP capture.{field} must be explicit: {config_path}')
        state_dir = capture.get('state_dir')
        if not isinstance(state_dir, str) or not Path(state_dir).expanduser().is_absolute():
            raise ValueError(f'MCP capture.state_dir must be explicit and absolute: {config_path}')
        native['capture'] = {**capture, 'state_dir': str(Path(state_dir).expanduser())}
    token = config.get('bearer_token_env_var')
    if token is not None:
        if not isinstance(token, str) or not IDENTIFIER.fullmatch(token):
            raise ValueError(f'MCP bearer_token_env_var must name an environment variable: {config_path}')
        native['bearer_token_env_var'] = token
    env_file = config.get('bearer_token_env_file')
    if env_file is not None:
        if not token or not isinstance(env_file, str) or not Path(env_file).is_absolute():
            raise ValueError(f'MCP bearer_token_env_file requires an absolute path and environment name: {config_path}')
        native['bearer_token_env_file'] = env_file
    return name, native


def _region(text):
    if text.count(OPEN) != text.count(CLOSE) or text.count(OPEN) > 1:
        raise ValueError('Invalid module-owned MCP markers; config unchanged')
    if OPEN not in text:
        return text, '', ''
    start, end = text.index(OPEN), text.index(CLOSE)
    if end < start:
        raise ValueError('Invalid module-owned MCP marker order; config unchanged')
    finish = end + len(CLOSE)
    if finish < len(text) and text[finish] == '\n': finish += 1
    return text[:start], text[start + len(OPEN):end], text[finish:]


def _native(config, harness, helper, python):
    if config.get('bearer_token_env_file'):
        command = shlex.join([python, str(helper), '--env-var', config['bearer_token_env_var'],
                              '--env-file', config['bearer_token_env_file']])
        if harness == 'codex':
            return {'url':config['url'], 'tool_timeout_sec':config['tool_timeout_sec'], 'http_headers_helper':command}
        return {'type':'http', 'url':config['url'], 'headersHelper':command}
    if harness == 'codex':
        value = {key:config[key] for key in ('url','bearer_token_env_var','tool_timeout_sec') if key in config}
        if config.get('bearer_token_file'):
            value['http_headers_helper'] = shlex.join([python, str(helper), config['bearer_token_file']])
        return value
    value = {'type': 'http', 'url': config['url']}
    if config.get('bearer_token_env_var'):
        value['headers'] = {'Authorization': 'Bearer ${' + config['bearer_token_env_var'] + '}'}
    if config.get('bearer_token_file'):
        value['headersHelper'] = shlex.join([python, str(helper), config['bearer_token_file']])
    return value


@dataclass
class Plan:
    path: Path | None
    harness: str
    desired: dict
    previous: dict
    modules: dict
    connections: dict

    def updated(self):
        if self.path is None: return None
        text = self.path.read_text(encoding='utf-8') if self.path.is_file() else ''
        if self.harness == 'codex':
            head, owned, tail = _region(text)
            parsed = tomllib.loads(owned)
            expected = {'mcp_servers':self.previous} if self.previous else {}
            if parsed != expected:
                raise ValueError('Module-owned MCP server was modified; config unchanged')
            outside = tomllib.loads(head + tail).get('mcp_servers', {})
            for name in self.desired:
                if name in outside:
                    raise ValueError(f'MCP server {name} belongs to another configuration; config unchanged')
            lines = []
            for name, config in sorted(self.desired.items()):
                lines.append('[mcp_servers.' + json.dumps(name) + ']')
                lines.extend(f'{key} = {json.dumps(value)}' for key, value in sorted(config.items()))
                lines.append('')
            block = OPEN + '\n' + '\n'.join(lines) + CLOSE + '\n' if lines else ''
            if owned or OPEN in text:
                updated = head + block + tail
            else:
                separator = '' if not text or text.endswith('\n\n') else ('\n' if text.endswith('\n') else '\n\n')
                updated = text + separator + block
            tomllib.loads(updated)  # Reject incompatible foreign TOML before any profile mutation.
            return updated
        config = json.loads(text) if text.strip() else {}
        if not isinstance(config, dict) or not isinstance(config.get('mcpServers', {}), dict):
            raise ValueError('MCP settings must contain an mcpServers object')
        servers = dict(config.get('mcpServers', {}))
        for name, expected in self.previous.items():
            if servers.get(name) != expected:
                raise ValueError(f'Module-owned MCP server {name} was modified; config unchanged')
            del servers[name]
        for name in self.desired:
            if name in servers:
                raise ValueError(f'MCP server {name} belongs to another configuration; config unchanged')
        servers.update(self.desired)
        if servers: config['mcpServers'] = servers
        else: config.pop('mcpServers', None)
        return json.dumps(config, ensure_ascii=False, indent=2) + '\n'

    def apply(self, write_atomic):
        updated = self.updated()
        if updated is not None:
            before = self.path.read_text(encoding='utf-8') if self.path.is_file() else None
            if before != updated: write_atomic(self.path, updated)

    def record(self, slug):
        if slug not in self.modules: return None
        name = self.modules[slug]
        return {'settings_file': str(self.path), 'name': name, 'config': self.desired[name]}

    def binding(self, slug):
        record = self.record(slug)
        if record is None: return None
        return {'server_name': record['name'], 'harness': self.harness,
                'connection_status': 'configured_not_verified', 'capture_status': 'not_configured',
                'endpoint_sha256': hashlib.sha256(record['config']['url'].encode()).hexdigest(),
                'connection': {k:v for k,v in self.connections[slug].items() if k != 'capture'},
                'capture': self.connections[slug].get('capture', {'enabled': False})}


def prepare(plan, settings_root, harness, previous, settings_file=None, python=None):
    desired, modules, connections = {}, {}, {}
    python = python or sys.executable
    for target, slug in plan:
        prior_binding=(settings_root/'jarvis/modules'/slug/'mcp'/BINDING
            if previous.get(slug,{}).get('mcp') else None)
        entry = _connection(target.path,prior_binding)
        if entry is None: continue
        name, config = entry
        if name in desired:
            raise ValueError(f'Multiple modules own MCP server {name}')
        helper = settings_root / 'jarvis' / 'modules' / slug / 'lib' / 'headers.py'
        if (config.get('bearer_token_file') or config.get('bearer_token_env_file')) and not (target.path / 'lib' / 'headers.py').is_file():
            raise ValueError('MCP token-file binding requires module lib/headers.py helper')
        desired[name] = _native(config, harness, helper.resolve(), python)
        modules[slug] = name
        connections[slug] = config
    old = [value['mcp'] for value in previous.values() if value.get('mcp')]
    if not desired and not old:
        return Plan(None, harness, {}, {}, {}, {})
    if harness == 'codex':
        if settings_file is not None:
            raise ValueError('--mcp-settings-file is for Claude; Codex uses its selected config.toml')
        path = settings_root / 'config.toml'
    else:
        if settings_file is None:
            raise ValueError('Claude MCP requires explicit --mcp-settings-file (user scope: ~/.claude.json)')
        path = Path(settings_file).expanduser().resolve()
        if path == (settings_root / 'settings.json').resolve():
            raise ValueError('Claude MCP file must differ from hook settings.json')
    if any(Path(record['settings_file']).resolve() != path.resolve() for record in old):
        raise ValueError('MCP settings file changed; remove prior owned bindings before migration')
    previous_native = {record['name']: record['config'] for record in old}
    result = Plan(path, harness, desired, previous_native, modules, connections)
    result.updated()  # ownership/schema preflight before the installer writes anything
    return result
