#!/usr/bin/python3 -I
"""Only sshd's exact authorized PROD receiver command, never an ordinary login shell."""
import os
import pwd
import sys

PYTHON = '/usr/bin/python3'
RECEIVER = '/etc/assertion-release/production/receive-authorized-entry.py'
HOME = '/srv/assertion-release/production'
USER = 'assertion-production-deploy'
ARGS = (PYTHON, '-I', '-B', RECEIVER, 'production')
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
