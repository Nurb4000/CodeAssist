# Project steering

## Test execution

- Run the project test suite only through `test_runner` with its default target. The configured remote test VM is the default; local container execution is a fallback used only when that VM is unreachable.
- Never run the test suite with `pytest`, `npm test`, or similar via `shell`. Do not pass `target: "local"` unless the user explicitly asks for a local comparison.
- Report the target in every result. A failing remote suite is a real failure — never rerun locally to get a pass.
- If a run fell back locally, say why (unconfigured host, SSH/auth failure, rsync failure, timeout).

## Storage paths

- Do not use `/tmp` for persistent storage, synced work, build/test artifacts, logs, or anything sizeable. On current Linux it is commonly a small tmpfs and is wiped between boots.
- Work inside the workspace. On the dedicated test VM, use its regular filesystem: `$HOME/codeassist-tests` (the configured `remote_test_vm.remote_dir`, default `~/codeassist-tests`) or another explicit path under `$HOME` / `/var/lib`. The VM is dedicated, so persistent directories are fine.
- `/tmp` is acceptable only for tiny throwaway scratch that you delete in the same session.
