# NOVA3 live dashboard (planned)

The worker loop writes durable, machine-readable status files on every pass.
This directory is reserved for a small, dependency-free reader that renders
them; the data contract is fixed here so the writer and the reader can be
built independently.

## Data contract (already written by `tools/dsh_loop`)

All paths are relative to the checkout. Everything under `.local/` is private
and git-ignored, so the dashboard must never be committed with live data.

| Path | Content | Written when |
| --- | --- | --- |
| `.local/dsh-loop/loop-state.json` | `version`, `passes`, `consecutive_failures`, `consecutive_no_progress`, `stopped_reason`, `last_session`, `last_commit`, `seeded` | after every pass and on stop |
| `.local/dsh-loop/passes.jsonl` | one JSON object per pass: `pass`, `at`, `ok`, `progress`, `summary`, `session`, `tokens_in/out`, `elapsed_s`, `head_before/after`, `dirty_before/after` | appended per pass |
| `.local/dsh-loop/live/current.json` | `pass`, `started` (epoch), `phase` | created at pass start, deleted at end |
| `.local/dsh-loop/BACKLOG.md` | the loop's own task queue (`N. [ ] task`) | maintained by the model |
| `.local/dsh-loop/prompt-overrides.md` | human guidance injected into every pass | maintained by the human |
| `.local/dsh-loop/logs/passes-YYYYMMDD.log` | per-pass result summary, stderr tail and final text (key-shaped strings masked) | appended per pass |
| `~/.config/nova3/dsh-loop.json` (outside the checkout) | installation state: provider, model, credential *variable name*, runtime paths | by the installer |

The public, already-published `tools/observatory/` dashboard remains the
supported view for repository-level status; this directory is for the
contributor-local worker view and must degrade gracefully when the files are
missing or malformed.

## Non-goals

* No network listener bound to anything but `127.0.0.1`.
* No reading of API keys or shell profiles; only `api_key_env` names.
* No writes: the dashboard is a reader.
