# Base native TEST journal and PROD gate (not installed)

The configured TEST gate verifies an existing signed Intent before handing the
same typed intent to ConfiguredBaseNativeBridge. The configured PROD gate
verifies its existing scoped authorizer response before passing the protected
Promotion. A future major fails closed when this bridge is absent; 0.x follows
its existing supervised path. The shared native Base helper passes through the
existing ActivationWitness and optional guarded native switch without changing
the implementation of the switch, smoke or rollback itself.

After the exact Base TEST switch and journal fsync, BaseNativeBinding records
the independently observed native identity and unique attempt, with the exact
authenticated TEST run ID, in the existing authority-provided ledger. PROD
matches the existing authenticated selected TEST receipt's independently
verified original run ID to that record, and reads actual TEST inside the same
family lock before allowing a native effect. The receipts' archive digest and
run fields are not inferred from tag, workflow GREEN, environment variables or
caller strings.

**One omitted install hook:** the existing protected configured receiver
installer must provide the root-owned ConfiguredBaseNativeBridge binding
and the existing approved receipts resolver across the exact TEST/PROD roles.
It has to supply the same root-owned family ledger/lock and prove the signed
PROD intent's selected TEST receipt artifact ID maps to its provider run. The
current installer scopes separate UID/nonce stores and has no such shared
trusted call. No loader, wider peer access, new socket, token, or environment
flag is added. Existing provided hooks remain review-only via
362-base-protected-native-call-sites.patch.
