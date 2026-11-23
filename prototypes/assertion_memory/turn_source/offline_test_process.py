"""Linux-only no-network protocol TEST subprocess; never a live launcher."""
from pathlib import Path
import subprocess

from .reader import SourceReadDenied


class OfflineTestProcess:
    """Run one trusted Python TEST fixture with only the per-attempt MCP broker."""

    def spawn(self, command, *, cwd, env, broker_root, relay):
        unshare, bubblewrap, python=(Path(p) for p in ('/usr/bin/unshare','/usr/bin/bwrap','/usr/bin/python3'))
        fixture=Path(command[1]).resolve(strict=True) if len(command)>1 else None
        if (not all(p.is_file() for p in (unshare,bubblewrap,python)) or fixture is None
                or not fixture.is_file() or fixture.suffix!='.py'):
            raise SourceReadDenied('Offline TEST process requires Linux isolation and a trusted Python fixture')
        root=str(Path(broker_root).resolve(strict=True))
        sandbox=[
            str(unshare),'--user','--map-root-user','--net','--',str(bubblewrap),
            '--unshare-user','--unshare-pid','--unshare-ipc','--unshare-uts',
            '--disable-userns','--assert-userns-disabled','--die-with-parent','--cap-drop','ALL',
            '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin',
            '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
            '--proc','/proc','--dev','/dev','--tmpfs','/tmp',
            '--bind',root,root,
            '--ro-bind',relay,relay,
            '--ro-bind',str(Path(relay).with_name('transport.py')),str(Path(relay).with_name('transport.py')),
            '--ro-bind',str(fixture),'/fixture/app.py',
            '--chdir',cwd,'--clearenv',
        ]
        for key,value in env.items():
            sandbox.extend(('--setenv',key,value))
        sandbox.extend(('--',str(python),'/fixture/app.py',*command[2:]))
        # Outer unshare never requests a shell or falls back to an unjailed PID.
        return subprocess.Popen(sandbox,cwd=cwd,env={'PATH':'/usr/bin:/bin'},start_new_session=True,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
