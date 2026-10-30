"""Offline, deterministic tests for the token wallet and evidence router.

No network, no paid provider call, no original game binary.  All money is
integer micro-USD and all time is an explicit fixed epoch.
"""
import json
import os
import sqlite3
import tempfile
import unittest

from nova3_team import token_wallet as tw


NOW = 1_800_000_000
DAY = 86_400
IN = 1_000_000          # $1.00 per million input tokens
OUT = 4_000_000         # $4.00 per million output tokens
CACHED = 100_000        # $0.10 per million cached input tokens
SNAP = "deepseek-20261030"


def usd(value):
    return tw.to_micros(value)


def policy(day="1", month="10", *, mode=tw.METERED, enforce=True):
    if mode == tw.SUBSCRIPTION:
        return tw.WalletPolicy("alice", mode, None, None, enforce)
    return tw.WalletPolicy("alice", mode, usd(day), usd(month), enforce)


def task(task_id="t1", issue="131", branch="nova/issue-131", **over):
    values = dict(
        task_id=task_id, issue=issue, branch=branch, provider="provA",
        model="model-a", snapshot=SNAP, estimated_micros=usd("0.01"),
        max_output_tokens=2000, max_context_tokens=10000, category="compiler",
        risk="medium", privacy_class="public", input_rate=IN,
        output_rate=OUT, cached_rate=CACHED)
    values.update(over)
    return tw.TaskRequest(**values)


def wallet(**over):
    return tw.Wallet(":memory:", policy=policy(**over))


def slot(slot_id="a", provider="provA", model="model-a", **over):
    values = dict(
        slot_id=slot_id, provider=provider, model=model, snapshot=SNAP,
        metered=True, input_rate=IN, output_rate=OUT, cached_rate=CACHED,
        privacy_classes=frozenset({"public", "restricted"}),
        tools=frozenset({"ghidra", "gh", "cmake"}),
        binaries=frozenset({"ida", "gcc"}),
        approval_roles=frozenset({"owner"}), max_context_tokens=200_000,
        strength_class="frontier")
    values.update(over)
    return tw.Slot(**values)


def attestation(slot_id="a", machine="a" * 32, **over):
    values = dict(slot_id=slot_id, machine_id=machine, lease_id="lease-0001",
                  attested_at=NOW, lease_expires_at=NOW + 300,
                  process_locked=True, healthy=True, branch=None)
    values.update(over)
    return tw.SlotAttestation(**values)


def route(task_id="t1", issue="131", branch="nova/issue-131", **over):
    values = dict(
        task_id=task_id, issue=issue, branch=branch, category="compiler",
        risk="medium", privacy_class="public", snapshot=SNAP,
        estimated_micros=usd("0.01"), max_output_tokens=2000,
        max_context_tokens=10000, enqueued_at=NOW)
    values.update(over)
    return tw.RouteRequest(**values)


def evidence(evidence_id, provider="provA", model="model-a",
             category="compiler", success=True, proof="verified", wall=600,
             billed=1000):
    return tw.Evidence(evidence_id, provider, model, category, "accepted_ci",
                       success, proof, wall, billed, False, "ci-runner-1",
                       NOW)


def record_strength(w, provider, model, category, successes=6, total=6,
                    wall=600):
    for index in range(total):
        w.record_evidence(evidence(
            f"ev-{provider}-{category}-{index}", provider, model, category,
            index < successes, "verified", wall, 1000))


class MoneyTest(unittest.TestCase):
    def test_micros_reject_float_and_excess_precision(self):
        self.assertEqual(usd("1.25"), 1_250_000)
        with self.assertRaises(tw.WalletError):
            tw.to_micros(1.25)
        with self.assertRaises(tw.WalletError):
            tw.to_micros("0.0000001")
        self.assertEqual(tw.format_usd(1_250_000), "1.250000")
        self.assertEqual(tw.format_usd(None), "UNKNOWN")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.w = wallet(day="1", month="10")
        self.p = policy(day="1", month="10")
        self.w.register_policy(self.p)

    def tearDown(self):
        self.w.close()

    def test_reserve_before_dispatch_and_settle_actual_usage(self):
        reservation = self.w.reserve(task(), now=NOW)
        self.assertEqual(reservation.status, "reserved")
        self.assertEqual(reservation.reserved_micros, task().reserved_ceiling())
        self.assertEqual(self.w.spent_today(NOW), 0)
        self.assertEqual(self.w.reserved_today(NOW), reservation.reserved_micros)
        settled = self.w.settle(reservation.reservation_id,
                                tw.Usage(1000, 200, 500), now=NOW + 1)
        self.assertEqual(settled.usage_state, "measured")
        # 800 fresh input @ $1/M + 200 cached @ $0.10/M + 500 output @ $4/M
        self.assertEqual(settled.charged_micros, 800 + 20 + 2000)
        self.assertEqual(self.w.spent_today(NOW + 1), 2820)
        self.assertEqual(self.w.reserved_today(NOW + 1), 0)

    def test_daily_cap_stops_second_reservation(self):
        small = wallet(day="0.05", month="10")
        small.register_policy(policy(day="0.05"))
        small.reserve(task("t1", issue="1", branch="b1"), now=NOW)
        with self.assertRaises(tw.BudgetExceeded):
            small.reserve(task("t2", issue="2", branch="b2"), now=NOW)
        small.close()

    def test_monthly_cap_is_independent_of_daily(self):
        small = wallet(day="10", month="0.05")
        small.register_policy(policy(day="10", month="0.05"))
        small.reserve(task("t1", issue="1", branch="b1"), now=NOW)
        with self.assertRaises(tw.BudgetExceeded):
            small.reserve(task("t2", issue="2", branch="b2"),
                          now=NOW + 3 * DAY)
        small.close()

    def test_cached_input_and_retry_and_credit_fee_are_separate(self):
        reservation = self.w.reserve(task(), now=NOW)
        usage = tw.Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000,
                         output_tokens=1000, retry_input_tokens=10_000,
                         retry_output_tokens=1000, retry_count=2,
                         credit_fee_micros=usd("0.005"))
        settled = self.w.settle(reservation.reservation_id, usage, now=NOW)
        # cached-only input costs $0.10, retries use the max token rate $4/M,
        # and the credit fee is billed separately but included in the charge.
        self.assertEqual(settled.credit_fee_micros, 5000)
        self.assertEqual(settled.retry_count, 2)
        self.assertEqual(settled.computed_micros,
                         100_000 + 40_000 + 4_000 + 4_000 + 5_000)
        row = self.w._connection.execute(
            "SELECT * FROM settlements WHERE reservation_id=?",
            (reservation.reservation_id,)).fetchone()
        self.assertEqual(row["cached_charge_micros"], 100_000)
        self.assertEqual(row["retry_charge_micros"], 44_000)
        self.assertEqual(row["input_charge_micros"], 0)

    def test_missing_usage_is_unknown_and_charges_reservation(self):
        reservation = self.w.reserve(task(), now=NOW)
        settled = self.w.settle(reservation.reservation_id, None, now=NOW)
        self.assertEqual(settled.usage_state, "unknown")
        self.assertEqual(settled.charged_micros, reservation.reserved_micros)
        self.assertEqual(self.w.spent_today(NOW), reservation.reserved_micros)
        with self.assertRaises(tw.UnknownUsage):
            # Re-settling with real usage must not silently replace the
            # provisional charge; it is an operator reconciliation path.
            self.w.settle(reservation.reservation_id, tw.Usage(1, 0, 1), now=NOW)
        self.w.complete(reservation.reservation_id, now=NOW)

    def test_settle_is_idempotent(self):
        reservation = self.w.reserve(task(), now=NOW)
        first = self.w.settle(reservation.reservation_id,
                              tw.Usage(10, 0, 10), now=NOW)
        second = self.w.settle(reservation.reservation_id,
                               tw.Usage(999, 0, 999), now=NOW + 5)
        self.assertEqual(first, second)
        self.assertEqual(self.w.spent_today(NOW), first.charged_micros)

    def test_price_snapshot_change_fails_closed(self):
        reservation = self.w.reserve(task(), now=NOW)
        with self.assertRaises(tw.PriceChanged):
            self.w.settle(reservation.reservation_id, tw.Usage(10, 0, 10),
                          now=NOW, rate=(IN * 2, OUT, CACHED))
        with self.assertRaises(tw.PriceChanged):
            self.w.settle(reservation.reservation_id, tw.Usage(10, 0, 10),
                          now=NOW, snapshot="other-endpoint")
        still = self.w.reservation_for_task("t1")
        self.assertEqual(still.status, "reserved")
        settled = self.w.settle(reservation.reservation_id,
                                tw.Usage(10, 0, 10), now=NOW,
                                rate=(IN, OUT, CACHED))
        self.assertEqual(settled.usage_state, "measured")

    def test_reservation_idempotent_for_same_task(self):
        first = self.w.reserve(task(), now=NOW)
        second = self.w.reserve(task(), now=NOW + 1)
        self.assertEqual(first.reservation_id, second.reservation_id)
        with self.assertRaises(tw.TaskConflict):
            self.w.reserve(task(max_output_tokens=1900), now=NOW + 2)

    def test_duplicate_issue_and_branch_are_refused(self):
        self.w.reserve(task("t1", issue="131", branch="b1"), now=NOW)
        with self.assertRaises(tw.DuplicateAssignment):
            self.w.reserve(task("t2", issue="131", branch="b2"), now=NOW)
        with self.assertRaises(tw.DuplicateAssignment):
            self.w.reserve(task("t3", issue="999", branch="b1"), now=NOW)

    def test_release_frees_budget_and_allows_new_attempt(self):
        reservation = self.w.reserve(task(), now=NOW)
        self.w.release(reservation.reservation_id, now=NOW, reason="pre_dispatch_failure")
        self.assertEqual(self.w.reserved_today(NOW), 0)
        retry = self.w.reserve(task(), now=NOW + 1)
        self.assertEqual(retry.attempt, 2)
        self.assertNotEqual(retry.reservation_id, reservation.reservation_id)

    def test_cap_reached_mid_flight_is_recorded_and_blocks_next(self):
        small = wallet(day="0.03", month="10")
        small.register_policy(policy(day="0.03"))
        reservation = small.reserve(task(), now=NOW)
        settled = small.settle(
            reservation.reservation_id,
            tw.Usage(10, 0, 10, reported_total_micros=usd("0.10")), now=NOW + 2)
        self.assertTrue(settled.over_budget)
        self.assertEqual(small.spent_today(NOW + 2), usd("0.10"))
        with self.assertRaises(tw.BudgetExceeded):
            small.reserve(task("t2", issue="2", branch="b2"), now=NOW + 3)
        small.close()

    def test_settled_assignment_stays_until_complete(self):
        reservation = self.w.reserve(task(), now=NOW)
        self.w.settle(reservation.reservation_id, tw.Usage(1, 0, 1), now=NOW)
        with self.assertRaises(tw.DuplicateAssignment):
            self.w.reserve(task("t2", issue="131", branch="other"), now=NOW)
        self.w.complete(reservation.reservation_id, now=NOW + 1)
        self.assertIsNone(self.w.assignment_for(issue="131"))

    def test_crash_restart_keeps_reservation_and_is_idempotent(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "wallet.sqlite3")
        first = tw.Wallet(path, policy=policy())
        reservation = first.reserve(task(), now=NOW)
        first.close()
        second = tw.Wallet(path)
        reopened = second.reservation_for_task("t1")
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened.reservation_id, reservation.reservation_id)
        same = second.reserve(task(), now=NOW + 5)
        self.assertEqual(same.reservation_id, reservation.reservation_id)
        second.settle(reservation.reservation_id, tw.Usage(1, 0, 1), now=NOW + 6)
        second.close()

    def test_recover_orphans_expired_reservation_and_keeps_budget_held(self):
        reservation = self.w.reserve(task(), now=NOW, ttl_seconds=10)
        self.assertEqual(self.w.recover(now=NOW + 20), [reservation.reservation_id])
        self.assertEqual(self.w.reservation_for_task("t1").status, "orphaned")
        self.assertEqual(self.w.reserved_today(NOW + 20),
                         reservation.reserved_micros)
        self.w.release(reservation.reservation_id, now=NOW + 21,
                       reason="provider_statement_no_charge")
        self.assertEqual(self.w.reserved_today(NOW + 21), 0)


class SubscriptionTest(unittest.TestCase):
    def test_subscription_usage_is_unmetered_and_reported_unknown(self):
        w = tw.Wallet(":memory:", policy=policy(mode=tw.SUBSCRIPTION,
                                                enforce=False))
        w.register_policy(policy(mode=tw.SUBSCRIPTION, enforce=False))
        reservation = w.reserve(task(estimated_micros=0,
                                     input_rate=0, output_rate=0,
                                     cached_rate=0), now=NOW)
        self.assertEqual(reservation.reserved_micros, 0)
        w.settle(reservation.reservation_id,
                 tw.Usage(100, 0, 100), now=NOW)
        snapshot = w.snapshot(now=NOW)
        self.assertEqual(snapshot["wallet"]["metering"], "unmetered_subscription")
        self.assertIsNone(snapshot["wallet"]["day"]["spent_usd"])
        self.assertIsNone(snapshot["wallet"]["day"]["remaining_usd"])
        w.close()

    def test_subscription_cap_enforcement_fails_closed(self):
        w = tw.Wallet(":memory:", policy=policy(mode=tw.SUBSCRIPTION,
                                                enforce=True))
        w.register_policy(policy(mode=tw.SUBSCRIPTION, enforce=True))
        with self.assertRaises(tw.UnmeasuredCap):
            w.reserve(task(estimated_micros=0, input_rate=0, output_rate=0,
                           cached_rate=0), now=NOW)
        allowed, reason = w.can_reserve(task(estimated_micros=0, input_rate=0,
                                             output_rate=0, cached_rate=0), NOW)
        self.assertFalse(allowed)
        self.assertEqual(reason, "unmeasured_cap")
        w.close()

    def test_subscription_rejects_dollar_limits(self):
        with self.assertRaises(tw.WalletError):
            tw.WalletPolicy("alice", tw.SUBSCRIPTION, usd("1"), None, False)


class CapabilityTest(unittest.TestCase):
    def setUp(self):
        self.w = wallet()
        self.w.register_policy(policy())

    def tearDown(self):
        self.w.close()

    def test_cold_start_is_unknown(self):
        cap = self.w.capability("provA", "model-a", "compiler")
        self.assertEqual(cap.state, "UNKNOWN")
        self.assertEqual(cap.samples, 0)
        self.assertFalse(cap.proven)
        self.assertIsNone(cap.cost_per_verified_unit_micros)

    def test_self_reported_evidence_cannot_be_constructed(self):
        with self.assertRaises(tw.InvalidEvidence):
            tw.Evidence("e1", "provA", "model-a", "compiler", "accepted_ci",
                        True, "verified", 10, 1, False, "provA", NOW)
        with self.assertRaises(tw.InvalidEvidence):
            tw.Evidence("e1", "provA", "model-a", "compiler", "merged_pr_count",
                        True, "verified", 10, 1, False, "ci-runner", NOW)
        with self.assertRaises(tw.InvalidEvidence):
            tw.Evidence("e1", "provA", "model-a", "compiler", "accepted_ci",
                        True, "thought_text", 10, 1, False, "ci-runner", NOW)

    def test_wilson_bounds_and_cost_per_verified_unit(self):
        record_strength(self.w, "provA", "model-a", "compiler", successes=5,
                        total=6)
        cap = self.w.capability("provA", "model-a", "compiler")
        self.assertEqual((cap.samples, cap.successes), (6, 5))
        self.assertLess(cap.success_lower, 5 / 6)
        self.assertGreater(cap.success_lower, 0.5)
        self.assertEqual(cap.cost_per_verified_unit_micros, 1200)
        self.assertTrue(cap.proven)
        self.assertEqual(cap.median_wall_seconds, 600)

    def test_duplicate_evidence_is_not_counted_twice(self):
        self.w.record_evidence(evidence("e1"))
        with self.assertRaises(tw.WalletConflict):
            self.w.record_evidence(evidence("e1"))


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.w = wallet(day="10", month="100")
        self.w.register_policy(policy(day="10", month="100"))

    def tearDown(self):
        self.w.close()

    def make(self, slots=None, attestations=None, **policy_over):
        slots = slots or [slot("a"), slot("b", provider="provB", model="model-b")]
        attestations = attestations or [attestation("a", "a" * 32),
                                        attestation("b", "b" * 32,
                                                    lease_id="lease-0002")]
        return tw.Router(self.w, slots=slots, attestations=attestations,
                         policy=tw.SchedulerPolicy(**policy_over))

    def test_identical_scores_are_fairly_distributed(self):
        router = self.make()
        order = []
        for index in range(4):
            decision = router.dispatch(now=NOW, request=route(
                f"t{index}", issue=str(index), branch=f"b{index}"))
            self.assertEqual(decision.status, "assigned")
            order.append(decision.slot_id)
            self.w.settle(decision.reservation_id, tw.Usage(1, 0, 1), now=NOW)
            self.w.complete(decision.reservation_id, now=NOW)
        self.assertEqual(order, ["a", "b", "a", "b"])
        self.assertEqual(router.snapshot(now=NOW)["slots"][0]["fairness"],
                         {"served": 2})

    def test_weighted_fair_queue_respects_slot_weight(self):
        router = self.make(slots=[slot("a", weight=3),
                                  slot("b", provider="provB", model="model-b")])
        order = []
        for index in range(4):
            decision = router.dispatch(now=NOW, request=route(
                f"t{index}", issue=str(index), branch=f"b{index}"))
            self.assertEqual(decision.status, "assigned")
            order.append(decision.slot_id)
            self.w.settle(decision.reservation_id, tw.Usage(1, 0, 1), now=NOW)
            self.w.complete(decision.reservation_id, now=NOW)
        self.assertEqual(order, ["a", "b", "a", "a"])

    def test_specialization_follows_verified_category_outcomes(self):
        record_strength(self.w, "provA", "model-a", "compiler", 6, 6)
        record_strength(self.w, "provB", "model-b", "compiler", 1, 6)
        record_strength(self.w, "provB", "model-b", "docs", 6, 6)
        record_strength(self.w, "provA", "model-a", "docs", 1, 6)
        router = self.make()
        compiler = router.plan(now=NOW, requests=[route("t1")])[0]
        docs = router.plan(now=NOW, requests=[
            route("t2", issue="2", branch="b2", category="docs")])[0]
        self.assertEqual(compiler.slot_id, "a")
        self.assertEqual(docs.slot_id, "b")
        self.assertEqual(compiler.capability_state, "KNOWN")

    def test_idle_slot_is_reported_without_assignment(self):
        router = self.make()
        decision = router.dispatch(now=NOW, request=route("t1"))
        self.assertEqual(decision.status, "assigned")
        snapshot = router.snapshot(now=NOW)
        idle = [row for row in snapshot["slots"]
                if row["assignment"] is None]
        self.assertEqual(len(idle), 1)
        self.assertEqual(idle[0]["readiness"], "ready")

    def test_weak_hardware_defaults_to_one_active_slot(self):
        same = [attestation("a", "a" * 32),
                attestation("b", "a" * 32, lease_id="lease-0002")]
        router = self.make(attestations=same, max_active_slots=2)
        decisions = router.plan(now=NOW, requests=[
            route("t1", issue="1", branch="b1"),
            route("t2", issue="2", branch="b2")])
        self.assertEqual([d.status for d in decisions],
                         ["assigned", "unmatched"])
        self.assertEqual(decisions[1].reason, "no_eligible_slot")

    def test_multi_slot_same_machine_requires_opt_in(self):
        same = [attestation("a", "a" * 32),
                attestation("b", "a" * 32, lease_id="lease-0002")]
        router = self.make(attestations=same, max_active_slots=2,
                           allow_same_machine_multi_slot=True)
        decisions = router.plan(now=NOW, requests=[
            route("t1", issue="1", branch="b1"),
            route("t2", issue="2", branch="b2")])
        self.assertEqual([d.status for d in decisions],
                         ["assigned", "assigned"])

    def test_hard_prerequisites_privacy_tools_and_binaries(self):
        private = self.make(slots=[slot("a", privacy_classes=frozenset({"public"}))],
                            attestations=[attestation("a", "a" * 32)])
        denied = private.plan(now=NOW, requests=[
            route("t1", privacy_class="restricted")])[0]
        self.assertEqual(denied.status, "unmatched")
        tools = self.make(slots=[slot("a", tools=frozenset({"gh"}))],
                          attestations=[attestation("a", "a" * 32)])
        missing = tools.plan(now=NOW, requests=[
            route("t2", required_tools=("ghidra",))])[0]
        self.assertEqual(missing.status, "unmatched")
        binaries = self.make(slots=[slot("a", binaries=frozenset({"gcc"}))],
                             attestations=[attestation("a", "a" * 32)])
        absent = binaries.plan(now=NOW, requests=[
            route("t3", required_binaries=("ida",))])[0]
        self.assertEqual(absent.status, "unmatched")

    def test_high_risk_requires_proven_evidence_or_human_approval(self):
        router = self.make()
        refused = router.plan(now=NOW, requests=[route("t1", risk="high")])[0]
        self.assertEqual(refused.status, "unmatched")
        record_strength(self.w, "provA", "model-a", "compiler", 6, 6)
        proven = router.plan(now=NOW, requests=[
            route("t2", issue="2", branch="b2", risk="high")])[0]
        self.assertEqual(proven.slot_id, "a")
        record_strength(self.w, "provB", "model-b", "compiler", 1, 6)
        approved = self.make().plan(
            now=NOW, requests=[route("t3", issue="3", branch="b3", risk="high",
                                     approval_role="owner")],
            approved_tasks={"t3"})[0]
        self.assertEqual(approved.status, "assigned")
        cold = wallet(day="10", month="100")
        cold.register_policy(policy(day="10", month="100"))
        cold_router = tw.Router(
            cold, slots=[slot("a")],
            attestations=[attestation("a", "a" * 32)])
        no_role = cold_router.plan(
            now=NOW, requests=[route("t4", issue="4", branch="b4", risk="high")],
            approved_tasks={"t4"})[0]
        self.assertEqual(no_role.status, "unmatched")
        cold.close()

    def test_stale_or_unlocked_attestation_is_not_eligible(self):
        stale = self.make(attestations=[
            attestation("a", "a" * 32, attested_at=NOW - 500)])
        self.assertEqual(stale.plan(now=NOW, requests=[route("t1")])[0].status,
                         "unmatched")
        unlocked = self.make(attestations=[
            attestation("a", "a" * 32, process_locked=False)])
        self.assertEqual(unlocked.plan(now=NOW,
                                       requests=[route("t2", issue="2", branch="b2")])[0].status,
                         "unmatched")
        expired = self.make(attestations=[
            attestation("a", "a" * 32, lease_expires_at=NOW - 1)])
        self.assertEqual(expired.plan(now=NOW,
                                      requests=[route("t3", issue="3", branch="b3")])[0].status,
                         "unmatched")

    def test_price_snapshot_mismatch_is_never_silently_substituted(self):
        router = self.make()
        decision = router.plan(now=NOW, requests=[
            route("t1", snapshot="other-endpoint")])[0]
        self.assertEqual(decision.status, "unmatched")
        self.assertEqual(decision.reason, "no_eligible_slot")

    def test_cheaper_canary_only_for_low_risk(self):
        cheap = slot("b", provider="provB", model="model-b",
                     input_rate=100_000, output_rate=100_000)
        record_strength(self.w, "provA", "model-a", "compiler", 6, 6)
        router = self.make(slots=[slot("a"), cheap])
        normal = router.plan(now=NOW, requests=[
            route("t1", risk="low")])[0]
        self.assertEqual(normal.slot_id, "a")
        canary = router.plan(now=NOW, requests=[
            route("t2", issue="2", branch="b2", risk="low",
                  canary=True)])[0]
        self.assertEqual(canary.slot_id, "b")
        denied = router.plan(now=NOW, requests=[
            route("t3", issue="3", branch="b3", risk="high",
                  canary=True)])[0]
        self.assertEqual(denied.status, "refused")
        self.assertEqual(denied.reason, "canary_high_risk_denied")

    def test_starvation_breaks_fairness_for_waiting_task(self):
        record_strength(self.w, "provA", "model-a", "compiler", 5, 6)
        record_strength(self.w, "provB", "model-b", "compiler", 4, 6)
        router = self.make(strength_tolerance=0.1)
        router.served["a"] = 5
        normal = router.plan(now=NOW, requests=[route("t1")])[0]
        self.assertEqual(normal.slot_id, "b")
        starved = router.plan(now=NOW, requests=[
            route("t2", issue="2", branch="b2",
                  enqueued_at=NOW - 10_000)])[0]
        self.assertEqual(starved.slot_id, "a")

    def test_no_mid_checkout_reprioritization(self):
        router = self.make()
        first = router.dispatch(now=NOW, request=route("t1", priority=1))
        self.assertEqual(first.status, "assigned")
        before = self.w.assignment_for(issue="131")
        second = router.dispatch(now=NOW, request=route(
            "t2", issue="131", branch="nova/issue-131-hotfix", priority=999))
        self.assertEqual(second.status, "refused")
        self.assertEqual(second.reason, "duplicate_issue")
        self.assertEqual(self.w.assignment_for(issue="131"), before)
        # The chosen slot is pinned and cannot be stolen by a new task.
        other = router.plan(now=NOW, requests=[
            route("t3", issue="3", branch="b3", priority=999)])[0]
        self.assertNotEqual(other.slot_id, first.slot_id)

    def test_duplicate_assignment_is_refused_at_dispatch(self):
        router = self.make()
        self.assertEqual(router.dispatch(now=NOW,
                                         request=route("t1")).status, "assigned")
        with self.assertRaises(tw.DuplicateAssignment):
            self.w.reserve(task("t9", issue="131", branch="b9"), now=NOW)

    def test_explicit_failover_only(self):
        router = self.make()
        no_failover = router.plan(now=NOW, requests=[
            route("t1", preferred_slot="missing")])[0]
        self.assertEqual(no_failover.reason, "preferred_unavailable")
        failover = router.plan(now=NOW, requests=[
            route("t2", issue="2", branch="b2", preferred_slot="missing",
                  allow_failover=True)])[0]
        self.assertEqual(failover.status, "assigned")

    def test_budget_blocks_router_before_dispatch(self):
        small = wallet(day="0.02", month="10")
        small.register_policy(policy(day="0.02"))
        router = tw.Router(small, slots=[slot("a")],
                           attestations=[attestation("a", "a" * 32)])
        decision = router.plan(now=NOW, requests=[route("t1")])[0]
        self.assertEqual(decision.status, "unmatched")
        self.assertEqual(decision.reason, "budget_cap")
        small.close()

    def test_dispatch_reserves_exactly_once(self):
        router = self.make()
        first = router.dispatch(now=NOW, request=route("t1"))
        self.assertEqual(first.status, "assigned")
        self.assertIsNotNone(first.reservation_id)
        again = router.dispatch(now=NOW, request=route("t1"))
        self.assertEqual(again.status, "refused")
        self.assertEqual(again.reason, "already_in_flight")
        self.assertEqual(self.w.reserved_today(NOW),
                         task().reserved_ceiling())


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.w = wallet()
        self.w.register_policy(policy())
        self.router = tw.Router(
            self.w, slots=[slot("a")],
            attestations=[attestation("a", "a" * 32)])

    def tearDown(self):
        self.w.close()

    def test_snapshot_is_machine_readable_and_sanitized(self):
        self.router.dispatch(now=NOW, request=route("t1"))
        snapshot = self.router.snapshot(now=NOW)
        text = json.dumps(snapshot, sort_keys=True)
        self.assertEqual(snapshot["schema"], tw.SNAPSHOT_SCHEMA)
        self.assertNotIn("/home", text)
        self.assertNotIn("sk-", text)
        self.assertNotIn("api_key", text)
        self.assertEqual(snapshot["wallet"]["day"]["spent_usd"], "0.000000")
        self.assertEqual(snapshot["slots"][0]["readiness"], "ready")
        self.assertEqual(snapshot["slots"][0]["assignment"]["task_id"], "t1")
        self.assertIn("cost_per_verified_unit_usd", snapshot["slots"][0])
        self.assertIn("eta_confidence", snapshot["slots"][0])
        self.assertEqual(snapshot["slots"][0]["proof_level"], "unknown")
        self.assertEqual(snapshot["slots"][0]["eta_confidence"], "unknown")

    def test_snapshot_rejects_private_paths_and_secret_keys(self):
        with self.assertRaises(tw.WalletError):
            tw.assert_sanitized({"slots": [{"note": "/home/jason/key"}]})
        with self.assertRaises(tw.WalletError):
            tw.assert_sanitized({"api_key": "synthetic"})
        with self.assertRaises(tw.WalletError):
            tw.assert_sanitized({"slots": [{"prompt": "synthetic"}]})

    def test_eta_confidence_and_cost_per_verified_unit(self):
        for index in range(5):
            self.w.record_evidence(evidence(
                f"eta-{index}", wall=600 + index, billed=1000))
        self.router.dispatch(now=NOW, request=route("t1"))
        row = self.router.snapshot(now=NOW)["slots"][0]
        self.assertEqual(row["proof_level"], "verified")
        self.assertEqual(row["eta_confidence"], "high")
        self.assertEqual(row["cost_per_verified_unit_usd"], "0.001000")


if __name__ == "__main__":
    unittest.main()
