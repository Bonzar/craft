# Opt-in real TEST database identity

This branch is not a deployment. Current TEST fixture mode and its Site are unchanged.
An operator may stage a separate protected profile only after approving the real TEST
canonical storage and current release on the isolated TEST VM.

- Keep the historical fixed TEST database path; name does not imply fixture contents.
- Read only file metadata for the existing DB, not any database rows. The operator
  pins its actual device and inode after the approved reset, while not accepting
  requests through the new relay. Keep the existing approved release, scoped TEST
  writer UID, cgroups and paths. Owner-only control without cross-lane redirects.
- Set `test_database_mode` to `real` in the private relay read profile and load a
  root-supplied systemd credential `test_real_db_identity`: exact JSON keys
  `device`, `inode`, `graph_fixture_flag`. The graph flag is a readback of the
  actual reviewed argv, not a seed flag read from SQLite. Only root-selected
  options. Preserve the underlying unit's filesystem/API restrictions.
- Real mode rejects symlink paths, other owner, writable group/other permissions,
  hardlinks, inode replacement, config/argv/listener/cgroup mismatch. It checks
  exact same DB identity on both ends of the readiness measurement and relies on
  the existing read-only filesystem view in the relay sandbox.
- Test no-data status against an actual empty TEST schema. Do not seed, invoke
  candidate fixture checks, enable effectful tools, or publish private records.
  Keep existing tool annotations and the old owner binding until explicit migration.
