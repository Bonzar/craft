# Enrolled Mac capture V2: narrow owner update

Use this package only when the release owner has verified Base V2 in PROD and
provided the reviewed updater commit. The Base package is pinned to
65bc8e94a2b53a416707c8f16ddbe40de821c311, which contains the interruption fix
and the V2 re-arm client. This is not the usual whole-Jarvis installer:
it never rewrites Codex hooks, settings, Keychain, native binding,
transcript positions, or the outbox. It does not clear any database.

Use the normal official repository checkout, with complete (non-promisor)
local source objects for the reviewed updater commit and the pinned Base
source commit. Do not unpack an unverified archive into the live profile.
From that verified checkout, first run:

    python3 tools/assertion-memory-mac-v2 plan --checkout "$PWD"

The owner must then stop only the already verified local Assertion Memory
collector (its launcher ends in /assertion-memory/lib/capture_worker.py, with
the existing --binding and --state arguments). Do not stop every Codex/Python
process, sign out, edit a binding, or move the outbox. During the short window,
do not start or resume Codex sessions that would fire capture hooks. The
updater never sends a signal itself, and will refuse if the existing worker
lock is held or file pins have changed.

After the service owner independently verifies that the matching release
assertion-memory-v0.1.30 is active and supports V2, apply:

    python3 tools/assertion-memory-mac-v2 apply --checkout "$PWD" \
      --operator-quiesced --confirm-prod-base assertion-memory-v0.1.30

Only five already installed code files are replaced under the collector lock.
The previous five verified source files and a binding digest are preserved for
rollback; no memory data or credentials are copied. Plan mode is read-only;
run it again and expect preflight_ok_already_current. The normal hook will
start the new worker when the owner resumes the already enrolled session.
Use a new authorized harmless test turn and confirm one delivery batch only;
do not re-open or replay historical transcripts as proof.

If the owner has independently established that **no database clear has
occurred since installation**, and the collector/hooks are again quiesced,
the pinned rollback is:

    python3 tools/assertion-memory-mac-v2 rollback --checkout "$PWD" \
      --operator-quiesced --confirm-no-clear

Do not roll back this Mac client after database clear: the older watcher cannot
acknowledge the new generation. If the plan or a pin fails, stop; there is
no fallback that resets a binding, cuts over an outbox or widens scope.
