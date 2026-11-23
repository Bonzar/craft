#!/bin/bash
# Materialize reviewed, source-only bootstrap under a trusted operator directory.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
[[ $# == 1 && $1 == /* && $1 != */ ]]
target=$1
[[ -d "${target%/*}" && ! -L "${target%/*}" ]]
mkdir -m 0700 -- "$target"
mkdir -m 0700 -- "$target/payload"
cat > "$target/install-visual-test.sh" <<'ASSERTION_VISUAL_FILE_EOF_1_b33754'
#!/bin/bash
# Visual TEST only; replays verify exact managed files and never modify existing accounts.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
mode="${1:-}"; shift || true
[[ $EUID == 0 && ( $mode == --check || $mode == --apply ) && $# == 8 ]]
expected_hostname=$8
[[ "$expected_hostname" =~ ^[[:alnum:]][[:alnum:]._-]{0,62}$ && $(hostname -s) == "$expected_hostname" ]]
keys="$(realpath -e -- "$1")"; check_user=$2 check_host=$3 remote_addr=$4 local_addr=$5 local_port=$6 approval=$7
[[ "$check_user" =~ ^[[:alpha:]_][[:alnum:]_-]{0,63}$ && "$check_host" =~ ^[[:alnum:]._-]{1,253}$ ]]
[[ "$remote_addr" =~ ^[0-9a-fA-F:.]+$ && "$local_addr" =~ ^[0-9a-fA-F:.]+$ && "$local_port" =~ ^[0-9]{1,5}$ ]]
[[ "$approval" == VERIFIED-SECOND-ADMIN || ( "$approval" == CHECK-ONLY && "$mode" == --check ) ]]
getent passwd "$check_user" >/dev/null
base="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
deploy=assertion-visual-test-deploy reader=assertion-visual-test-read group=assertion-visual-test
[[ "$check_user" != "$deploy" && "$check_user" != "$reader" ]]
config=/etc/assertion-memory-visual/test
state=/var/lib/assertion-memory-visual/test
home=/var/empty/assertion-visual-test-deploy
trusted=/etc/assertion-release/shared/release_core.py
keydir=/etc/ssh/assertion-visual
main=/etc/ssh/sshd_config
unit=/etc/systemd/system/assertion-memory-visual-test.service
modecheck() { [[ ! -L "$1" && "$(stat -c '%u:%g:%a' -- "$1")" == "$2" ]]; }
mintrusted() { [[ ! -L "$1" && -d "$1" && "$(stat -c '%u:%a' "$1")" =~ ^0:(700|750|755)$ ]]; }
expect_absence() {
  local code="$1"; shift
  local actual=0
  "$@" >/dev/null || actual=$?
  [[ "$actual" == "$code" ]]
}
for d in "$base" "$base/payload" "$keys"; do mintrusted "$d"; done
for input in transport.pub signer.pub; do
  file="$keys/$input"
  [[ -f "$file" ]]; modecheck "$file" '0:0:644'
  [[ $(wc -l < "$file") == 1 ]]
  grep -Eq '^ssh-ed25519 [A-Za-z0-9+/]{68}={0,2}$' "$file"
  ssh-keygen -E sha256 -lf "$file" | grep -Eq '^256 SHA256:[A-Za-z0-9+/]{43} .* \(ED25519\)$'
done
transport="$(<"$keys/transport.pub")"; signer="$(<"$keys/signer.pub")"
[[ "$transport" != "$signer" ]]
for prior in /etc/ssh/assertion-release/{test,production,prod}.authorized_keys /etc/ssh/assertion-visual/prod.authorized_keys /etc/assertion-memory-visual/prod/allowed-signers /etc/assertion-release/{test,production,prod}/allowed-signers; do
  if [[ -e "$prior" || -L "$prior" ]]; then
    [[ ! -L "$prior" && -f "$prior" && "$(stat -c '%u:%a' "$prior")" =~ ^0:(600|644)$ ]]
    expect_absence 1 grep -Fq -- "$transport" "$prior"
    expect_absence 1 grep -Fq -- "$signer" "$prior"
  fi
done
[[ -f "$trusted" && ! -L "$trusted" && "$(stat -c '%u:%a' "$trusted")" =~ ^0:(644|755)$ ]]
mintrusted /etc/assertion-release/shared
[[ "$(sha256sum "$trusted" | cut -c1-64)" == "$(<"$base/shared-release-core.sha256")" ]]
(cd "$base/payload"; sha256sum -c --strict -- "$base/PAYLOAD.sha256" >/dev/null)
sshd -t
systemctl is-active --quiet ssh.service
[[ -f "$main" && ! -L "$main" ]]; modecheck "$main" '0:0:644'
[[ $(df -PB1 /var/lib | awk 'NR==2 {print $4}') -ge 33554432 ]]
for p in /etc/assertion-memory-visual /var/lib/assertion-memory-visual /var/empty "$keydir"; do
  if [[ -e "$p" || -L "$p" ]]; then [[ -d "$p" ]]; modecheck "$p" '0:0:755'; fi
done
tmp="$(mktemp -d /run/assertion-visual-test.XXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT
context() { printf 'user=%s,host=%s,addr=%s,laddr=%s,lport=%s' "$1" "$check_host" "$remote_addr" "$local_addr" "$local_port"; }

protected_users=("$check_user" assertion-test-deploy assertion-production-deploy assertion-prod-deploy assertion-visual-prod-deploy)
for u in "${protected_users[@]}"; do sshd -T -f "$main" -C "$(context "$u")" >"$tmp/old.$u"; done
grep -qx 'usepam yes' "$tmp/old.$check_user"
before_main="$(sha256sum "$main" | cut -c1-64)"
start_mark='# BEGIN assertion memory visual TEST'
end_mark='# END assertion memory visual TEST'
created=0
if grep -qFx "$start_mark" "$main"; then
  [[ $(grep -Fxc "$start_mark" "$main") == 1 && $(grep -Fxc "$end_mark" "$main") == 1 ]]
  awk -v s="$start_mark" -v e="$end_mark" '$0==s{on=1} on{print} $0==e&&on{exit}' "$main" >"$tmp/match"
  cmp -s "$tmp/match" "$base/payload/ssh-match.fragment"
  for p in "$config" "$state" "$state/releases" "$home"; do [[ -d "$p" && ! -L "$p" ]]; done
  modecheck "$config" '0:0:755'; modecheck "$home" '0:0:755'
  for p in "$state" "$state/releases"; do
    [[ "$(stat -c '%U:%G:%a' "$p")" == "$deploy:$group:2750" ]]
  done
  groupid="$(getent group "$group" | cut -d: -f3)"; [[ "$groupid" =~ ^[0-9]+$ ]]
  deploy_identity="$(getent passwd "$deploy")"; reader_identity="$(getent passwd "$reader")"
  [[ "$deploy_identity" == "$deploy":*:"$groupid":*:"$home":"$config"/receiver-login ]]
  [[ "$reader_identity" == "$reader":*:"$groupid":*:/nonexistent:/usr/sbin/nologin ]]
  for input in visual_receive.py visual_auth.py visual_policy.py asset-origin.py receiver-login; do
    perm=644; [[ "$input" != receiver-login ]] || perm=755
    modecheck "$config/$input" "0:0:$perm"
    cmp -s "$config/$input" "$base/payload/$input"
  done
  modecheck "$config/allowed-signers" '0:0:644'; modecheck "$keydir/test.authorized_keys" '0:0:644'
  printf 'visual-test %s\n' "$signer" | cmp -s - "$config/allowed-signers"
  printf 'restrict %s\n' "$transport" | cmp -s - "$keydir/test.authorized_keys"
  modecheck "$unit" '0:0:644'; cmp -s "$unit" "$base/payload/${unit##*/}"
  cp -p -- "$main" "$tmp/ssh"
else
  expect_absence 1 grep -qFx "$end_mark" "$main"
  expect_absence 2 getent group "$group"
  for u in "$deploy" "$reader"; do expect_absence 2 getent passwd "$u"; done
  for p in "$config" "$state" "$home" "$keydir/test.authorized_keys" "$unit"; do [[ ! -e "$p" && ! -L "$p" ]]; done
  [[ -z "$(ss -H -lnt 'sport = :8829')" ]]
  unit_active_result=0
  systemctl is-active --quiet assertion-memory-visual-test.service || unit_active_result=$?
  [[ $unit_active_result == 3 || $unit_active_result == 4 ]]
  cat -- "$main" > "$tmp/ssh"
  printf '\n' >>"$tmp/ssh"
  cat -- "$base/payload/ssh-match.fragment" >>"$tmp/ssh"
  created=1
fi
sshd -t -f "$tmp/ssh"
for u in "${protected_users[@]}"; do sshd -T -f "$tmp/ssh" -C "$(context "$u")" | cmp -s - "$tmp/old.$u"; done
sshd -T -f "$tmp/ssh" -C "$(context "$deploy")" >"$tmp/visual"
for repair in 'authenticationmethods publickey' 'pubkeyauthentication yes' 'passwordauthentication no' 'kbdinteractiveauthentication no' 'authorizedkeyscommand none' "authorizedkeysfile $keydir/test.authorized_keys" "forcecommand /usr/bin/python3 -I -B $config/visual_receive.py test" 'disableforwarding yes' 'permittty no' 'x11forwarding no' 'permituserrc no'; do
  grep -Fqx "$repair" "$tmp/visual"
done
if [[ $mode == --check || $created == 0 ]]; then
  echo 'Visual TEST plan verified; no files, services, or releases changed.'
  exit 0
fi

# Create the disconnected state first. No token, start, Nginx, gateway or asset deploy here.
for p in /etc/assertion-memory-visual /var/lib/assertion-memory-visual /var/empty "$keydir"; do
  [[ -d "$p" ]] || install -d -o root -g root -m 0755 "$p"
done
install -d -o root -g root -m 0755 "$config" "$home"
for i in visual_receive.py visual_auth.py visual_policy.py asset-origin.py; do install -o root -g root -m 0644 "$base/payload/$i" "$config/$i"; done
install -o root -g root -m 0755 "$base/payload/receiver-login" "$config/receiver-login"
groupadd --system "$group"
useradd --system --gid "$group" --home-dir "$home" --no-create-home --shell "$config/receiver-login" "$deploy"
useradd --system --gid "$group" --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$reader"
install -d -o "$deploy" -g "$group" -m 2750 "$state" "$state/releases"
printf 'visual-test %s\n' "$signer" >"$tmp/allowed"
printf 'restrict %s\n' "$transport" >"$tmp/auth"
install -o root -g root -m 0644 "$tmp/allowed" "$config/allowed-signers"
install -o root -g root -m 0644 "$tmp/auth" "$keydir/test.authorized_keys"
install -o root -g root -m 0644 "$base/payload/${unit##*/}" "$unit"
[[ "$(sha256sum "$main" | cut -c1-64)" == "$before_main" ]]
backup="$(mktemp /etc/ssh/.assertion-visual-test-sshd-v0.XXXXXX)"
cat -- "$main" >"$backup"; chmod 0600 "$backup"
fresh="$(mktemp /etc/ssh/.assertion-visual-test-sshd-v1.XXXXXX)"
install -o root -g root -m 0644 "$tmp/ssh" "$fresh"
mv -fT -- "$fresh" "$main"
if ! sshd -t || ! systemctl reload ssh.service; then
  fresh="$(mktemp /etc/ssh/.assertion-visual-test-rollback.XXXXXX)"
  install -o root -g root -m 0644 "$backup" "$fresh"
  mv -fT -- "$fresh" "$main"
  sshd -t && systemctl reload ssh.service || true
  echo 'Visual TEST could not be enabled; original SSH config restored and files retained for review.' >&2
  exit 1
fi
echo "Visual TEST SSH receiver added; prior configuration backed up at $backup."
echo 'HTTP unit deliberately inactive, no token/gateway/routes configured, no release deployed.'
ASSERTION_VISUAL_FILE_EOF_1_b33754
cat > "$target/README.md" <<'ASSERTION_VISUAL_FILE_EOF_2_b33754'
# Initialize the isolated visual TEST receiver (no live deployment)

This installer prepares scoped SSH, read-only origin identity and
filesystem layout. No ports or listeners are started, no credentials
other than two public keys are installed, and no release is deployed.
Canonical product payload: commit af395ee3629e48fbaf1bd1e44e4bd7e6418be614.

Run the generator under a trusted operator account into a NEW 0700
directory whose parent is already controlled by that account.
For VM provisioning, the directory and its files must be controlled
by root. Pass the independently verified `hostname -s` as HOSTNAME
(never rely on auto-discovery for this comparison).

    ./install-visual-test.sh --check /ABS/KEYS ADMIN SSH_HOST SOURCE_ADDR DEST_ADDR DEST_PORT CHECK-ONLY HOSTNAME
    ./install-visual-test.sh --apply /ABS/KEYS ADMIN SSH_HOST SOURCE_ADDR DEST_ADDR DEST_PORT VERIFIED-SECOND-ADMIN HOSTNAME

SSH_HOST/addresses/port define the actual sshd comparison context,
not new network routes. KEYS contains distinct normalized transport.pub
and signer.pub files, root:root mode 0644. Each is one canonical
ssh-ed25519 OpenSSH line with no comment. Never supply private keys.
Keep a second independently tested admin session during installation.
Check-only does not change persistent configuration. An already
installed identical result is a no-op; conflicting or partial state
fails closed. No HTTP token, Gateway, existing admin access, other
scope, firewall or base service changes. No release execution or copy here.

The separate inactive systemd unit takes a root-only scoped asset
credential. Validate the exact TLS proxy separately before starting
that service and enabling CI. The common core is checked by hash and
never overwritten. Web/GitHub checks do not replace live VM validation.
ASSERTION_VISUAL_FILE_EOF_2_b33754
cat > "$target/shared-release-core.sha256" <<'ASSERTION_VISUAL_FILE_EOF_3_b33754'
c34cbc0ddf79064f694046299d3a3f20d2ed9370d6f5eec15ab1da36941a0b7c
ASSERTION_VISUAL_FILE_EOF_3_b33754
cat > "$target/PAYLOAD.sha256" <<'ASSERTION_VISUAL_FILE_EOF_4_b33754'
4b2050c97101d144314afc9e64ffd59fd1236d1f1b0ad8b05ce091577956f146  assertion-memory-visual-test.service
20e62f82de19d4869647d5fba5af051818d460b8cf7fb82108e26cc68bc477b3  asset-origin.py
fd1773f5f5c7b606be9218e88de17589482151af6ddaa84c9d94880c8ee93131  receiver-login
eb7942775e3dc57cdf6e230a41433dc8c20f9ced9a6a1bf07ff5131722881d36  ssh-match.fragment
64b3d5b224803c4d9105c79256e691d07d52df8ad2351e842e280540a6778407  visual_auth.py
4a26a7d8ab25b11d19a2141e9b9ec5c2f6f52ccd046b40e8182bdb630ff9335d  visual_policy.py
2eba60164452cd627c3eb965ae5c1b978a78ee1603502630ed219dc14e20b69e  visual_receive.py
ASSERTION_VISUAL_FILE_EOF_4_b33754
cat > "$target/payload/assertion-memory-visual-test.service" <<'ASSERTION_VISUAL_FILE_EOF_5_b33754'
[Unit]
Description=Assertion Memory private TEST visual assets (no database access)
After=network.target

[Service]
Type=simple
User=assertion-visual-test-read
Group=assertion-visual-test
ExecStart=/usr/bin/python3 -I -B /etc/assertion-memory-visual/test/asset-origin.py test 8829
LoadCredential=asset-token:/etc/assertion-memory-visual/test/asset-token
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET
RestrictSUIDSGID=yes
LockPersonality=yes
UMask=0027
Restart=on-failure

[Install]
WantedBy=multi-user.target
ASSERTION_VISUAL_FILE_EOF_5_b33754
cat > "$target/payload/asset-origin.py" <<'ASSERTION_VISUAL_FILE_EOF_6_b33754'
"""TEST-only HTTP transport for immutable visual assets; no DB or outbound access."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import stat
from threading import BoundedSemaphore

PATH = '/memory-visual-test-read'
ROOT = Path('/var/lib/assertion-memory-visual')
HEX = re.compile(r'[0-9a-f]{64}\Z')
ITEM = re.compile(r'(?:index\.html|assets/(?:graph\.worker-|index-)[A-Za-z0-9_-]+\.(?:js|css))\Z')
LIMIT = 2_400_000
BODY_LIMIT = 512
MIME = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}


def digest(data):
    return sha256(data).hexdigest()


def read_small(path, limit):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError('not a regular file')
        data = file.read(limit + 1)
        if len(data) > limit:
            raise ValueError('file exceeds bound')
        return data


def manifest(root, release):
    if not HEX.fullmatch(release):
        raise ValueError('invalid release')
    directory = root / 'releases' / release
    if directory.is_symlink() or (directory / 'assets').is_symlink():
        raise ValueError('invalid release path')
    body = read_small(directory / 'manifest.json', 1024)
    if digest(body) != release:
        raise ValueError('manifest changed')
    parsed = json.loads(body)
    if (not isinstance(parsed, dict) or len(parsed) != 4 or 'index.html' not in parsed
            or any(not ITEM.fullmatch(k) or not isinstance(v, str) or not HEX.fullmatch(v)
                   for k, v in parsed.items())):
        raise ValueError('invalid manifest')
    return directory, parsed


def current_release(root):
    """Read the shared journal receiver's atomic pointer, without following it."""
    target = Path(os.readlink(root / 'current'))
    if target.parent not in (Path('releases'), root.absolute() / 'releases'):
        raise ValueError('current escaped this visual environment')
    key = target.name
    if not HEX.fullmatch(key):
        raise ValueError('invalid visual release identity')
    return key


def make_server(root, token, port=8829, *, environment='test'):
    if environment not in ('test', 'prod') or not HEX.fullmatch(token):
        raise ValueError('invalid dedicated TEST token')

    route = f'/memory-visual-{environment}-read'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(7)

        def log_message(self, *_):
            pass

        def handle_one_request(self):
            self.request_version, self.command = 'HTTP/1.0', ''
            try:
                self.raw_requestline = self.rfile.readline(4097)
                if not self.raw_requestline:
                    self.close_connection = True
                    return
                if len(self.raw_requestline) > 4096:
                    self.send_error(414)
                    return
                if self.parse_request():
                    # Dispatch even unknown methods through the same narrow route rules.
                    self.dispatch()
                    self.wfile.flush()
            except OSError:
                self.close_connection = True

        def send_error(self, code, *_args, **_kwargs):
            self.respond(code)

        def respond(self, status, data=b'', mime=None):
            self.close_connection = True
            self.send_response_only(status)
            self.send_header('Cache-Control', 'private, no-store, no-transform')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if mime:
                self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            if data and getattr(self, 'command', '') != 'HEAD':
                self.wfile.write(data)

        def dispatch(self):
            raw = self.requestline.split()
            if len(raw) != 3 or raw[1] != route or self.command != 'POST':
                return self.respond(404)
            auth = self.headers.get_all('Authorization', [])
            got = re.fullmatch(r'Bearer ([0-9a-f]{64})', auth[0]) if len(auth) == 1 else None
            if not got or not hmac.compare_digest(got[1], token):
                return self.respond(401)
            if self.headers.get_all('Content-Type', []) != ['application/json']:
                return self.respond(415)
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get_all('Transfer-Encoding') or len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,9}', lengths[0]):
                return self.respond(400)
            length = int(lengths[0])
            if length > BODY_LIMIT:
                return self.respond(413)
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    return self.respond(400)
                value = json.loads(body)
            except (ValueError, OSError, RecursionError):
                return self.respond(404)
            if not isinstance(value, dict):
                return self.respond(400)
            try:
                if set(value) == {'op'} and value['op'] == 'current':
                    rel = current_release(root)
                    manifest(root, rel)
                    return self.respond(200, json.dumps({'digest': rel}, separators=(',', ':')).encode(), 'application/json')
                release, name = value.get('digest'), value.get('name')
                if (set(value) != {'op', 'digest', 'name'} or value['op'] != 'asset'
                        or not isinstance(release, str) or not HEX.fullmatch(release)
                        or not isinstance(name, str) or not ITEM.fullmatch(name)):
                    return self.respond(400)
                directory, files = manifest(root, release)
                if name not in files:
                    return self.respond(404)
                asset = read_small(directory / name, LIMIT)
                if digest(asset) != files[name]:
                    return self.respond(404)
                return self.respond(200, asset, MIME[Path(name).suffix])
            except (ValueError, OSError, RecursionError):
                return self.respond(404)

    class BoundedServer(ThreadingHTTPServer):
        slots = BoundedSemaphore(16)

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    return BoundedServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    import sys
    mode, port = sys.argv[1:]
    if mode not in ('test','prod') or not re.fullmatch(r'[1-9][0-9]{0,4}',port) or int(port)>65535:
        raise SystemExit('invalid isolated service settings')
    credential = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'asset-token'
    secret = read_small(credential, 66).decode('ascii').strip()
    with make_server(ROOT / mode, secret, int(port), environment=mode) as server:
        server.serve_forever()
ASSERTION_VISUAL_FILE_EOF_6_b33754
cat > "$target/payload/receiver-login" <<'ASSERTION_VISUAL_FILE_EOF_7_b33754'
#!/usr/bin/python3 -I
"""Only sshd's exact TEST receiver command, never an ordinary login shell."""
import os
import pwd
import sys

PYTHON = '/usr/bin/python3'
RECEIVER = '/etc/assertion-memory-visual/test/visual_receive.py'
HOME = '/var/empty/assertion-visual-test-deploy'
USER = 'assertion-visual-test-deploy'
ARGS = (PYTHON, '-I', '-B', RECEIVER, 'test')
FORCED = ' '.join(ARGS)


def main():
    # sshd execs the account login shell with exactly ['-c', ForceCommand].
    if sys.argv[1:] != ['-c', FORCED] or os.geteuid() != os.getuid() or os.getuid() == 0:
        return 126
    try:
        account = pwd.getpwuid(os.getuid())
    except KeyError:
        return 126
    if (account.pw_name, account.pw_dir) != (USER, HOME):
        return 126
    original = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    if len(original) > 160:
        return 126
    os.umask(0o027)
    os.chdir(HOME)
    os.execve(PYTHON, ARGS, {
        'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
        'LANG': 'C.UTF-8',
        'HOME': HOME,
        'USER': USER,
        'LOGNAME': USER,
        'SSH_ORIGINAL_COMMAND': original,
    })


if __name__ == '__main__':
    raise SystemExit(main())
ASSERTION_VISUAL_FILE_EOF_7_b33754
cat > "$target/payload/ssh-match.fragment" <<'ASSERTION_VISUAL_FILE_EOF_8_b33754'
# BEGIN assertion memory visual TEST
Match User assertion-visual-test-deploy
  AuthenticationMethods publickey
  PubkeyAuthentication yes
  PasswordAuthentication no
  KbdInteractiveAuthentication no
  AuthorizedKeysFile /etc/ssh/assertion-visual/test.authorized_keys
  AuthorizedKeysCommand none
  ForceCommand /usr/bin/python3 -I -B /etc/assertion-memory-visual/test/visual_receive.py test
  DisableForwarding yes
  PermitTTY no
  X11Forwarding no
  PermitUserRC no
# END assertion memory visual TEST
ASSERTION_VISUAL_FILE_EOF_8_b33754
cat > "$target/payload/visual_auth.py" <<'ASSERTION_VISUAL_FILE_EOF_9_b33754'
"""Visual signature policy; common code still owns SSH/TAR/activation."""
from pathlib import Path
import re
import subprocess
import tempfile

NAMESPACE = 'assertion-memory-visual'
COMMAND = re.compile(r'deploy-visual (test|prod) ([a-f0-9]{40}) ([a-f0-9]{64})\Z')


def message(command):
    if not isinstance(command, str) or not COMMAND.fullmatch(command):
        raise ValueError('command_denied')
    return ('assertion-memory-visual-v2 ' + command + '\n').encode('ascii')


def sign(command, key, *, directory=None):
    with tempfile.TemporaryDirectory(prefix='visual-sign-',dir=directory) as tmp:
        signing_key=Path(tmp)/'identity'
        signing_key.write_text(key.strip()+'\n',encoding='ascii')
        signing_key.chmod(0o600)
        source=Path(tmp)/'proof'
        source.write_bytes(message(command))
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(signing_key),'-n',NAMESPACE,str(source)],
                       check=True, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        proof=source.with_suffix('.sig').read_bytes()
    return f'{len(proof)}\n'.encode()+proof


def verify_frame(signers, command, stream):
    mode=COMMAND.fullmatch(command).group(1)
    length=stream.readline(7)
    if not re.fullmatch(rb'[1-9][0-9]{0,3}\n', length):
        raise ValueError('invalid_signature_framing')
    signature=stream.read(int(length))
    if len(signature)!=int(length):
        raise ValueError('invalid_signature_framing')
    with tempfile.TemporaryDirectory(prefix='visual-proof-') as tmp:
        saved=Path(tmp)/'proof'
        saved.write_bytes(signature)
        verdict=subprocess.run(['/usr/bin/ssh-keygen','-Y','verify','-f',str(signers),
                        '-I','visual-'+mode,'-n',NAMESPACE,'-s',str(saved)],input=message(command),
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
    if verdict.returncode:
        raise ValueError('invalid_release_signature')
ASSERTION_VISUAL_FILE_EOF_9_b33754
cat > "$target/payload/visual_policy.py" <<'ASSERTION_VISUAL_FILE_EOF_10_b33754'
"""Product-only payload validation for the common release receiver.

Never extracts, deploys, restarts, swaps pointers or reads secrets.  The common
transaction may call validate() on extracted staging or a previously retained
candidate before choosing its immutable release key.
"""
import hashlib
import json
from pathlib import Path
import re

MAX_ARCHIVE_BYTES = 5_000_000
MAX_MEMBER_BYTES = 2_400_000
MAX_MEMBERS = 5
ITEMS = [r'index\.html',r'assets/index-[A-Za-z0-9_-]+\.js',r'assets/index-[A-Za-z0-9_-]+\.css',
         r'assets/graph\.worker-[A-Za-z0-9_-]+\.js']
HEX = re.compile('[a-f0-9]{64}')


def validate(payload: Path, *, allow_prepared=False) -> str:
    """Return stable browser ID, or fail closed when any file/release is invalid."""
    root = Path(payload)
    marker = root/'manifest.json'
    if (root.is_symlink() or not root.is_dir() or (root/'assets').is_symlink()
            or not (root/'assets').is_dir() or marker.is_symlink()
            or not marker.is_file() or marker.stat().st_size > 1024):
        raise ValueError('invalid_visual_layout')
    raw = marker.read_bytes()
    doc = json.loads(raw)
    if (not isinstance(doc, dict) or len(doc) != 4
            or any(sum(bool(re.fullmatch(rule, key)) for key in doc) != 1 for rule in ITEMS)
            or any(not isinstance(value,str) or not HEX.fullmatch(value) for value in doc.values())
            or raw != (json.dumps(doc, sort_keys=True, separators=(',', ':'))+'\n').encode()):
        raise ValueError('invalid_visual_manifest')
    entries = set()
    for item in root.rglob('*'):
        name = item.relative_to(root).as_posix()
        if item.is_symlink() or (name != 'assets' and not item.is_file()):
            raise ValueError('invalid_visual_member')
        if name == '.prepared-v1' and allow_prepared:
            continue  # Trusted core owns marker validation; never a TAR member.
        if name != 'assets': entries.add(name)
    if entries != {*doc, 'manifest.json'}:
        raise ValueError('visual_manifest_membership')
    for name, digest in doc.items():
        p = root/name
        if p.stat().st_size > MAX_MEMBER_BYTES:
            raise ValueError('visual_member_size')
        with p.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest() != digest:
                raise ValueError('visual_manifest_digest')
    return hashlib.sha256(raw).hexdigest()


def accept_member(entry, name):
    """Additional product rule called by the common TAR reader, after path safety."""
    path = name.as_posix()
    if path == 'manifest.json':
        return entry.isfile() and 0 < entry.size <= 1024
    return entry.isfile() and 0 < entry.size <= MAX_MEMBER_BYTES and any(
        re.fullmatch(rule,path) for rule in ITEMS)


def retained(incoming, final):
    if validate(incoming) != validate(final, allow_prepared=True):
        raise ValueError('visual_retained_digest')


def make_hook(root):
    """Visual never restarts a service; test the in-scope atomic pointer after a swap."""
    root = Path(root).absolute()

    def hook(action, release):
        if action in ('prepare', 'restart'):
            return
        if action == 'stop':
            marker = root/'current'
            if marker.exists() or marker.is_symlink():
                raise ValueError('visual_pointer_still_present')
            return
        if action not in ('smoke', 'restore'):
            raise ValueError('visual_hook_action')
        release = Path(release)
        if release.absolute().parent != root/'releases':
            raise ValueError('visual_hook_scope')
        if release.name != validate(release,allow_prepared=True):
            raise ValueError('visual_release_identity')
        marker = root/'current'
        target = Path(marker.readlink())
        if target not in (release.absolute(), Path('releases')/release.name):
            raise ValueError('visual_wrong_active_release')

    return hook


def archive_spec():
    import release_core
    return release_core.ArchiveSpec(tar_mode='r|', max_archive_bytes=MAX_ARCHIVE_BYTES,
        max_expanded_bytes=MAX_ARCHIVE_BYTES, max_members=MAX_MEMBERS,
        accept_member=accept_member, allow_dirs=False)


def deploy_verified(stream, expected_sha, *, root):
    """Apply the product adapter after caller verifies its environment/signature authority."""
    import release_core
    return release_core.deploy(stream, expected_sha, root=Path(root), spec=archive_spec(),
        validate=validate, hook=make_hook(root), retained=retained)
ASSERTION_VISUAL_FILE_EOF_10_b33754
cat > "$target/payload/visual_receive.py" <<'ASSERTION_VISUAL_FILE_EOF_11_b33754'
"""Visual restricted entrypoint: check authority, call shared deploy, return receipt."""
import json
import os
from pathlib import Path
import pwd
import sys
if sys.flags.isolated:
    product=Path('/etc/assertion-memory-visual') / (sys.argv[1] if len(sys.argv)==2 and sys.argv[1] in ('test','prod') else 'invalid')
    shared=Path('/etc/assertion-release/shared')
    if Path(__file__) != product/'visual_receive.py':
        raise SystemExit('invalid fixed visual receiver path')
    for path in (product, shared):
        st=path.stat()
        if path.is_symlink() or not path.is_dir() or st.st_uid!=0 or st.st_mode&0o022:
            raise SystemExit('untrusted installed receiver directory')
    sys.path[:0]=[str(shared),str(product)]

from visual_auth import COMMAND, verify_frame
from visual_policy import deploy_verified

ROOT=Path('/var/lib/assertion-memory-visual')
CONFIG=Path('/etc/assertion-memory-visual')


def run(mode,command,stream):
    match=COMMAND.fullmatch(command)
    if not match or match[1]!=mode or mode not in ('test','prod'):
        return {'status':'rejected','error':'command_denied'},1
    if pwd.getpwuid(os.getuid()).pw_name!='assertion-visual-'+mode+'-deploy':
        return {'status':'rejected','error':'unexpected_receiver_identity'},1
    signers=CONFIG/mode/'allowed-signers'
    stat=signers.stat()
    if signers.is_symlink() or not signers.is_file() or stat.st_uid!=0 or stat.st_mode&0o022:
        return {'status':'rejected','error':'untrusted_signer_policy'},1
    verify_frame(signers,command,stream)
    digest=match[3]
    release=deploy_verified(stream,digest,root=ROOT/mode)
    return {'environment':mode,'revision':match[2],'archive_sha256':digest,'release':release},0


def main():
    try:
        mode,=sys.argv[1:]
        reply,status=run(mode,os.environ.get('SSH_ORIGINAL_COMMAND',''),sys.stdin.buffer)
    except Exception:
        reply,status={'status':'rejected','error':'visual_deployment_failed'},1
    print(json.dumps(reply,separators=(',',':')))
    return status


if __name__=='__main__':
    raise SystemExit(main())
ASSERTION_VISUAL_FILE_EOF_11_b33754
chmod 0700 -- "$target/install-visual-test.sh"
(cd "$target/payload"; sha256sum -c --strict -- "$target/PAYLOAD.sha256")
printf 'Materialized source-only visual TEST bootstrap; run --check first.\n'
