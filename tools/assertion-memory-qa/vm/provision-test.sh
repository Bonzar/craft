#!/bin/bash
set -euo pipefail
umask 077
[[ "$EUID" == 0 && $# -le 1 ]]
name=assertion-test-deploy
root=/srv/assertion-release/test
config=/etc/assertion-release/test
if command -v ss >/dev/null && ss -H -ltn '( sport = :8891 or sport = :8893 )' | grep -q .; then
  echo 'TEST ports already occupied. Leaving existing processes untouched.' >&2
  exit 2
fi
[[ ! -e "$root" && ! -L "$root" && ! -e "$config" && ! -L "$config" ]]
! getent passwd "$name" >/dev/null
[[ -x /usr/bin/python3 ]] && /usr/bin/python3 -c 'import venv,ensurepip' >/dev/null
if [[ $# == 1 ]]; then
  pubkey_file=$1
  [[ -f "$pubkey_file" ]]
  IFS= read -r key < "$pubkey_file"
  [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]{60,120}(\ [A-Za-z0-9_@.+-]{1,128})?$ ]]
  ssh-keygen -lf "$pubkey_file" >/dev/null
  [[ $(wc -l < "$pubkey_file") == 1 ]]
fi
install -d -m 0755 /srv/assertion-release /etc/assertion-release
useradd --system --user-group --home-dir "$root" --shell /bin/bash "$name"
install -d -m 0700 -o "$name" -g "$name" "$root" "$root/releases" "$root/state"
install -d -m 0755 "$config"
cat > "$config/receive.py" <<'RECEIVER_PY'
#!/usr/bin/python3
"""Environment-scoped release receiver. No shells or remote arguments executed."""
import fcntl, hashlib, hmac, json, os, re, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath

MAX_BYTES = 96 * 1024**2
MAX_EXPANDED = 350 * 1024**2
MAX_FILES = 20_000
COMMAND = re.compile(r'deploy-release (test|production) ([a-f0-9]{40}) ([a-f0-9]{64})')
ROOT = Path('/srv/assertion-release')
CONFIG = Path('/etc/assertion-release')
RECEIPT = dict(status='rejected')


def stage(stream,destination,expected):
    source=destination/'incoming.tar.gz'
    digest=hashlib.sha256()
    size=0
    with source.open('xb') as out:
        for chunk in iter(lambda: stream.read(1024*1024),b''):
            size+=len(chunk)
            if size>MAX_BYTES: raise ValueError('archive_size')
            digest.update(chunk)
            out.write(chunk)
    if not hmac.compare_digest(digest.hexdigest(),expected): raise ValueError('archive_digest')
    release=destination/'release'
    release.mkdir(mode=0o750)
    total=0
    count=0
    seen=set()
    with tarfile.open(source,mode='r|gz') as archive:
        for entry in archive:
            count+=1
            if count>MAX_FILES: raise ValueError('member_count')
            if entry.isdir() and entry.name in ('.','./'):
                continue
            path=PurePosixPath(entry.name)
            if (path.is_absolute() or not path.parts or any(p in {'.','..'} or (p.startswith('.') and p!='.gitignore') for p in path.parts)
                    or any(not re.fullmatch(r'[A-Za-z0-9_.+@ -]{1,180}',p) for p in path.parts)
                    or str(path) in seen):
                raise ValueError('member_path')
            if any(p.endswith(('.db','.sqlite','.sqlite3','.pem','.key','.db-wal','.sqlite3-wal','.env'))
                    or p in {'__pycache__','node_modules','secrets'} for p in path.parts):
                raise ValueError('member_private')
            seen.add(str(path))
            target=release.joinpath(*path.parts)
            if entry.isdir():
                target.mkdir(parents=True,exist_ok=True,mode=0o750)
                continue
            if not entry.isfile() or getattr(entry,'sparse',None) is not None: raise ValueError('member_type')
            total+=entry.size
            if total>MAX_EXPANDED: raise ValueError('expanded_size')
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
            with target.open('xb') as out:
                with archive.extractfile(entry) as incoming:
                    shutil.copyfileobj(incoming,out,1024*1024)
            target.chmod(0o750 if entry.mode&0o111 else 0o640)
    if not seen: raise ValueError('empty_archive')
    return release


def verify_manifest(payload):
    manifest=payload/'SHA256SUMS'
    if not manifest.is_file() or manifest.stat().st_size>5*1024**2: raise ValueError('missing_manifest')
    expected={}
    for line in manifest.read_text().splitlines():
        digest,sep,location=line.partition('  ')
        path=PurePosixPath(location)
        if (not sep or not re.fullmatch('[a-f0-9]{64}',digest) or path.is_absolute()
                or '..' in path.parts or not path.parts or path in expected):
            raise ValueError('manifest_invalid')
        expected[path]=digest
    actual={PurePosixPath(path.relative_to(payload)) for path in payload.rglob('*')
            if path!=manifest and path.is_file()}
    if actual!=expected.keys(): raise ValueError('manifest_membership')
    for name,digest in expected.items():
        with (payload/name).open('rb') as stream:
            if not hmac.compare_digest(hashlib.file_digest(stream,'sha256').hexdigest(),digest):
                raise ValueError('manifest_digest')


def run_hook(script,action,location):
    result=subprocess.run(['/bin/bash',str(script),action,str(location)],
        cwd=location,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':os.environ['HOME'],
          'XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}','LANG':'C.UTF-8'},timeout=300)
    if result.returncode!=0: raise RuntimeError(action)


def swap(root,destination):
    current=root/'current'
    if destination is None:
        current.unlink(missing_ok=True)
    else:
        with tempfile.TemporaryDirectory(prefix='.link-',dir=root) as temp:
            pointer=Path(temp)/'current'
            pointer.symlink_to(destination)
            os.replace(pointer,current)


def deploy(environment, revision, sha256, stream, *, root=None, config=None, hook=run_hook):
    root=ROOT/environment if root is None else root
    config=CONFIG/environment if config is None else config
    release=root/'releases'/f'{revision}-{sha256}'
    script=config/'service-hook.sh'
    if not script.is_file(): raise ValueError('missing_hook')
    with (root/'deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='.incoming-',dir=root) as scratch:
            payload=stage(stream,Path(scratch),sha256)
            if (payload/'prototypes/assertion_memory/http_server.py').is_file():
                app=payload
            elif (payload/'source/prototypes/assertion_memory/http_server.py').is_file():
                app=payload/'source'
            else:
                raise ValueError('source_layout')
            if not (app/'apps/graph_viewer/web/dist/index.html').is_file():
                raise ValueError('compiled_viewer_missing')
            if not (payload/'REVISION').is_file() or (payload/'REVISION').read_text().strip()!=revision:
                raise ValueError('revision_mismatch')
            if not (payload/'QA_PROFILE').is_file() or (payload/'QA_PROFILE').read_text().strip()!='assertion-memory-synthetic-qa-v1':
                raise ValueError('release_profile')
            verify_manifest(payload)
            if not release.exists():
                os.rename(payload,release)
                try:
                    if environment=='test':
                        hook(script,'prepare',release)
                except BaseException:
                    shutil.rmtree(release)
                    raise
            if environment=='production':
                gate=config/'allow-activation'
                # No hook/dependencies/source execution on an unapproved production stage.
                if not gate.is_file() or gate.is_symlink() or gate.stat().st_uid != 0 or gate.stat().st_mode&0o022:
                    return 'staged'
                if gate.read_text().strip()!=f'{revision} {sha256}':
                    return 'staged'
                hook(script,'prepare',release)
            current=root/'current'
            if current.exists() and not current.is_symlink(): raise ValueError('invalid_current')
            old=os.readlink(current) if current.is_symlink() else None
            if old is not None and not Path(old).is_relative_to(root/'releases'):
                raise ValueError('invalid_current')
            swap(root,release)
            try:
                hook(script,'restart',release)
                hook(script,'smoke',release)
            except BaseException:
                swap(root,old)
                hook(script,'restore' if old is not None else 'stop',Path(old) if old else release)
                raise
        return 'ok'


def main():
    try:
        import pwd
        if len(sys.argv)!=2 or sys.argv[1] not in ('test','production'):raise ValueError('expected_environment')
        env=sys.argv[1]
        if pwd.getpwuid(os.getuid()).pw_name != {'test':'assertion-test-deploy','production':'assertion-production-deploy'}[env]:
            raise ValueError('wrong_account')
        match=COMMAND.fullmatch(os.environ.get('SSH_ORIGINAL_COMMAND',''))
        if not match or match[1]!=env:raise ValueError('command_denied')
        _,revision,sha=match.groups()
        status=deploy(env,revision,sha,sys.stdin.buffer)
        print(json.dumps(dict(status=status,revision=revision,sha256=sha,environment=env),separators=(',',':')))
    except Exception as exc:
        code=str(exc) if isinstance(exc,(ValueError,RuntimeError)) and re.fullmatch(r'[a-z_]+',str(exc)) else 'deployment_failed'
        print(json.dumps(dict(status='rejected',error=code),separators=(',',':')))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
RECEIVER_PY
cat > "$config/service-hook.sh" <<'HOOK_SH'
#!/bin/bash
set -euo pipefail
umask 077
[[ $(id -un) == assertion-test-deploy && $# == 2 ]]
root=/srv/assertion-release/test
state=$root/state
release=$2
units=(assertion-test-deploy-graph.service assertion-test-deploy-mcp.service)
case "$1" in
prepare)
  [[ "$release" == "$root/releases/"* && -f "$release/requirements-graph-lexical.txt" ]]
  cd "$release"
  python3 -m venv venv
  py="$release/venv/bin/python"
  "$py" -m pip install --disable-pip-version-check --only-binary=:all: --no-cache-dir \
    -r "$release/prototypes/assertion_memory/requirements-mcp.txt" -r "$release/requirements-graph-lexical.txt"
  fixture=$(mktemp -d "$root/.fixture.XXXXXXXX")
  trap 'rm -rf -- "$fixture"' EXIT
  "$py" -B synthetic_fixture.py "$fixture/fictional.sqlite3"
  "$py" -B smoke_local.py "$fixture/fictional.sqlite3"
  if [[ ! -f "$state/fictional.sqlite3" ]]; then
    mv -- "$fixture/fictional.sqlite3" "$state/fictional.sqlite3"
  fi
  ;;
restart|restore)
  systemctl --user restart "${units[@]}"
  ;;
smoke)
  cd "$release"
  for attempt in 1 2 3 4 5; do
    if "$release/venv/bin/python" -B live-smoke.py "$state"; then exit 0; fi
    sleep 1
  done
  exit 1
  ;;
stop)
  systemctl --user stop "${units[@]}"
  ;;
*)
  exit 2
  ;;
esac
HOOK_SH
chmod 0644 "$config/receive.py"
chmod 0755 "$config/service-hook.sh"
if [[ $# == 1 ]]; then
  install -d -m 0700 -o "$name" -g "$name" "$root/.ssh"
  printf 'restrict,command="/usr/bin/python3 -B /etc/assertion-release/test/receive.py test" %s\n' "$key" > "$root/.ssh/authorized_keys"
  chown "$name:$name" "$root/.ssh/authorized_keys"
fi
install -d -m 0700 -o "$name" -g "$name" "$root/.config" "$root/.config/systemd" "$root/.config/systemd/user"
/usr/bin/python3 - "$root/state" <<'PYTHON'
import json, pathlib, secrets, sys
state=pathlib.Path(sys.argv[1])
for key in ('mcp-token','graph-password'):
    (state/key).write_text(secrets.token_urlsafe(40)+'\n')
(state/'http.json').write_text(json.dumps({'service':{'db':str(state/'fictional.sqlite3'),
 'provider':'none','embedding_mode':'lexical','processing_session_ids':[]},
 'public_host':'localhost','port':8893,'token_file':str(state/'mcp-token')}))
PYTHON
chown -R "$name:$name" "$root/state"
cat > "$root/.config/systemd/user/assertion-test-deploy-graph.service" <<'UNIT'
[Unit]
Description=Assertion Memory TEST graph (isolated synthetic database)
[Service]
Type=simple
UMask=0077
WorkingDirectory=%h/current
NoNewPrivileges=yes
Restart=on-failure
ExecStart=%h/current/venv/bin/python -B -m apps.graph_viewer.server --source %h/state/fictional.sqlite3 --state-dir %h/state/graph-state --password-file %h/state/graph-password --static-dir %h/current/apps/graph_viewer/web/dist --port 8891 --synthetic-fixture
UNIT
cat > "$root/.config/systemd/user/assertion-test-deploy-mcp.service" <<'UNIT'
[Unit]
Description=Assertion Memory TEST MCP (isolated synthetic database)
[Service]
Type=simple
UMask=0077
WorkingDirectory=%h/current
NoNewPrivileges=yes
Restart=on-failure
ExecStart=%h/current/venv/bin/python -B -m prototypes.assertion_memory.http_server --config %h/state/http.json
UNIT
# No production systemd service, data, port, DNS, or nginx modifications.
loginctl enable-linger "$name"
id=$(id -u "$name")
systemctl start "user@$id.service"
runuser -u "$name" -- env XDG_RUNTIME_DIR="/run/user/$id" systemctl --user daemon-reload
echo 'TEST provisioned; submit approved archive as assertion-test-deploy (no production changes).'
