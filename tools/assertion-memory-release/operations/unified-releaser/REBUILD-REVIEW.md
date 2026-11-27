# No mandatory releaser archive attachment
From protected #399 source 8bf3d22c1b4b051df1312515bd66c1a16a0ae2e7.
A GitHub release may have no assets. Run from authorized protected main,
pin immutable release ID/tag, exact peeled commit (before/after), and
reviewed deterministic package digest. Verify the tag commit is on the
protected history before building. Build ephemeral in runner tmp with no
GitHub token in build process. Do not publish/upload any result or store
it on the server. An immutable tag alone is not installation authority.

Two statically lane-labelled verification jobs share the same build bytes and one program;
selection only chooses a closed lane/operation. Check exact workflow
boundary, immutable source/release, exact rebuild digest, and independent
lane-bound executor receipt. The hosted workflow Verify Releaser From Tag (No Deploy) only qualifies
bytes; selecting a lane does not select a GitHub deployment Environment,
load secrets, or request host privileges. The lane environment value in the
fixture is a contract label, not admission. No apply transport is provided.
