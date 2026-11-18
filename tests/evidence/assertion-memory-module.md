# Optional memory module verification — 2026-11-16

Isolated tracked-source snapshot, synthetic profiles, Python 3.12. No global
configuration, personal database, token value, transcript body, network endpoint
or model was used.

TDD red: test_assertion_memory_module initially failed on absent MCP registration,
missing startup policy/identity receipt, missing ownership collision guards and
missing explicit Claude MCP configuration support.

Green: 194 tests passed (20.403s): test_assertion_memory_module (13 cases),
test_session_bootstrap, test_codex_wrapper, test_start_context, test_installer,
test_installer_harness, test_installer_mentions, test_installer_skills.

The fake Codex in baseline harness tests uses /usr/bin/env python3. Its PATH must
select Python >=3.11 for tomllib; machine system python3 is 3.9. Running with the
Homebrew Python 3.12 libexec/bin first resolves this fixture requirement.

Boundary: installation schema + synthetic hook execution are verified, not live
remote connectivity, global coverage, tool readiness or automated capture. Startup
receipts explicitly record capture_status=not_configured. No live claim made.
