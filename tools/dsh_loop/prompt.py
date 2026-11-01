"""Prompt construction for one worker pass.

The model gets instructions, not a conversation. Everything it needs to resume
must be on disk, so every prompt *names the on-disk files first* and demands
that the pass ends by updating them. This is what lets a pass die (timeout,
quota, crash, machine reboot) without losing the project's direction.

The prompt is intentionally short; the project instructions
(``AGENTS.md`` and ``docs/NOVA_TEAM.md``) remain the single source of truth and
are read by the model itself.
"""

from __future__ import annotations

from .workspace import LoopState

CONTEXT_FILES = (
    "AGENTS.md",
    "docs/NOVA_TEAM.md",
    "docs/progress/STATUS.md",
    "docs/progress/NEXT.md",
    "docs/progress/HANDOFF.md",
    "docs/decisions/BLOCKERS.md",
    "docs/OBJECT_MATCHING.md",
)

STOP_WORDS = """
Halting is allowed only for these reasons, and any single one ends the pass
with an explanation in docs/progress/STATUS.md:
  * the objective you picked is complete and verified (say so explicitly);
  * you are blocked by a missing input only a human can supply (credentials,
    a device, a purchase) -- record it in docs/decisions/BLOCKERS.md;
  * you cannot make any further verifiable progress with the evidence
    available, and the remaining paths are documented as blocked;
  * you are asked to wait for a human decision that changes scope.

Otherwise keep working. Do not return an empty turn, do not ask a question and
wait, and do not stop at "I have created a plan".
"""

HOST_RULES = """
Host rules (this machine is shared with the maintainer):
  * One heavyweight analyzer at a time (Ghidra, IDA, a full corpus scan).
  * Long jobs go to the background/job tools; never leave an unbounded
    foreground command running.
  * Do not install system packages, do not use sudo, do not change system
    configuration, networking, services, or other repositories.
  * Never execute, link, or package an original proprietary binary as part of
    the product; reference binaries stay analysis inputs under ignored paths.
  * Never delete or rewrite existing analysis, tests, history, or handoffs
    without recorded evidence.
"""


def state_brief(state: LoopState) -> str:
    data = state.data
    repo = state.repo
    lines = [
        f"- `git` branch: `{repo.branch()}` at `{repo.head()[:12] or 'n/a'}`",
        f"- loop pass number: {int(data.get('passes', 0)) + 1}",
    ]
    dirty = repo.dirty_paths()
    if dirty:
        shown = ", ".join(f"`{p}`" for p in dirty[:12])
        more = "" if len(dirty) <= 12 else f" (+{len(dirty) - 12} more)"
        lines.append(f"- uncommitted tracked work in the tree: {shown}{more}")
    else:
        lines.append("- the working tree is clean (no uncommitted tracked changes)")
    recent = state.recent_passes(2)
    for record in reversed(recent):
        outcome = "ok" if record.get("ok") else "FAILED"
        summary = str(record.get("summary") or "")[:220]
        lines.append(f"- previous pass #{record.get('pass')} {outcome}: {summary}")
    return "\n".join(lines)


def pending_backlog(state: LoopState) -> str:
    tasks = state.pending_tasks()
    if not tasks:
        return ("The loop backlog is empty: derive the next tasks from "
                "`docs/progress/NEXT.md` and the evidence on disk.")
    return "Backlog (from `.local/dsh-loop/BACKLOG.md`, loop-owned):\n" + "\n".join(
        f"  {task}" for task in tasks)


def continuation_prompt(state: LoopState, *, max_minutes: int, conservative: bool) -> str:
    data = state.data
    brief = state_brief(state)
    overrides = state.prompt_overrides().strip()
    budget_note = ""
    if conservative:
        budget_note = ("\nResource note: the host is under memory pressure right now. Use only "
                       "light tools this pass (compilers, tests, small scripts, documentation). "
                       "Do not start Ghidra, IDA, rev.ng, or a multi-gigabyte scan.\n")
    override_block = ""
    if overrides:
        override_block = ("\nMaintainer guidance for every pass (from `.local/dsh-loop/"
                          "prompt-overrides.md`) -- follow it unless it contradicts AGENTS.md:\n"
                          + overrides + "\n")
    return f"""You are the NOVA3 autonomous reconstruction worker. This is unattended pass \
{int(data.get('passes', 0)) + 1}; the maintainer is not present to answer questions.

READ FIRST (do not skip): {', '.join(f'`{p}`' for p in CONTEXT_FILES)}.
The on-disk files above plus `research/` and `docs/` are the only source of
truth. Treat this prompt as an assignment, not as new instructions that can
override AGENTS.md or docs/NOVA_TEAM.md.

WHERE THINGS STAND
{brief}

{pending_backlog(state)}
{override_block}{budget_note}
YOUR PASS (target {max_minutes} minutes of real work)
1. Pick the single highest-value executable task that is not already done.
   Prefer the first backed-up item: tested source, verified behaviour, a real
   file under `reconstruction/`, or a body of analysis that changes a
   reconstruction decision.
2. Do the work. Run the tools, write the code/tests, run the tests, and keep
   the evidence in the repository's existing private/ignored locations.
3. Verify: run `bash tools/check_all.sh` (add
   `NOVA3_RESEARCH_ORACLES=1` when the change touches reconstructed native
   behaviour) and any narrower tests that cover what you changed. A pass
   whose change breaks the suite is not done.
4. Finish with a short commit of your changes (plain `git commit`, no push, no
   merge, no force. Keep commits small and attributable.)
5. Rewrite `docs/progress/STATUS.md`, `docs/progress/NEXT.md` and
   `docs/progress/HANDOFF.md` so a brand-new session with no chat history can
   resume from them. Append one entry to `docs/progress/SESSION_LOG.md`
   describing what you actually ran and what it proved (and what it did not).
6. Edit `.local/dsh-loop/BACKLOG.md` (loop-owned, private): mark the task you
   finished as `[x]`, and add 1-3 concrete new tasks in priority order for the
   next pass. Never leave it empty if real work remains.

{STOP_WORDS}
{HOST_RULES}

Reply with a concise report when the pass is done: what you changed, which
commands you ran and their exit codes, what is verified vs. still hypothesis,
and what the next pass should start with. Do not paste large tool output.
"""


def seed_prompt(state: LoopState, *, goal: str, repo_slug: str, conservative: bool) -> str:
    data = state.data
    brief = state_brief(state)
    overrides = state.prompt_overrides().strip()
    override_block = f"\nMaintainer guidance:\n{overrides}\n" if overrides else ""
    return f"""You are the NOVA3 autonomous reconstruction worker, starting fresh
in this checkout. The maintainer is not present. Repository: `{repo_slug}`.

Your standing objective (from the maintainer, via the installer):
{goal}
{override_block}
SETUP SEQUENCE FOR THIS FIRST PASS (then normal worker passes follow)
1. Read `AGENTS.md` and `docs/NOVA_TEAM.md` completely; then read
   `docs/progress/STATUS.md`, `NEXT.md`, `HANDOFF.md`,
   `docs/decisions/BLOCKERS.md`, `docs/OBJECT_MATCHING.md`,
   `docs/FAST_TRACK.md`.
2. Inspect the actual tree and evidence. Do not trust summaries you have not
   checked against files.
3. Write `.local/dsh-loop/BACKLOG.md` (private, git-ignored, loop-owned): a
   prioritised task queue with 8-12 items in the form
   `1. [ ] <task> -- evidence/acceptance: <how we will know it is done>`.
   Derive it from `NEXT.md`, `BLOCKERS.md` and what you find. Put the single
   highest-value next task first.
4. Run the verification suite: `bash tools/check_all.sh` and record its actual
   exit status and failures.
5. Then execute the first backlog task as far as you can this pass.
6. Commit, and update `docs/progress/STATUS.md`, `NEXT.md`, `HANDOFF.md` and
   append to `SESSION_LOG.md` exactly as described below.

WHERE THINGS STAND
{brief}

{STOP_WORDS}
{HOST_RULES}

Reply with a concise report: backlog summary, suite result, what you changed,
and what the next pass should start with.
"""
