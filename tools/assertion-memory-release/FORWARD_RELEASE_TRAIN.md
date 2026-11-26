# Forward release train

This is a deployment gate only. Apply independently to Base and Visual. Keep
existing 0.1.x tags immutable and recognize the exact running artifact as
baseline after supervised initialization. The first planned forward train is
1.0.0. Publish and build later releases freely. The next major cannot activate
in TEST until the preceding train is verified running in PROD.

Next minor requires a reviewed repair of a recorded failing TEST check on
the exact current train artifact. Review binds the exact old artifact, new
head, failing evidence and scope manifest; unrelated changes are disallowed.
A passing TEST candidate is not superseded by unrelated feature work and
proceeds to the existing protected PROD approval.

Legacy versions, emergency continuity and historical rollback use a distinct
supervised path, not the forward train. If a rollback changes live PROD, new
forward activation remains blocked until the genuine baseline is reverified.
