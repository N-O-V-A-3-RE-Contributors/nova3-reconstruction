# Task output scopes and recovery

The worker runs `git status --porcelain=v1 -z --untracked-files=all` to inspect every created/modified file individually. Parent folders containing new files are not treated as changed files themselves. Deletions, conflicts and symlinks fail closed.

Ledger-generated tasks permit the named block source/test file and one per-cluster helper directory each. Anything outside their `Allowed paths:` remains blocked. Evidence is restricted to the task's named JSON file. Native original binaries and game assets remain forbidden by the original guard.

The patch fixes **future tasks**. GitHub issues already published (#29–#36) keep their original scopes, so a pre-existing checkout is only recoverable automatically if its actual files match those scopes. When output differs from permitted filenames, do not blindly add unrestricted directory prefixes or disable validation; inspect the exact changed files and issue body first.

Inspect the saved work without modifying it:

```bash
ROOT="$HOME/.cache/nova3-team/N-O-V-A-3-RE-Contributors_nova3-reconstruction"
for n in 29 30 31; do
  echo "========== TASK $n =========="
  find "$ROOT" -maxdepth 1 -type d -name "$n-*" -print | while read -r work; do
    git -C "$work" status --short --untracked-files=all
  done
done
```

If the only difference is a generated helper file with a task-specific filename, add **that exact file** to the GitHub task's `Allowed paths` entry after verifying content and provenance, then requeue the blocked issue. Do not erase the saved checkout, blindly retry Codex, or mark a reconstruction as matched without actual binary/behavioural evidence.

Contributors must update their workers; an existing process will not gain the fix without restart. Stop systemd before patching and restart only after the check suite passes.
