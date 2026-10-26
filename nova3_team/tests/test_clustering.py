"""Reproducible synthetic metadata fixtures; no game binaries or asset data."""
import contextlib
import copy
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import clustering as c


BINARY = "a" * 64
OTHER = "b" * 64
PRIVATE = "SYNTHETIC_PRIVATE_PAYLOAD"


def ident(address, binary=BINARY):
    return f"{binary}:{address:08x}"


def row(address, *, binary=BINARY, deps=(), **extra):
    return {"binary_sha256": binary, "address": address, "size": 16, "thumb": True,
            "depends_on": list(deps), "instruction_count": 10,
            "cyclomatic_complexity": 2, "globals": [], **extra}


def ledger(*rows):
    return {"schema_version": 1, "functions": list(rows)}


def clusters(*rows):
    return c.cluster_functions(ledger(*rows))["clusters"]


def ghidra(address=0x1000, *, calls=(), **extra):
    # Matches the field/address convention in tools/ghidra/export_batch.py.
    return {"binary_sha256": BINARY, "architecture": "ARM:LE:32:v7",
            "address": address, "ghidra_image_base": 0x10000,
            "ghidra_address": address + 0x10000, "byte_mapping_validated": True,
            "symbol_size": 16, "thumb": True,
            "calls": [{"from": "00011004", "to": target, "type": "UNCONDITIONAL_CALL"}
                      for target in calls],
            "instructions": [{"text": "synthetic instruction"}] * 4,
            "basic_blocks": [{"start": "00011000", "end": "0001100f", "flow": "RETURN"}],
            "references": [{"from": "00011008", "to": "00018000", "type": "READ"}],
            "symbol": PRIVATE, "c": PRIVATE, **extra}


def make_sqlite(path):
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE functions(id TEXT PRIMARY KEY, binary_hash TEXT,
                address INTEGER, size INTEGER, thumb INTEGER, name TEXT);
            CREATE TABLE edges(source_id TEXT, target_address INTEGER,
                kind TEXT, evidence TEXT);
        """)
        db.executemany("INSERT INTO functions VALUES(?,?,?,?,?,?)", [
            ("alias-a", BINARY, 0x1000, 16, 1, PRIVATE),
            ("alias-b", BINARY, 0x1000, 16, 1, PRIVATE),
            ("leaf", BINARY, 0x2000, 16, 0, PRIVATE),
            ("other", OTHER, 0x2000, 0, 0, PRIVATE)])
        db.executemany("INSERT INTO edges VALUES(?,?,?,?)", [
            ("alias-b", 0x2000, "CALL", PRIVATE),
            ("alias-a", 0x3000, "CALL", PRIVATE),
            ("leaf", 0x4000, "READ", PRIVATE)])


class ComponentsTest(unittest.TestCase):
    def test_empty_and_exact_singleton_score(self):
        self.assertEqual(c.cluster_functions(ledger())["clusters"], [])
        result = clusters(row(0x1000))[0]
        self.assertEqual(result["function_ids"], [ident(0x1000)])
        self.assertEqual(result["effort_points"], 18)  # 10 instructions + 8 * (2-1)
        self.assertTrue(result["effort_complete"])
        self.assertEqual(result["shared_global_links"], 0)

    def test_weak_components_include_cycles_recursion_and_isolates(self):
        result = clusters(row(0x1000, deps=[ident(0x2000)]),
                          row(0x2000, deps=[ident(0x1000), ident(0x2000)]),
                          row(0x3000, deps=[ident(0x2000)]), row(0x4000))
        self.assertEqual([r["function_count"] for r in result], [3, 1])
        self.assertEqual(result[0]["internal_dependency_count"], 4)

    def test_shared_globals_merge_transitively_and_count_distinct_users(self):
        result = clusters(row(0x1000, globals=["g", "g"]),
                          row(0x2000, globals=["g", "h"]),
                          row(0x3000, globals=["h", "unique"]),
                          row(0x4000, deps=[ident(0x3000)]))[0]
        self.assertEqual(result["function_count"], 4)
        self.assertEqual(result["shared_global_count"], 2)
        self.assertEqual(result["shared_global_links"], 2)
        self.assertEqual(result["effort_points"], 4 * 18 + 2 * 4)

    def test_binary_identity_isolates_equal_addresses_and_global_names(self):
        a, b = row(0x1000, globals=["g"]), row(0x1000, binary=OTHER, globals=["g"])
        self.assertEqual(len(clusters(a, b)), 2)
        a["depends_on"] = [ident(0x1000, OTHER)]  # Only explicit cross-binary edges join.
        self.assertEqual(len(clusters(a, b)), 1)

    def test_unknown_callee_does_not_connect_unrelated_callers(self):
        result = clusters(row(0x1000, deps=[ident(0x9000)]),
                          row(0x2000, deps=[ident(0x9000)]))
        self.assertEqual(len(result), 2)
        for component in result:
            self.assertEqual(component["internal_dependency_count"], 0)
            self.assertEqual(component["unresolved_dependencies"], [ident(0x9000)])

    def test_aliases_merge_metrics_refs_without_double_scoring_or_mutation(self):
        a = row(0x1000, instruction_count=None, globals=None)
        b = row(0x1000, deps=[ident(0x2000), ident(0x2000)], globals=["g"])
        d = row(0x2000, globals=["g"])
        source = ledger(a, b, d)
        before = copy.deepcopy(source)
        result = c.cluster_functions(source)
        self.assertEqual(source, before)
        self.assertEqual(result["function_count"], 2)
        self.assertEqual(result["clusters"][0]["effort_points"], 40)
        self.assertEqual(result["clusters"][0]["internal_dependency_count"], 1)

    def test_missing_metrics_never_get_a_complete_effort_score(self):
        for field, subtotal in (("instruction_count", 8), ("cyclomatic_complexity", 10),
                                ("globals", 18)):
            for omitted in (False, True):
                with self.subTest(field=field, omitted=omitted):
                    a = row(0x1000, **{field: None})
                    if omitted:
                        a.pop(field)
                    result = clusters(a)[0]
                    self.assertIsNone(result["effort_points"])
                    self.assertFalse(result["effort_complete"])
                    self.assertEqual(result["known_effort_points"], subtotal)
                    self.assertEqual(result["missing_metrics"][field], 1)
        result = clusters({"binary_sha256": BINARY, "address": 0x1000})[0]
        self.assertEqual(result["known_effort_points"], 0)
        self.assertEqual(sum(result["missing_metrics"].values()), 3)

    def test_score_model_zero_and_monotonic_weights(self):
        base = row(0x1000, instruction_count=0, cyclomatic_complexity=1)
        self.assertEqual(clusters(base)[0]["effort_points"], 0)
        self.assertEqual(clusters({**base, "instruction_count": 5})[0]["effort_points"], 5)
        self.assertEqual(clusters({**base, "cyclomatic_complexity": 4})[0]["effort_points"], 24)

    def test_permutations_and_alias_order_are_deterministic(self):
        rows = [row(0x1000, globals=["g"]), row(0x2000, deps=[ident(0x1000)]),
                row(0x2000, globals=["g"]), row(0x3000)]
        expected = c.cluster_functions(ledger(*rows))
        for permutation in itertools.permutations(rows):
            self.assertEqual(c.cluster_functions(ledger(*permutation)), expected)
        old_id = clusters(row(0x1000))[0]["cluster_id"]
        self.assertEqual(clusters(row(0x1000, instruction_count=50))[0]["cluster_id"], old_id)
        self.assertNotEqual(clusters(*rows)[0]["cluster_id"], old_id)

    def test_components_agree_with_independent_graph_traversal(self):
        rng = random.Random(9841)
        for _ in range(50):
            addresses = list(range(0x1000, 0x1020, 2))
            graph = {a: set() for a in addresses}
            rows = []
            for address in addresses:
                deps = rng.sample(addresses, rng.randrange(5))
                rows.append(row(address, deps=[ident(d) for d in deps]))
                for dep in deps:
                    graph[address].add(dep)
                    graph[dep].add(address)
            expected, unseen = [], set(addresses)
            while unseen:
                seen, pending = set(), [min(unseen)]
                while pending:
                    address = pending.pop()
                    if address not in seen:
                        seen.add(address)
                        pending.extend(graph[address] - seen)
                unseen -= seen
                expected.append(sorted(ident(a) for a in seen))
            self.assertEqual([r["function_ids"] for r in clusters(*rows)], sorted(expected))

    def test_large_chain_and_global_hub_do_not_require_recursion_or_pair_expansion(self):
        n = 5000
        chain = [row(2*i, deps=[ident(2*(i-1))] if i else []) for i in range(n)]
        self.assertEqual(clusters(*chain)[0]["internal_dependency_count"], n-1)
        hub = [row(2*i, globals=["one-hub"]) for i in range(n)]
        result = clusters(*hub)[0]
        self.assertEqual(result["shared_global_links"], n-1)
        self.assertEqual(result["effort_points"], n*18 + 4*(n-1))


class ValidationTest(unittest.TestCase):
    def test_bad_schema_records_and_references_fail_closed(self):
        bad = [None, {}, {"schema_version": True, "functions": []}, ledger(None),
               {"schema_version": 1, "functions": {}}, ledger(row(1)),
               ledger(row(2**32)), ledger(row(-2)), ledger(row(True)),
               ledger(row(0x1000, binary="bad")),
               ledger(row(0x1000, depends_on="bad")),
               ledger(row(0x1000, depends_on=[PRIVATE])),
               ledger(row(0x1000, depends_on=[ident(0x1001)])),
               ledger(row(0x1000, globals="bad")),
               ledger(row(0x1000, globals=[""])),
               ledger(row(0x1000, globals=[{}])),
               ledger(row(0x1000, globals=["x"*257])),
               ledger(row(0x1000, thumb=1)), ledger(row(0xFFFFFFFE, size=16))]
        for source in bad:
            with self.subTest(source=source), self.assertRaises(c.ClusteringError) as caught:
                c.cluster_functions(source)
            self.assertNotIn(PRIVATE, str(caught.exception))

    def test_metrics_and_alias_conflicts_are_rejected(self):
        for field in ("instruction_count", "cyclomatic_complexity", "size"):
            for value in (True, -1, 1.5, "2", float("inf"), c.MAX_METRIC+1):
                with self.subTest(field=field, value=value), self.assertRaises(c.ClusteringError):
                    clusters(row(0x1000, **{field: value}))
        with self.assertRaises(c.ClusteringError):
            clusters(row(0x1000, cyclomatic_complexity=0))
        for field, value in (("instruction_count", 11), ("cyclomatic_complexity", 3),
                             ("size", 18), ("thumb", False)):
            with self.subTest(field=field), self.assertRaises(c.ClusteringError):
                clusters(row(0x1000), row(0x1000, **{field: value}))

    def test_function_and_aggregate_reference_limits(self):
        with mock.patch.object(c, "MAX_FUNCTIONS", 1), self.assertRaises(c.ClusteringError):
            clusters(row(0x1000), row(0x2000))
        with mock.patch.object(c, "MAX_LINKS", 1), self.assertRaises(c.ClusteringError):
            clusters(row(0x1000, globals=["a"]), row(0x2000, globals=["b"]))


class AdapterTest(unittest.TestCase):
    def test_ghidra_rebases_calls_clears_thumb_tags_and_drops_payloads(self):
        source = [ghidra(calls=["00012001", "0x00013000"]), ghidra(0x2000)]
        before = copy.deepcopy(source)
        result = c.ledger_from_ghidra(source)
        self.assertEqual(source, before)
        self.assertEqual(result["functions"][0]["depends_on"], [ident(0x2000), ident(0x3000)])
        self.assertEqual(result["functions"][0]["instruction_count"], 4)
        self.assertNotIn(PRIVATE, json.dumps(result))
        component = c.cluster_functions(result)["clusters"][0]
        self.assertEqual(component["known_instruction_count"], 8)
        self.assertEqual(component["unresolved_dependencies"], [ident(0x3000)])
        self.assertEqual(component["missing_metrics"],
                         {"instruction_count": 0, "cyclomatic_complexity": 2, "globals": 2})
        self.assertIsNone(component["effort_points"])
        # One block and an arbitrary READ ref must not become complexity/global evidence.
        self.assertEqual(component["known_excess_complexity"], 0)
        self.assertEqual(component["shared_global_count"], 0)

    def test_ghidra_only_uses_explicit_optional_metrics(self):
        source = ghidra(cyclomatic_complexity=3, globals=[], instruction_count=4)
        self.assertEqual(c.cluster_functions(c.ledger_from_ghidra(source))["clusters"][0]
                         ["effort_points"], 20)
        result = c.ledger_from_ghidra(ghidra(instructions=None))
        self.assertIsNone(result["functions"][0]["instruction_count"])

    def test_ghidra_invalid_mapping_addresses_counts_and_architecture_fail(self):
        changes = [{"byte_mapping_validated": False}, {"ghidra_image_base": None},
                   {"ghidra_image_base": -1}, {"ghidra_address": 0x1000},
                   {"ghidra_address": True}, {"architecture": "x86:LE:32:default"},
                   {"instruction_count": 5}, {"instruction_count": True},
                   {"instructions": {}}, {"calls": [None]},
                   {"calls": [{"to": "EXTERNAL:00012000"}]},
                   {"calls": [{"to": "00000002"}]}, {"calls": [{"to": PRIVATE}]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(c.ClusteringError) as caught:
                c.ledger_from_ghidra(ghidra(**change))
            self.assertNotIn(PRIVATE, str(caught.exception))

    def test_sqlite_actual_inventory_schema_readonly_aliases_and_unresolved_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index ? #.sqlite"
            make_sqlite(path)
            before = path.read_bytes()
            result = c.load_sqlite_index(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), [path.name])
            self.assertNotIn(PRIVATE, json.dumps(result))
            report = c.cluster_functions(result)
            self.assertEqual(report["function_count"], 3)
            self.assertEqual(report["cluster_count"], 2)
            component = report["clusters"][0]
            self.assertEqual(component["function_count"], 2)
            self.assertEqual(component["unresolved_dependencies"], [ident(0x3000)])
            self.assertIsNone(component["effort_points"])

    def test_sqlite_missing_database_bad_schema_orphan_and_noncanonical_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.sqlite"
            with self.assertRaises(c.ClusteringError):
                c.load_sqlite_index(path)
            self.assertFalse(path.exists())
            path.write_bytes(b"synthetic malformed SQLite")
            with self.assertRaises(c.ClusteringError):
                c.load_sqlite_index(path)
        for source, target in (("absent", 0x2000), ("leaf", 0x2001)):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "index.sqlite"
                make_sqlite(path)
                with contextlib.closing(sqlite3.connect(path)) as db, db:
                    db.execute("INSERT INTO edges VALUES(?,?,?,?)", (source, target, "CALL", PRIVATE))
                with self.assertRaises(c.ClusteringError):
                    c.load_sqlite_index(path)


class CliTest(unittest.TestCase):
    def test_report_allowlist_hashseed_reproducibility_and_digest_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            source = ledger(row(0x1000, globals=[PRIVATE], c=PRIVATE, name=PRIVATE),
                            row(0x2000, globals=[PRIVATE], instructions=[PRIVATE]))
            raw = json.dumps(source).encode()
            path.write_bytes(raw)
            args = [str(path), "--index-sha256", hashlib.sha256(raw).hexdigest()]
            outputs = []
            for seed in ("1", "81"):
                run = subprocess.run([sys.executable, "-m", "nova3_team.clustering", *args],
                                     env={**os.environ, "PYTHONHASHSEED": seed,
                                          "PYTHONDONTWRITEBYTECODE": "1"},
                                     capture_output=True, text=True, check=False)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stderr, "")
                self.assertNotIn(PRIVATE, run.stdout)
                outputs.append(run.stdout)
            self.assertEqual(outputs[0], outputs[1])
            report = json.loads(outputs[0])
            self.assertEqual(report["clusters"][0]["effort_points"], 40)
            self.assertEqual(report["matching_claim"], "NOT MATCHED")
            for bad_args in ([str(path)], [str(path), "--index-sha256", "0"*64]):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(c.main(bad_args), 2)
                self.assertEqual(out.getvalue(), "")
                self.assertNotIn(PRIVATE, err.getvalue())

    def test_cli_ghidra_and_sqlite_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exports.json"
            raw = json.dumps(ghidra()).encode()
            path.write_bytes(raw)
            db_path = Path(tmp) / "index.sqlite"
            make_sqlite(db_path)
            for args in ([str(path), "--format", "ghidra", "--index-sha256",
                          hashlib.sha256(raw).hexdigest()],
                         [str(db_path), "--format", "sqlite"]):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(c.main(args), 0)
                self.assertEqual(err.getvalue(), "")
                self.assertNotIn(PRIVATE, out.getvalue())
                self.assertGreater(json.loads(out.getvalue())["function_count"], 0)


if __name__ == "__main__":
    unittest.main()
