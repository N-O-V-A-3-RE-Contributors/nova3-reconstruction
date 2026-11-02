window.__ModuleLoader__.load({
  id: "@nova3/dsh-control",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;
    const { useCallback, useEffect, useMemo, useState } = React;

    const CSS = `
.nv3m-root{height:100%;overflow:auto;background:var(--dsw-alias-bg-l1,#0f1115);color:var(--dsw-alias-label-primary,#e6e8ee);font-family:var(--dsw-font-sans,system-ui,sans-serif)}
.nv3m-wrap{max-width:1120px;margin:0 auto;padding:22px 20px 60px}
.nv3m-top{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.nv3m-title{font-size:22px;font-weight:650;letter-spacing:.06em}
.nv3m-sub{color:var(--dsw-alias-label-tertiary,#8b93a7);font-size:12px;margin-top:4px}
.nv3m-pill{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:4px 11px;font-size:12px;font-weight:600;border:1px solid var(--dsw-alias-border-l2,#2a2f3a);background:var(--dsw-alias-fill-l1,#171a21)}
.nv3m-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.nv3m-card{border:1px solid var(--dsw-alias-border-l2,#262b35);background:var(--dsw-alias-bg-layer-1,#151820);border-radius:12px;padding:14px 16px}
.nv3m-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.nv3m-label{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--dsw-alias-label-tertiary,#8b93a7);margin-bottom:6px}
.nv3m-big{font-size:24px;font-weight:650;line-height:1.15}
.nv3m-mid{font-size:15px;font-weight:600}
.nv3m-small{font-size:12px;color:var(--dsw-alias-label-secondary,#aab2c5);line-height:1.5}
.nv3m-mono{font-family:var(--dsw-font-mono,ui-monospace,monospace);font-variant-numeric:tabular-nums}
.nv3m-bar{height:8px;border-radius:999px;background:var(--dsw-alias-fill-l2,#212634);overflow:hidden;margin:6px 0}
.nv3m-bar>div{height:100%;border-radius:999px;background:linear-gradient(90deg,#3d7eff,#61d0ff)}
.nv3m-bar.alt>div{background:linear-gradient(90deg,#f0a23c,#ffd166)}
.nv3m-bar.ok>div{background:linear-gradient(90deg,#22a06b,#5be0a3)}
.nv3m-row{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.nv3m-hr{height:1px;background:var(--dsw-alias-border-l1,#22262f);margin:12px 0}
.nv3m-btn{border:1px solid var(--dsw-alias-border-l2,#2c3240);background:var(--dsw-alias-fill-l1,#1b1f29);color:var(--dsw-alias-label-primary,#e6e8ee);border-radius:9px;padding:7px 12px;font-size:12px;font-weight:600;cursor:pointer}
.nv3m-btn:hover{background:var(--dsw-alias-fill-l2,#242a37)}
.nv3m-btn:disabled{opacity:.5;cursor:not-allowed}
.nv3m-btn.on{background:rgba(43,160,107,.16);border-color:rgba(43,160,107,.55);color:#7ff0b6}
.nv3m-btn.off{background:rgba(240,90,90,.12);border-color:rgba(240,90,90,.45);color:#ff9b9b}
.nv3m-tag{font-size:10px;border:1px solid var(--dsw-alias-border-l2,#2c3240);border-radius:5px;padding:1px 6px;color:var(--dsw-alias-label-tertiary,#8b93a7)}
.nv3m-note{border-left:3px solid #f0a23c;padding:8px 11px;background:rgba(240,162,60,.08);border-radius:6px;font-size:12px;color:var(--dsw-alias-label-secondary,#c2c8d6)}
.nv3m-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.nv3m-kv{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;font-size:12px}
.nv3m-kv b{color:var(--dsw-alias-label-tertiary,#8b93a7);font-weight:500}
.nv3m-clock{font-size:12px;color:var(--dsw-alias-label-tertiary,#8b93a7);text-align:right}
.nv3m-err{border:1px solid rgba(240,90,90,.5);background:rgba(240,90,90,.08);padding:12px 14px;border-radius:10px;font-size:13px;white-space:pre-wrap}
@media (max-width:620px){.nv3m-wrap{padding:14px 12px 40px}.nv3m-big{font-size:20px}}
`;

    function injectCss() {
      if (typeof document === "undefined") return;
      if (document.querySelector('style[data-plugin-css="nova3-mission"]')) return;
      const tag = document.createElement("style");
      tag.dataset.plugin = "@nova3/dsh-control";
      tag.dataset.pluginCss = "nova3-mission";
      tag.textContent = CSS;
      document.head.appendChild(tag);
    }

    const STATE_COLOR = {
      RUNNING: "#3ddc84", BACKOFF: "#f0c24c", WAITING_CREDITS: "#ff6b6b",
      WAITING_APPROVAL: "#5aa9ff", BLOCKED: "#ff8f5a", COMPLETE: "#3ddc84",
    };

    function pct(value, digits) {
      if (typeof value !== "number" || !isFinite(value)) return "—";
      return (value * 100).toFixed(digits === undefined ? 2 : digits) + "%";
    }

    function humanDays(days) {
      if (typeof days !== "number" || !isFinite(days)) return "—";
      if (days < 1) return "under a day";
      if (days < 45) return Math.round(days) + " days";
      if (days < 730) return (days / 30.44).toFixed(1) + " months";
      return (days / 365.25).toFixed(1) + " years";
    }

    function dayDate(days) {
      if (typeof days !== "number" || !isFinite(days)) return "—";
      const when = new Date(Date.now() + days * 86400000);
      return when.toLocaleDateString(undefined,
        { year: "numeric", month: "short", day: "numeric" });
    }

    function num(value) {
      return typeof value === "number" && isFinite(value) ? value.toLocaleString() : "—";
    }

    function ago(unix) {
      if (!unix) return "never";
      const s = Math.max(0, Math.round(Date.now() / 1000 - unix));
      if (s < 90) return s + "s ago";
      if (s < 5400) return Math.round(s / 60) + "m ago";
      if (s < 172800) return (s / 3600).toFixed(1) + "h ago";
      return Math.round(s / 86400) + "d ago";
    }

    function Pill({ color, text, title }) {
      return h("span", { className: "nv3m-pill", title }, [
        h("span", { key: "d", className: "nv3m-dot", style: { background: color } }),
        h("span", { key: "t" }, text),
      ]);
    }

    function Card({ label, children, style }) {
      return h("div", { className: "nv3m-card", style },
        label ? h("div", { className: "nv3m-label" }, label) : null, children);
    }

    function Bar({ value, max, cls, label, right }) {
      const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
      return h("div", null, [
        h("div", { key: "r", className: "nv3m-row" }, [
          h("span", { key: "l", className: "nv3m-small" }, label),
          h("span", { key: "v", className: "nv3m-small nv3m-mono" }, right),
        ]),
        h("div", { key: "b", className: "nv3m-bar " + (cls || "") },
          h("div", { style: { width: (ratio * 100).toFixed(2) + "%" } })),
      ]);
    }

    function Scenario({ name, scenario, accent }) {
      if (!scenario) return null;
      const days = scenario.days || [null, null, null];
      return h("div", { className: "nv3m-card", style: { flex: "1 1 260px" } }, [
        h("div", { key: "l", className: "nv3m-label" }, name),
        h("div", { key: "b", className: "nv3m-big", style: { color: accent } },
          humanDays(days[1])),
        h("div", { key: "d", className: "nv3m-small nv3m-mono" },
          dayDate(days[0]) + "  →  " + dayDate(days[2])),
        h("div", { key: "r", className: "nv3m-small", style: { marginTop: "6px" } },
          num(scenario.remaining) + " remaining · " + (scenario.label || "")),
      ]);
    }

    function MissionView() {
      const [data, setData] = useState(null);
      const [error, setError] = useState("");
      const [busy, setBusy] = useState("");
      const [loading, setLoading] = useState(true);
      const [clock, setClock] = useState(Date.now());

      const load = useCallback(async () => {
        try {
          const response = await fetch("/nova3/api/mission", {
            cache: "no-store", headers: { accept: "application/json" },
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || ("HTTP " + response.status));
          setData(payload);
          setError("");
        } catch (failure) {
          setError(String(failure.message || failure));
        } finally {
          setLoading(false);
        }
      }, []);

      useEffect(() => { load(); }, [load]);
      useEffect(() => {
        const refresh = setInterval(load, 20000);
        return () => clearInterval(refresh);
      }, [load]);
      useEffect(() => {
        const tick = setInterval(() => setClock(Date.now()), 1000);
        return () => clearInterval(tick);
      }, []);

      const act = useCallback(async (path, body) => {
        setBusy(path);
        try {
          const response = await fetch(path, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body || {}),
          });
          const payload = await response.json();
          if (!response.ok || payload.ok === false) {
            throw new Error(payload.error || ("HTTP " + response.status));
          }
          if (payload.state) setData(payload.state);
          else await load();
          setError("");
        } catch (failure) {
          setError(String(failure.message || failure));
        } finally {
          setBusy("");
        }
      }, [load]);

      const model = useMemo(() => {
        if (!data) return null;
        const project = data.project || {};
        const inventory = project.inventory || {};
        const progress = project.progress || {};
        const estimate = project.estimate || {};
        const autopilot = data.autopilot || {};
        const worker = data.worker || {};
        const assessment = worker.assessment || {};
        const queue = assessment.queue || {};
        const scenarios = estimate.scenarios || {};
        const recorded = inventory.functions_recorded || 0;
        const elf = inventory.elf_functions || 0;
        const implemented = progress.mappings_implemented || 0;
        return { project, inventory, progress, estimate, autopilot, worker, assessment,
          queue, scenarios, recorded, elf, implemented };
      }, [data]);

      if (loading && !data) {
        return h("div", { className: "nv3m-root" },
          h("div", { className: "nv3m-wrap" }, h("div", { className: "nv3m-small" },
            "Loading Mission Control…")));
      }
      if (!data) {
        return h("div", { className: "nv3m-root" },
          h("div", { className: "nv3m-wrap" }, [
            h("div", { key: "t", className: "nv3m-title" }, "MISSION CONTROL"),
            h("div", { key: "e", className: "nv3m-err", style: { marginTop: "14px" } },
              "The Mission endpoint is unavailable.\n" + error +
              "\n\nCheck the host log and run: python -m nova3_team.mission --json"),
          ]));
      }

      const { project, inventory, progress, estimate, autopilot, worker, queue,
        scenarios, recorded, elf, implemented } = model;
      const workerState = worker.state || "not started";
      const autoMode = autopilot.mode || "unset";
      const autoOn = autoMode === "on";
      const paused = autoMode === "paused_credits";
      const source = data.source_reference || {};
      const etaReady = estimate.state && estimate.state !== "unavailable";
      const rate = estimate.rate || {};

      return h("div", { className: "nv3m-root" },
        h("div", { className: "nv3m-wrap" }, [
          // Header
          h("div", { key: "top", className: "nv3m-top" }, [
            h("div", { key: "l" }, [
              h("div", { key: "t", className: "nv3m-title" }, "MISSION CONTROL"),
              h("div", { key: "s", className: "nv3m-sub" },
                "N.O.V.A. 3 · independent source reconstruction · " + (data.repo || "")),
            ]),
            h("div", { key: "r", style: { textAlign: "right" } }, [
              h("div", { key: "p", style: { display: "flex", gap: "8px", justifyContent: "flex-end", flexWrap: "wrap" } }, [
                h(Pill, { key: "w", color: STATE_COLOR[workerState] || "#8b93a7",
                  text: "worker " + workerState, title: worker.reason || "" }),
                h(Pill, { key: "a", color: paused ? "#f0c24c" : (autoOn ? "#3ddc84" : "#8b93a7"),
                  text: "automation " + autoMode, title: autopilot.path || "" }),
              ]),
              h("div", { key: "c", className: "nv3m-clock", style: { marginTop: "7px" } },
                "updated " + ago(data.generated_unix) + " · " +
                new Date(clock).toLocaleTimeString()),
            ]),
          ]),

          error ? h("div", { key: "err", className: "nv3m-err", style: { marginBottom: "12px" } }, error) : null,

          // KPI row
          h("div", { key: "kpi", className: "nv3m-grid" }, [
            h(Card, { key: "w", label: "current task" }, [
              h("div", { key: "n", className: "nv3m-mid" },
                worker.task ? ("#" + worker.task + " " + (worker.task_title || "")) : "idle"),
              h("div", { key: "m", className: "nv3m-small", style: { marginTop: "5px" } },
                "last heartbeat " + ago(worker.updated_unix) + " · " +
                num(worker.sessions_today) + " sessions today"),
            ]),
            h(Card, { key: "e", label: "elapsed" }, [
              h("div", { key: "b", className: "nv3m-big" }, num(project.elapsed_days) + " days"),
              h("div", { key: "s", className: "nv3m-small", style: { marginTop: "5px" } },
                "work started " + (project.work_started || "—") +
                " · " + num(worker.tokens_today) + " tokens today"),
            ]),
            h(Card, { key: "c", label: "completion" }, [
              h("div", { key: "b", className: "nv3m-big" },
                pct(project.completion && project.completion.recorded_of_elf)),
              h("div", { key: "s", className: "nv3m-small", style: { marginTop: "5px" } },
                "of the ELF function inventory is recorded; " +
                num(implemented) + " mappings implemented"),
            ]),
            h(Card, { key: "s", label: "source reference" }, [
              h("div", { key: "b", className: "nv3m-mid" },
                source.present ? "present" : "not materialised"),
              h("div", { key: "s", className: "nv3m-small", style: { marginTop: "5px" } },
                source.present
                  ? (source.indexed ? "indexed · " + (source.index.files || "?") + " files"
                    : "not indexed yet · pre-release engine tree")
                  : (source.note || "")),
            ]),
          ]),

          // ETA
          h("div", { key: "eta", style: { marginTop: "12px" } },
            h(Card, { label: "eta · dynamic, evidence-based" }, [
              etaReady
                ? h("div", { key: "b", style: { display: "flex", gap: "10px", flexWrap: "wrap" } }, [
                    h(Scenario, { key: "r", name: "recorded inventory",
                      scenario: scenarios.recorded_inventory, accent: "#61d0ff" }),
                    h(Scenario, { key: "f", name: "full ELF (naive floor)",
                      scenario: scenarios.full_elf_at_recorded_rate, accent: "#f0c24c" }),
                  ])
                : h("div", { key: "u", className: "nv3m-note" }, "ETA unavailable: " +
                    (estimate.reasons || []).join("; ")),
              h("div", { key: "r", className: "nv3m-small", style: { marginTop: "10px" } },
                "rate " + (rate.mappings_per_day !== undefined
                  ? rate.mappings_per_day.toFixed(2) + " mappings/day over " + rate.days +
                    " days (" + rate.window + ")"
                  : "not measurable yet") +
                " · confidence " + (estimate.confidence || "—") +
                " · state " + (estimate.state || "—")),
              estimate.rebaseline
                ? h("div", { key: "n", className: "nv3m-note", style: { marginTop: "9px" } },
                    estimate.rebaseline)
                : null,
            ])),

          // Completion
          h("div", { key: "comp", style: { marginTop: "12px" } },
            h(Card, { label: "completion statistics" }, [
              h(Bar, { key: "i", value: recorded, max: elf,
                label: "function inventory recorded (" + num(recorded) + " / " + num(elf) + ")",
                right: pct(inventory.inventory_coverage) }),
              h(Bar, { key: "m", value: implemented, max: Math.max(1, recorded), cls: "alt",
                label: "mappings implemented (" + num(implemented) + " / " + num(recorded) + " recorded)",
                right: pct(implemented / Math.max(1, recorded)) }),
              h(Bar, { key: "a", value: progress.mappings_matched_arm || 0, max: Math.max(1, implemented), cls: "ok",
                label: "instruction-window matched ARM (" + num(progress.mappings_matched_arm) + " / " + num(implemented) + " implemented)",
                right: pct((progress.mappings_matched_arm || 0) / Math.max(1, implemented)) }),
              h("div", { key: "n", className: "nv3m-small", style: { marginTop: "8px" } },
                num(progress.mappings_behavioural) + " behavioural · " +
                num(progress.utilities_verified) + " utilities verified · " +
                num(progress.source_modules) + " source modules · " +
                "inventory " + (inventory.inventory_complete ? "complete" : "INCOMPLETE")),
            ])),

          // Queue + next
          h("div", { key: "mid", className: "nv3m-grid", style: { marginTop: "12px" } }, [
            h(Card, { key: "q", label: "queue" }, [
              h("div", { key: "g", className: "nv3m-kv" }, [
                h("b", { key: "1" }, "runnable"), h("span", { key: "1v" }, num(queue.runnable)),
                h("b", { key: "2" }, "claimed"), h("span", { key: "2v" }, num(queue.claimed)),
                h("b", { key: "3" }, "in review"), h("span", { key: "3v" }, num(queue.review)),
                h("b", { key: "4" }, "blocked"), h("span", { key: "4v" }, num(queue.blocked)),
                h("b", { key: "5" }, "open PRs"), h("span", { key: "5v" }, num(queue.open_prs)),
                h("b", { key: "6" }, "proposals"), h("span", { key: "6v" },
                  num(model.assessment.pending_proposal_count)),
              ]),
            ]),
            h(Card, { key: "n", label: "next" }, [
              h("div", { key: "t", className: "nv3m-small" },
                data.next_task || "See docs/progress/NEXT.md"),
              h("div", { key: "a", className: "nv3m-actions" }, [
                h("button", { key: "r", className: "nv3m-btn", disabled: !!busy,
                  onClick: () => act("/nova3/api/run", {}) },
                  busy === "/nova3/api/run" ? "running…" : "Run one pass now"),
                h("button", { key: "f", className: "nv3m-btn", disabled: loading,
                  onClick: load }, "Refresh"),
              ]),
            ]),
          ]),

          // Automation control
          h("div", { key: "auto", style: { marginTop: "12px" } },
            h(Card, { label: "automation · persistent switch" }, [
              h("div", { key: "r", className: "nv3m-row" }, [
                h("div", { key: "l" }, [
                  h("div", { key: "t", className: "nv3m-mid" },
                    autoOn ? "ON — no expiry, no task budget" :
                      (paused ? "PAUSED — credit depleted" : "OFF")),
                  h("div", { key: "s", className: "nv3m-small", style: { marginTop: "4px" } },
                    paused
                      ? "The switch stays on. It resumes automatically on the next tick once the account can pay again."
                      : "Approves safe planner proposals and merges PRs that pass the read-only reviewer with green checks. It pauses only when credit is depleted."),
                ]),
                h("div", { key: "b", style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, [
                  h("button", {
                    key: "on", className: "nv3m-btn " + (autoOn ? "on" : ""),
                    disabled: !!busy,
                    onClick: () => act("/nova3/api/automation", { enabled: !autoOn }),
                  }, busy === "/nova3/api/automation" ? "saving…" : (autoOn ? "Turn automation off" : "Turn automation on")),
                  h("button", {
                    key: "m", className: "nv3m-btn " + (autopilot.auto_merge ? "on" : "off"),
                    disabled: !!busy || !autoOn,
                    onClick: () => act("/nova3/api/automation",
                      { auto_merge: !autopilot.auto_merge }),
                  }, "auto-merge " + (autopilot.auto_merge ? "on" : "off")),
                ]),
              ]),
              h("div", { key: "f", className: "nv3m-small", style: { marginTop: "10px" } },
                "switch record: " + (autopilot.path || "not configured") +
                " · actor " + ((autopilot.grant && autopilot.grant.actor) || "—")),
            ])),
        ]));
    }

    function apply(ctx) {
      injectCss();
      ctx.slots.inject("conversation.view", () => ctx.slots.register({
        name: "conversation.view",
        id: "mission",
        order: 25,
        label: () => "Mission",
      }, MissionView));
    }

    exports.apply = apply;
    exports.inject = ["slots"];
    return module.exports;
  },
});
