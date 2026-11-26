"""Exact sidecar check bound to native protected hook's fixed PID query."""
from pathlib import Path
from kernel_observer import PROC, BASE, GRAPH, _process, _read, _tcp, start_ticks
from settled_guard_binding import verified_settled_identity, restart_selected

def sidecar_enabled():
    identity=verified_settled_identity()
    return identity is not None and identity.mode=='sidecar'

def sidecar_check_live(policy, release, uid, *, trusted_pid, not_listening):
    """Called after inherited current, root file and system socket checks.

    `trusted_pid` uses only the existing scoped user-unit systemctl query.
    `not_listening` is exactly the existing native bounded retry exception.
    No application requests, new sessions, or service control here.
    """
    identity=verified_settled_identity()
    if (identity is None or identity.mode!='sidecar' or uid!=986
            or policy.mcp_port!=8903 or policy.graph_port!=8901
            or type(release) is not type(Path('/')) or release.name!=identity.revision+'-'+identity.archive
            or release.parent!=Path('/srv/assertion-release/production/releases')):
        raise ValueError('wrong protected Base sidecar identity')
    if trusted_pid(GRAPH)!=0:
        raise ValueError('legacy Graph still running in scoped sidecar mode')
    pid=trusted_pid(BASE)
    if pid==0:
        raise not_listening('Base unit has no MainPID yet')
    process, owned=_process(PROC,pid=pid,unit=BASE,release=identity,physical=release)
    sockets=_tcp(_read(PROC/'net/tcp',1024*1024))
    if _tcp(_read(PROC/'net/tcp6',1024*1024)) or 8901 in sockets:
        raise ValueError('foreign listener in protected Base scope')
    if not {8902,8903}<=set(sockets):
        raise not_listening('Base sidecar not listening yet')
    if (sockets[8902]==sockets[8903] or set(sockets.values())-owned
            or process.start_ticks!=start_ticks(_read(PROC/str(pid)/'stat',8192),pid)
            or trusted_pid(BASE)!=pid or trusted_pid(GRAPH)!=0
            or verified_settled_identity()!=identity):
        raise ValueError('Base sidecar listener identity changed')
    # Preserve native before/after comparable return; sidecar is one PID.
    return (pid,)
