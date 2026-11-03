# Native DSH runs, and a runner that works for more than one project

Status: **partially implemented** (2026-11-03). The reusable runner exists and is
tested; NOVA3 still executes through the headless backend. This document records
what was verified, what was built, what blocks the cutover, and what "port it to
other projects" therefore means concretely.

## What was verified about the DSH Host (0.2.1-rc.5)

| seam | evidence |
| --- | --- |
| Native Agents tab = `@linxin666/dsh-agents` | tab data comes from `$DSH_HOME/dsh-agents/ledger.json` (`teams`, `runs`), which was `{"teams": [], "runs": []}` before this work |
| Squads are definitions, runs are sessions | `POST /dsh-agents/api/run` launches a real Host session and records a run; `POST /api/team` and `/api/member` only edit definitions |
| Routes are guarded, not open | a plain GET returned `403 ... own browser only`; the same GET with `Origin: http://127.0.0.1:3080` returned 200 |
| State exposes the Host catalog | `/api/state` returns `workspaces`, `presets`, `models` (8 routes incl. `gpt-6-astra`), `efforts`, `teams`, `runs` |
| Sessions are inspectable offline | each Host session is `$DSH_HOME/sessions/<cwd-slug>/<session-id>/session.v4.jsonl.zstd`; the terminal `turn_end.reason.kind` is the objective outcome |
| Host session creation is workspace-scoped | the squad gateway calls `session.create({workspaceId, agentPreset})`; there is no `cwd` argument |
| Workspaces can be created in-process | `@deepseek-ai/dsh-api-workspace-controller` exposes `workspace.create` on the typert gateway (in-process), not on a documented external HTTP route |

## The consequence that decides the port

A native (Host) run is pinned to a **registered workspace**, and the only
external way to start one is a **squad run**. NOVA3's verification and
publication pipeline is built on **one git worktree per task**:

* the scope guard and `verify()` run inside that checkout,
* a failed task keeps its checkout for inspection,
* two tasks never write the same tree.

So a naive cutover would either serialise all tasks into the main working tree
(losing checkout isolation and the ability to keep failed work) or misattribute
verification. That is a correctness change, not a visibility feature, and it is
the reason the cutover is staged.

## What was built (in this commit)

`dsh_runner/` — project-agnostic mechanics, no NOVA3 dependency:

* `profile.py`: the only project-specific input. Schema `dsh-runner-project/1`
  validates repo, workspace, backend, isolation, timeout, step bound, members
  (preset/permission/model/effort/brief), routing policy, safe/denied paths and
  the project's own verify commands. A second project is a second YAML file.
* `host.py`: guarded Host client (`state`, `ensure_team`, `ensure_member`,
  `run_member`, `report`). `report()` decompresses a session transcript and
  returns `running|completed|error|aborted|missing` with step/tool counters —
  never model prose.
* `backends.py`: one `RunResult`, two implementations. `HeadlessBackend` keeps
  today's isolated checkout behaviour. `HostBackend` launches a native session
  and therefore *requires* `execution.isolation: shared-workspace`, refusing to
  pretend otherwise.
* `cli.py`: `profiles`, `register` (idempotent native squad registration),
  `status`.
* `config/projects/nova3.yaml`: the first profile, mapping current NOVA3 policy.
* Tests: profile validation and unsafe-value refusal, backend selection and the
  isolation refusal, headless status mapping, transcript observation
  (completed/error/running/missing), profile discovery. 9 pass.

## The next step, and why it is generic

To run native sessions with per-task isolation, the runner needs a small DSH
plugin (host half) that exposes two typert calls the external runner cannot
reach today:

```
POST /dsh-runner/api/session  {workspacePath, permission, provider, model,
                               reasoningEffort, title, prompt}
    -> creates or reuses a workspace for that path, creates the session in it,
       applies permission/model, prompts, optionally records a squad run
GET  /dsh-runner/api/session/{id} -> the report() projection
```

With that, `execution.isolation: per-task-checkout` + `backend: host` becomes
valid, and every project (not just NOVA3) can have native, visible sessions with
its own checkout per task. Until then, `host` + `shared-workspace` is available
for projects that genuinely want one tree.

## Multi-project contract

Invariants the runner keeps for every project:

* a profile is data; no project logic enters `dsh_runner`;
* unsafe paths, non-identifier model routes and unknown backends are refused
  before a session starts;
* a provider pause (`WAITING_CREDITS`, `AUTH`, quota text) is `unavailable`, not
  a task failure; a task error is never retried on another paid route;
* outcomes come from `turn_end` events and counters, never prose;
* nothing in the runner writes a project's git, queue, ledger or secrets;
* native squad registration is idempotent and uses the Host's own guarded API.

Open questions recorded rather than guessed: whether a Host session can be
created with an explicit working directory in a later DSH release; whether the
agents ledger accepts externally recorded runs (it owns locking/revision, so the
runner will not write it); and how many concurrent Host sessions the Web Host
will sustain on this 4-core machine.
