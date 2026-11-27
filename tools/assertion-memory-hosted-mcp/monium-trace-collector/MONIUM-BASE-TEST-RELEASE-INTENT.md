# Monium Base TEST release intent

The narrow candidate `1c022bfdd3d728d94cb5ea77a68970ca4d7348e2` is exactly the active Base v0.1.37 source `6c2c0a8ee33eb71f8784416adb2170b099abe922` plus the eleven reviewed Monium telemetry files from #368 (using its previously reviewed pre-device relay blob). This ancestry carrier is for merge only: never tag or build it as the release candidate.

Once the narrow commit is an ancestor of protected main and an authorized publisher and TEST-only activation are verified, standard immutable release tooling may build it. Do not approve PROD or issue a duplicate TEST dispatch. The separately operated TEST collector selector must qualify under its existing operator authority; no runtime config, access or deploy action is enabled by this note.
