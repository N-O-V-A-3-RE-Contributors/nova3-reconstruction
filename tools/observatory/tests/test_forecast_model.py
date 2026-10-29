"""Synthetic offline integration vectors; no original game data or services."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "nova3_forecast_model_test", Path(__file__).resolve().parents[1] / "forecast_model.py")
f = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = f
SPEC.loader.exec_module(f)

NOW = 2_000_000.0


def history(durations=(100, 100, 100, 100, 100), *, kind="source",
            cohort="small-v1", phase="execution", prefix="h", age=1000):
    """Each closure is a distinct task, not five sessions on one task."""
    return tuple(f.Attempt(
        id=f"{prefix}{i}", task_id=f"{prefix}-task{i}", cohort=cohort,
        kind=kind, phase=phase, started_at=NOW-age-i*1000-duration,
        ended_at=NOW-age-i*1000) for i, duration in enumerate(durations))


def queue_vector(**changes):
    """Active task + queued peer: 80 + 100 = 180 stage-work seconds."""
    s = f.Snapshot(
        now=NOW, observed_at=NOW, scope="two small source tasks",
        queue_complete=True,
        tasks=(f.Task("active", "small-v1", stage="active", stage_started_at=NOW-20),
               f.Task("queued", "small-v1")),
        workers=(f.Worker("worker", "active", NOW),), history=history())
    return replace(s, **changes)


def matching_vector(**changes):
    """Five independently evidenced units, one per 100-second sample."""
    rows = tuple(replace(a, matched_units=(f"unit{i}",), source_owned=True,
                         match_kind="linked_window", evidence=f"reviewed-match-{i}")
                 for i, a in enumerate(history(kind="matching", phase="matching")))
    evidence = f.MatchingEvidence(
        verified_original_matches=5, verified_count_evidence="reviewed-ledger-v1",
        inventory_functions=100, inventory_confidence="verified",
        inventory_evidence="independent-inventory-v1", cohort="small-v1",
        throughput_calibrated=True, calibration_evidence="representative-pilot-v1")
    return replace(f.Snapshot(NOW, NOW, "synthetic target", queue_complete=True,
                              history=rows, matching=evidence), **changes)


class ForecastTests(unittest.TestCase):
    def test_zero_matches_and_source_blocks_do_not_create_game_eta(self):
        s = queue_vector(matching=f.MatchingEvidence(verified_original_matches=0,
                                                    verified_count_evidence="reviewed-zero"))
        out = f.forecast(s)
        self.assertEqual(out["whole_game"]["label"], "UNKNOWN")
        self.assertIsNone(out["whole_game"]["eta_seconds"])
        self.assertIsNone(out["source_matching"]["per_worker_throughput"])
        self.assertIn("zero_verified_original_matches", out["source_matching"]["reasons"])

    def test_infrastructure_closures_never_supply_source_samples(self):
        out = f.forecast(queue_vector(history=history(tuple(range(1, 51)), kind="infrastructure")))
        self.assertIsNone(out["queue"]["runnable_seconds"])
        self.assertEqual(out["queue"]["sample_size"], 0)
        self.assertEqual(out["source_matching"]["sample_size"], 0)
        self.assertIsNone(out["whole_game"]["eta_seconds"])

    def test_queue_fixture_exact_output_and_distinct_samples(self):
        out = f.forecast(queue_vector())
        q = out["queue"]
        self.assertEqual(q["runnable_seconds"], {"p20": 180, "median": 180, "p80": 180})
        self.assertEqual(q["all_supplied_queue_seconds"], q["runnable_seconds"])
        self.assertEqual(q["sample_size"], 5)  # not 10 for two queued tasks
        self.assertEqual(q["confidence"], "low")
        self.assertEqual(q["active_workers"], {"execution": 1, "review": 0})
        self.assertEqual(out["as_of"], NOW)
        self.assertEqual(out["observed_at"], NOW)

    def test_empirical_percentiles(self):
        out = f.forecast(queue_vector(tasks=(f.Task("active", "small-v1", stage="active", stage_started_at=NOW),),
                                     history=history((100, 200, 300, 400, 500))))
        self.assertEqual(out["queue"]["runnable_seconds"], {"p20": 180, "median": 300, "p80": 420})

    def test_blocked_task_keeps_total_unknown_and_subset_visible(self):
        s = queue_vector()
        s = replace(s, tasks=s.tasks + (f.Task("blocked", "unknown-cohort", stage="blocked",
                                              blockers=("missing-toolchain",)),))
        q = f.forecast(s)["queue"]
        self.assertEqual(q["runnable_seconds"]["median"], 180)
        self.assertIsNone(q["all_supplied_queue_seconds"])
        self.assertEqual(q["blocked_tasks"]["blocked"], ["missing-toolchain", "task_blocked"])
        self.assertEqual(q["stage_counts"]["blocked"], 1)

    def test_all_blocked_has_no_completion_forecast(self):
        q = f.forecast(queue_vector(tasks=(f.Task("blocked", "small-v1", stage="blocked"),)))["queue"]
        self.assertEqual(q["state"], "unknown")
        self.assertIsNone(q["all_supplied_queue_seconds"])

    def test_zero_backlog_is_empty_without_samples_or_workers(self):
        q = f.forecast(f.Snapshot(NOW, NOW, "empty slice", queue_complete=True))["queue"]
        self.assertEqual(q["state"], "empty")
        self.assertEqual(q["all_supplied_queue_seconds"], {"p20": 0, "median": 0, "p80": 0})
        self.assertEqual(q["confidence"], "none")

    def test_incomplete_empty_snapshot_is_not_zero_backlog(self):
        q = f.forecast(f.Snapshot(NOW, NOW, "unknown queue"))["queue"]
        self.assertEqual(q["state"], "unknown")
        self.assertIsNone(q["runnable_seconds"])

    def test_idle_offline_unassigned_and_stale_workers_add_no_capacity(self):
        for worker in (f.Worker("w", "active", NOW, "idle"),
                       f.Worker("w", "active", NOW, "offline"),
                       f.Worker("w", None, NOW),
                       f.Worker("w", "queued", NOW),
                       f.Worker("w", "active", NOW-901),
                       f.Worker("w", "active", NOW+1)):
            with self.subTest(worker=worker):
                q = f.forecast(queue_vector(workers=(worker,)))["queue"]
                self.assertIsNone(q["runnable_seconds"])
                self.assertEqual(q["active_workers"]["execution"], 0)

    def test_duplicate_worker_assignments_cannot_multiply_capacity(self):
        workers = (f.Worker("a", "active", NOW), f.Worker("b", "active", NOW))
        q = f.forecast(queue_vector(workers=workers))["queue"]
        self.assertEqual(q["effective_workers"]["execution"], 1)
        self.assertEqual(q["runnable_seconds"]["median"], 180)

    def test_sublinear_worker_scaling_and_cap_and_serial_task_floor(self):
        def vector(n):
            active = tuple(f.Task(f"a{i}", "small-v1", stage="active", stage_started_at=NOW) for i in range(n))
            queued = tuple(f.Task(f"q{i}", "small-v1") for i in range(12-n))
            return queue_vector(tasks=active+queued,
                                workers=tuple(f.Worker(f"w{i}", f"a{i}", NOW) for i in range(n)))
        one, two, four, eight = (f.forecast(vector(n))["queue"] for n in (1, 2, 4, 8))
        self.assertEqual(one["runnable_seconds"]["median"], 1200)
        self.assertGreater(two["runnable_seconds"]["median"], 600)
        self.assertLess(two["runnable_seconds"]["median"], 1200)
        self.assertEqual(four["runnable_seconds"], eight["runnable_seconds"])
        self.assertEqual(four["effective_workers"]["execution"], 2)
        self.assertEqual(f.forecast(queue_vector(tasks=(queue_vector().tasks[0],)))["queue"]["runnable_seconds"]["median"], 80)

    def test_stale_or_future_snapshot_suppresses_forecasts(self):
        for observed in (NOW-901, NOW+1):
            out = f.forecast(queue_vector(observed_at=observed))
            self.assertIsNone(out["queue"]["runnable_seconds"])
            self.assertIn("stale_or_future_snapshot", out["queue"]["reasons"])
            self.assertIsNone(out["source_matching"]["per_worker_throughput"])

    def test_review_has_separate_samples_capacity_and_ci_gate(self):
        review = f.Task("review", "small-v1", stage="review", stage_started_at=NOW-10)
        s = queue_vector(tasks=(review,), workers=(f.Worker("reviewer", "review", NOW),),
                         prs=(f.PullRequest("review", NOW),), ci=(f.CI("review", NOW, "passed"),),
                         history=history(phase="review"))
        q = f.forecast(s)["queue"]
        self.assertEqual(q["runnable_seconds"]["median"], 90)
        self.assertEqual(q["active_workers"]["review"], 1)
        self.assertIsNone(f.forecast(replace(s, history=history()))["queue"]["runnable_seconds"])
        for check in (f.CI("review", NOW, "failed"), f.CI("review", NOW, "pending"),
                      f.CI("review", NOW, "unknown"), f.CI("review", NOW-901, "passed")):
            with self.subTest(check=check):
                q = f.forecast(replace(s, ci=(check,)))["queue"]
                self.assertIsNone(q["all_supplied_queue_seconds"])
                self.assertIn("review", q["blocked_tasks"])
        for pr in (None, f.PullRequest("review", NOW, "merged"),
                   f.PullRequest("review", NOW, "closed"), f.PullRequest("review", NOW, draft=True),
                   f.PullRequest("review", NOW+1)):
            q = f.forecast(replace(s, prs=() if pr is None else (pr,)))["queue"]
            self.assertIn("review", q["blocked_tasks"])

    def test_recent_cohort_and_regime_changes(self):
        old = history((5,)*5, cohort="old-v1", prefix="old")
        now = history((500,)*5, prefix="new")
        q = f.forecast(queue_vector(history=old+now))["queue"]
        self.assertEqual(q["runnable_seconds"]["median"], 980)
        aged = history((5,)*5, age=15*86400)
        out = f.forecast(queue_vector(history=aged+now))
        self.assertEqual(out["queue"]["runnable_seconds"]["median"], 980)
        self.assertEqual(out["excluded_history"]["old_history"], 5)

    def test_only_latest_bounded_samples_are_used(self):
        rows = history((100,)*5 + (1000,)*5)
        out = f.forecast(queue_vector(history=rows), f.Policy(max_samples=5))
        self.assertEqual(out["queue"]["runnable_seconds"]["median"], 180)
        self.assertEqual(out["queue"]["sample_size"], 5)

    def test_retry_includes_failed_attempt_and_backoff(self):
        timeout = f.Attempt("retry1", "retry-task", "small-v1", "source", "execution", NOW-500, NOW-450, "timeout")
        complete = replace(timeout, id="retry2", number=2, started_at=NOW-400, ended_at=NOW-200, outcome="completed")
        rows = history((300,)*4) + (timeout, complete)
        q = f.forecast(queue_vector(history=rows))["queue"]
        self.assertEqual(q["runnable_seconds"]["median"], 580)
        self.assertEqual(q["sample_size"], 5)

    def test_unresolved_timeout_failed_cancelled_are_censored_not_successes(self):
        for outcome in ("timeout", "failed", "cancelled"):
            a = replace(history(prefix="censored")[0], outcome=outcome)
            out = f.forecast(queue_vector(history=history()+(a,)))
            self.assertIsNone(out["queue"]["runnable_seconds"])
            self.assertEqual(out["excluded_history"]["unresolved_censored_history"], 1)
            self.assertEqual(out["queue"]["per_task"][0]["censored_tasks"], 1)

    def test_censored_other_cohort_does_not_poison_comparable_samples(self):
        a = replace(history(prefix="censored", cohort="other")[0], outcome="timeout")
        self.assertEqual(f.forecast(queue_vector(history=history()+(a,)))["queue"]["runnable_seconds"]["median"], 180)

    def test_invalid_history_and_missing_retry_never_create_durations(self):
        a = history(prefix="invalid")[0]
        for bad in (replace(a, started_at=a.ended_at+1),
                    replace(a, started_at=a.ended_at), replace(a, ended_at=NOW+1),
                    replace(a, number=2), replace(a, started_at=NOW-15*86400)):
            out = f.forecast(queue_vector(history=history()+(bad,)))
            self.assertIsNone(out["queue"]["runnable_seconds"])
            self.assertEqual(out["excluded_history"]["invalid_or_incomplete_history"], 1)

    def test_active_duration_exceeding_history_is_unknown_not_zero(self):
        task = replace(queue_vector().tasks[0], stage_started_at=NOW-200)
        q = f.forecast(queue_vector(tasks=(task,)))["queue"]
        self.assertIsNone(q["runnable_seconds"])
        self.assertEqual(q["sample_size"], 0)

    def test_unknown_and_future_active_start_are_blocked(self):
        for started in (None, NOW+1):
            task = replace(queue_vector().tasks[0], stage_started_at=started)
            self.assertIn("active", f.forecast(queue_vector(tasks=(task,)))["queue"]["blocked_tasks"])

    def test_session_budget_is_not_a_completion_eta(self):
        session = f.Session("s", "active", NOW-1500, 1200)
        out = f.forecast(queue_vector(sessions=(session,)))["sessions"][0]
        self.assertEqual(out["elapsed_seconds"], 1500)
        self.assertEqual(out["remaining_budget_seconds"], 0)
        self.assertTrue(out["ceiling_exceeded"])
        self.assertIsNone(out["completion_eta_seconds"])

    def test_timeout_session_blocks_current_task_until_reconciled(self):
        timeout = f.Session("s", "active", NOW-1200, 1200, NOW-1, "timeout")
        out = f.forecast(queue_vector(sessions=(timeout,)))
        self.assertIn("session_timeout_requires_reconciliation", out["queue"]["blocked_tasks"]["active"])
        self.assertEqual(out["queue"]["active_workers"]["execution"], 0)
        retry = f.Session("s2", "active", NOW-1, 1200)
        out = f.forecast(queue_vector(sessions=(timeout, retry)))
        self.assertNotIn("active", out["queue"]["blocked_tasks"])

    def test_invalid_and_stale_sessions_never_extrapolate(self):
        for session in (f.Session("s", "active", NOW+1, 100),
                        f.Session("s", "active", NOW-10, 100, NOW+1, "completed"),
                        f.Session("s", "active", NOW-10, 100, NOW-20, "completed"),
                        f.Session("s", "active", NOW-10, 100, outcome="completed")):
            out = f.forecast(queue_vector(sessions=(session,)))["sessions"][0]
            self.assertIsNone(out["elapsed_seconds"])
            self.assertEqual(out["state"], "unknown")
        s = queue_vector(sessions=(f.Session("s", "active", NOW-1000, 1200),), observed_at=NOW-901)
        self.assertIsNone(f.forecast(s)["sessions"][0]["elapsed_seconds"])

    def test_setup_states_require_evidence_and_freshness(self):
        for state in ("unknown", "pending", "blocked", "in-progress", "complete"):
            m = f.Milestone("compiler", state, ("local-check-id",))
            out = f.forecast(queue_vector(milestones=(m,)))["setup"]
            self.assertEqual(out["state"], state)
            self.assertEqual(out["milestones"][0]["evidence"], ["local-check-id"])
            self.assertEqual(f.forecast(queue_vector(milestones=(replace(m, evidence=()),)))["setup"]["state"], "unknown")
        milestones = (f.Milestone("a", "complete", ("a-proof",)), f.Milestone("b", "blocked", ("b-proof",)))
        self.assertEqual(f.forecast(queue_vector(milestones=milestones))["setup"]["state"], "blocked")
        self.assertEqual(f.forecast(queue_vector(milestones=milestones, observed_at=NOW-901))["setup"]["state"], "unknown")
        self.assertEqual(f.forecast(queue_vector())["setup"]["state"], "unknown")

    def test_calibrated_matching_target_still_is_not_whole_game_eta(self):
        out = f.forecast(matching_vector())
        match = out["source_matching"]
        self.assertEqual(match["per_worker_throughput"], {"p20": .01, "median": .01, "p80": .01})
        self.assertEqual(match["target_remaining_seconds"], {"p20": 9500, "median": 9500, "p80": 9500})
        self.assertEqual(match["sample_size"], 5)
        self.assertIsNone(out["whole_game"]["eta_seconds"])

    def test_matching_denominator_and_calibration_gate_only_target(self):
        s = matching_vector()
        for evidence in (replace(s.matching, inventory_confidence="partial"),
                         replace(s.matching, inventory_functions=None),
                         replace(s.matching, inventory_evidence=""),
                         replace(s.matching, throughput_calibrated=False),
                         replace(s.matching, calibration_evidence=""),
                         replace(s.matching, inventory_functions=4)):
            out = f.forecast(replace(s, matching=evidence))["source_matching"]
            self.assertIsNotNone(out["per_worker_throughput"])
            self.assertIsNone(out["target_remaining_seconds"])
            self.assertTrue(out["target_reasons"])

    def test_matching_requires_real_source_match_evidence(self):
        s = matching_vector()
        for change in ({"source_owned": False}, {"evidence": ""}, {"match_kind": None},
                       {"matched_units": ()}, {"kind": "infrastructure"}):
            out = f.forecast(replace(s, history=tuple(replace(a, **change) for a in s.history)))
            self.assertIsNone(out["source_matching"]["per_worker_throughput"])
            self.assertIsNone(out["whole_game"]["eta_seconds"])

    def test_duplicate_units_and_count_contradictions_are_unknown(self):
        s = matching_vector()
        rows = tuple(replace(a, matched_units=("same-unit",)) for a in s.history)
        self.assertIn("duplicate_matching_unit_ids", f.forecast(replace(s, history=rows))["source_matching"]["reasons"])
        s = replace(s, matching=replace(s.matching, verified_original_matches=4))
        self.assertIn("samples_exceed_verified_match_count", f.forecast(s)["source_matching"]["reasons"])

    def test_complete_matching_inventory_has_zero_remaining_target(self):
        s = matching_vector()
        out = f.forecast(replace(s, matching=replace(s.matching, inventory_functions=5)))
        self.assertEqual(out["source_matching"]["target_remaining_seconds"]["median"], 0)
        self.assertIsNone(out["whole_game"]["eta_seconds"])

    def test_queue_bound_and_fewer_samples_do_not_fabricate_estimate(self):
        self.assertIsNone(f.forecast(queue_vector(), f.Policy(max_queue_tasks=1))["queue"]["runnable_seconds"])
        self.assertIsNone(f.forecast(queue_vector(history=history((100,)*4)))["queue"]["runnable_seconds"])

    def test_forecast_horizon_is_a_gate_not_an_artificially_clipped_range(self):
        q = f.forecast(queue_vector(), f.Policy(max_forecast_seconds=150))["queue"]
        self.assertIsNone(q["runnable_seconds"])
        self.assertIn("queue_exceeds_short_range_horizon", q["reasons"])
        self.assertEqual(q["horizon_seconds"], 150)

    def test_confidence_tracks_independent_completions_not_backlog(self):
        q = f.forecast(queue_vector(history=history((100,)*10)))["queue"]
        self.assertEqual(q["confidence"], "medium")
        self.assertEqual(q["sample_size"], 10)

    def test_bad_numbers_enums_duplicates_and_bounds_rejected(self):
        for value in (-1, float("nan"), float("inf"), True, 10**1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                f.forecast(queue_vector(now=value))
        for s in (queue_vector(tasks=(f.Task("x", "small-v1", stage="invented"),)),
                  queue_vector(tasks=(queue_vector().tasks[0],)*2),
                  queue_vector(history=history()+history()),
                  queue_vector(matching=f.MatchingEvidence(verified_original_matches=-1)),
                  queue_vector(matching=f.MatchingEvidence(verified_count_evidence=" ")),
                  queue_vector(sessions=(f.Session("s", "a", NOW, -1),))):
            with self.subTest(snapshot=s), self.assertRaises(ValueError):
                f.forecast(s)
        for policy in (f.Policy(min_samples=1), f.Policy(worker_exponent=1),
                       f.Policy(worker_cap=100), f.Policy(max_records=1)):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                f.forecast(queue_vector(), policy)

    def test_pure_repeatable_json_output_and_inputs_unchanged(self):
        s = queue_vector()
        original = repr(s)
        with patch("builtins.open", side_effect=AssertionError("no IO")), \
                patch("time.time", side_effect=AssertionError("no clock")):
            first, second = f.forecast(s), f.forecast(s)
        self.assertEqual(first, second)
        self.assertEqual(repr(s), original)
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        # History order, task order and worker order are immaterial.
        reordered = replace(s, history=tuple(reversed(s.history)), tasks=tuple(reversed(s.tasks)))
        self.assertEqual(first, f.forecast(reordered))


if __name__ == "__main__":
    unittest.main()
