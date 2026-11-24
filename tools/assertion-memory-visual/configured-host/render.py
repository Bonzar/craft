"""Render immutable per-stage recipes; never modifies system config or keys."""
import argparse
from pathlib import Path

POLICY = '/etc/assertion-memory-visual/configured'


def recipe(stage):
    if stage not in ('test', 'prod'):
        raise ValueError('Unknown configured host stage')
    account = f'assertion-cfg-vis-{stage}-deploy'
    writer = f'assertion-visual-{stage}-deploy'
    alias = f'ASSERTION_CFG_VIS_{stage.upper()}_RECV'
    command = f'/usr/bin/python3 -I -B {POLICY}/receive.py {stage}'
    forced = f'/usr/bin/sudo -n -u {writer} -- {command}'
    sshd = f'''Match User {account}
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysCommand none
    AuthorizedKeysFile /etc/ssh/assertion-visual/configured-{stage}.authorized_keys
    ForceCommand {forced}
    DisableForwarding yes
    PermitTTY no
    X11Forwarding no
    PermitUserRC no
Match all
'''
    sudoers = f'''Cmnd_Alias {alias} = {command}
Defaults:{account} env_reset, !setenv, env_keep += "SSH_ORIGINAL_COMMAND"
Defaults!{alias} !use_pty, !log_input, !log_output
{account} ALL=({writer}) NOPASSWD: NOSETENV: {alias}
'''
    return sshd, sudoers


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('test','prod'))
    p.add_argument('output', type=Path)
    args = p.parse_args()
    sshd, sudoers = recipe(args.stage)
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, content in [('sshd.fragment',sshd),('sudoers.fragment',sudoers)]:
        (args.output/name).write_text(content,encoding='ascii')


if __name__ == '__main__':
    main()
