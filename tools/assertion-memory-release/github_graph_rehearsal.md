# Native TEST approval rehearsal

This is a no-secret pilot for the GitHub Actions **Review deployments** control.
It simulates activation in the same run after validating the original archive.
There is no SSH, VM action, deployment receipt, rewrite of a tag, or PROD access.

Before explicitly running the rehearsal, a repository administrator should create
`assertion-memory-native-test-rehearsal` under **Settings → Environments**,
restrict deployment branches to selected branch `main` (no tag policy), add
the repository owner as a required reviewer, and leave secret storage empty.
For a solo operator, leave **Prevent self-review** off so Bonzar can both start
and approve this rehearsal. Disable admin bypass if available. Inspect the saved
rules, then add environment variable `ASSERTION_MEMORY_NATIVE_REHEARSAL_READY=true`.
A missing environment can be auto-created without reviewer rules; the variable
blocks a misleading green result in that case. The variable is not itself a
reviewer proof, so check the reviewer rule and observe the waiting transition.

Open **Actions → Assertion Memory · Release graph → Run workflow**, choose
`main`, enter existing immutable base tag `assertion-memory-v0.1.0`, and
enable **Wait for a TEST approval, then simulate activation (no deploy)**.
This creates no new tag. Verify the original-build and archive jobs succeed;
the rehearsal job must visibly wait for **Review deployments**. Rejecting
must not show a successful simulation. Approving must produce only a short
summary for the original verified archive. Ordinary previews and automatic
release events never request this dedicated rehearsal.

The current PROD proof, transport, and environments are not changed.
