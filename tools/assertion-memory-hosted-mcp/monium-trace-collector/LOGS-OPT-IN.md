# Optional query timing Logs pipeline

The installed trace unit does not load these files by default. Base startup
does not enable the application mirror by default either. This source does not
authorize activation or add journal access, a new service, or a credential grant.

Load the existing trace common + same-lane trace config unchanged, then
`logs-common.opt-in.json` and exactly one same-lane `logs-{lane}.opt-in.json`.
The missing `filter/logs_lane` component intentionally prevents partial startup.
The existing trusted collector operator must qualify that lane's preexisting
Logs writer and destination. `MONIUM_LOGS_PROJECT` and `MONIUM_LOGS_API_KEY`
are private collector destination settings, never application feature flags.
Keep the trace receiver, trace pipeline, trace destination and permissions intact.
The initial Logs projection is the six verified core fields and eight qualified
stages, not raw journals, general application logs or all trace spans.
