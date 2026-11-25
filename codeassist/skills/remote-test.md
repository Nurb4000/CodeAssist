---
name: remote-test
description: Run or troubleshoot test suites on the configured dedicated remote VM (the default when a host is set, local only as a fallback)
slash: remote-test
---

# Remote Test VM Skill

Use this skill when test results need to come from a full VM rather than the
CodeAssist container, or when the remote test VM is misbehaving. Execution itself
is always done by `test_runner`; this skill is guidance only.

## Default behavior

- `test_runner` runs on the configured `[remote_test_vm]` target when one is set.
  It syncs the workspace with rsync (unless `sync = "none"`), runs the suite over
  SSH, and reports `Target: remote VM (user@host)`.
- If the VM cannot be reached, it falls back to `Target: local container` and
  includes a `Fallback reason`. A failing remote suite is **not** a fallback
  condition — it is a real test failure.
- `<env>` in the system prompt shows `Remote Test VM: ssh(user@host)` or `off`.
- The synced workspace lives in `[remote_test_vm].remote_dir` (default
  `~/codeassist-tests` on the VM), never `/tmp` — that is a small, wiped tmpfs.

## Steps

1. Run `test_runner` normally (no `target` argument) for the default path.
2. Report which target produced the result. If it fell back locally, say why.
3. To compare environments, run twice:
   - `test_runner` with `target: "remote"` — forces the VM; fails if unreachable.
   - `test_runner` with `target: "local"` — forces the container.
4. If the remote result differs, check the VM's dependencies and
   `[remote_test_vm].remote_env` (e.g. activate the right virtualenv) before
   assuming the code is at fault.

## Troubleshooting

- **SSH preflight failed / auth failure** — verify `host`, `user`, `port`, and
  that the private key at `key_path` is mounted and chmod 600. Password auth
  uses `sshpass -e`; prefer key auth.
- **Host key verification failed** — populate `known_hosts` with the VM's key.
  Only set `strict_host_key_checking = false` for disposable lab VMs.
- **rsync failed** — confirm `remote_dir` is writable and `rsync` and `ssh` are
  installed in the image. Check `rsync_excludes` if files are missing remotely.
- **Tests fail only on the VM** — inspect `remote_env`, the VM's Python/toolchain
  versions, and any services the suite expects. Do not silently switch to local
  results to make a failing suite pass.

Do not ask the user for the VM IP during a session; it is administrator config
(`[remote_test_vm]` in TOML, Settings > Remote Test VM, or
`CODEASSIST_REMOTE_TEST_VM_*` at launch).
