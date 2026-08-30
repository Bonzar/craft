# Core capability rule

`core` owns the complete product contract and is the union of capabilities
implemented by all adapters. It is intentionally allowed to be wider than any
single adapter.

- A feature enters the core contract as soon as one adapter implements it.
- Other adapters may declare the feature unsupported until they catch up.
- Missing adapter support never removes, narrows, or hides the core feature.
- Callers receive an explicit structured `unsupported` result; adapters must
  not imitate support or silently degrade behavior.
- Business logic branches on declared capabilities, never on an adapter name.
- Adding support later changes only the adapter declaration and transport; the
  core contract and business graph stay unchanged.

## Planning transition

The policy layer returns `plan_required`; it never pretends that a text prompt
changed the active execution mode. The current action remains denied. An
adapter with `native-plan-transition` ends that turn and asks its harness to
start the next turn with the same intent in the harness's native planning
capability. Missing support is an explicit `unsupported` result and remains
fail-closed.

Capability and connection are separate facts. An adapter may implement the
native operation builder while a particular hook-only installation has no
app-server transport capable of ending and restarting turns. In that case the
coordinator returns `unsupported`, the original action stays denied, and no
text prompt is treated as a mode switch.
