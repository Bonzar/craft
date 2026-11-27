# Source-evidence comparison
The reference is the immutable public Git commit in the runner, not a claim that a release archive was fetched or installed. The runner reads only the selected local tracked Python closure and checks its aggregate digest. It does not fetch, publish, or deploy anything. Place the pinned commit in the local repository through your ordinary authorized Git workflow before running.

Offline:
```sh
python3 -I -B tools/assertion-memory-qa/source-evidence-eval.py --git-root .
```

A separate authorized native Codex environment can evaluate one explicitly selected fictional case:
```sh
python3 -I -B tools/assertion-memory-qa/source-evidence-eval.py --git-root . \
  --run-one --variant stock --case scoped_user_adoption
```
Use `standalone_C` for the independent grounding/disambiguation prompt overlay. Do not copy authentication or set up a provider as part of this QA command. Model runs are not a CI or deployment step. The child uses the app's SOL medium/low profile and disposable SQLite; reporting is limited to synthetic case IDs, verdicts, source-role/count diagnostics and digests.

Run stock and candidate on the same case. A deployment decision needs correct positive retention, zero false saves, and complete provider trace including the wrong-source and typed-tool controls; both failing is not an acceptable result. Offline preflight is not a semantic pass.
