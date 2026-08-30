# Craft agent system

This repository supports both Claude Code and Codex.

- Shared policy and skills live only in `core/`. `.codex`, `.claude`, and
  `.agents` are harness adapters and discovery surfaces; core must not import them.
- Invoke registered child roles through `core/agents/run.mjs` using their
  canonical `agentId`. Native custom-agent files are forwarding adapters; do
  not bypass the core registry, depth/concurrency limits, permission narrowing,
  context contract, or unified result schema.
- The live Craft router and the code-rules dispatcher are injected by the
  repository `.codex/hooks.json` `SessionStart` adapter. Treat that injected
  developer context as authoritative.
- Every action that is not proven read-only must use a directly exposed native
  tool call. Do not route such actions through a programmatic tool container:
  the plan-gate must receive the concrete call before it can execute.
- If hooks are disabled, untrusted, or unavailable, read
  `.claude/craft-router-context.md` completely before working with the Craft
  knowledge base. Before editing code, also load the current code rules from
  Craft as directed by that router.
- Generated Craft snapshots are runtime state. Do not commit them.
- Run `node --test tests/unit/` and `bash tests/install-smoke.sh` after changing
  hook or installer behavior.
