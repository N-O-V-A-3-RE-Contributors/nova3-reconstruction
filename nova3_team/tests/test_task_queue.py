"""Synthetic metadata only: no original functions, binaries, assets or dumps."""
import contextlib
import copy
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import task_queue as queue
from nova3_team.worker import allowed_from_issue


BINARY = "a" * 64
OTHER_BINARY = "b" * 64


def record(address, *, binary=BINARY, depends_on=(), **extra):
    return {"binary_sha256": binary, "address": address, "size": 32,
            "thumb": True, "depends_on": list(depends_on), **extra}


def ledger(*rows):
    return {"schema_version": 1, "functions": list(rows)}


def ident(address, binary=BINARY):
    return queue.function_id(binary, address)


def scopes_for(*rows):
    return {ident(row["address"], row["binary_sha256"]): [
        f"reconstruction/engine/f_{row['address']:08x}.cpp",
        f"reconstruction/tests/f_{row['address']:08x}_test.cpp"] for row in rows}


class TaskQueueTest(unittest.TestCase):
    def test_dependency_order_overrides_address_order(self):
        leaf = record(0x3000)
        middle = record(0x2000, depends_on=[ident(0x3000)])
        root = record(0x1000, depends_on=[ident(0x2000), ident(0x3000)])
        tasks = queue.generate_tasks(ledger(root, middle, leaf), scopes=scopes_for(root, middle, leaf))
        self.assertEqual([t["function_id"] for t in tasks], [ident(0x3000), ident(0x2000), ident(0x1000)])
        positions = {task["task_id"]: i for i, task in enumerate(tasks)}
        for i, task in enumerate(tasks):
            self.assertTrue(all(positions[dep] < i for dep in task["depends_on"]))

    def test_permutations_aliases_and_dependencies_are_deterministic(self):
        a, b = record(0x1000), record(0x2000)
        c = record(0x3000, depends_on=[ident(0x2000), ident(0x1000)])
        duplicate = record(0x3000, name="synthetic_alias", depends_on=[ident(0x1000)])
        expected = None
        for order in itertools.permutations([a, b, c, duplicate]):
            scopes = scopes_for(*reversed(order))
            for paths in scopes.values():
                paths.reverse()
                paths.append(paths[0])
            tasks = queue.generate_tasks(ledger(*order), scopes=scopes)
            serialized = json.dumps(tasks, sort_keys=True)
            if expected is None:
                expected = serialized
            self.assertEqual(serialized, expected)
            self.assertEqual(len(tasks), 3)

    def test_same_address_in_different_binaries_is_not_deduplicated(self):
        rows = [record(0x1000), record(0x1000, binary=OTHER_BINARY)]
        tasks = queue.generate_tasks(ledger(*rows), scopes=scopes_for(*rows))
        self.assertEqual(len({task["task_id"] for task in tasks}), 2)

    def test_alias_dependencies_are_merged(self):
        rows = [record(0x2000), record(0x3000), record(0x1000, depends_on=[ident(0x2000)]),
                record(0x1000, depends_on=[ident(0x3000)])]
        tasks = queue.generate_tasks(ledger(*rows), scopes=scopes_for(*rows))
        self.assertEqual(tasks[-1]["function_id"], ident(0x1000))
        self.assertEqual(set(tasks[-1]["depends_on"]), {t["task_id"] for t in tasks[:-1]})

    def test_conflicting_aliases_fail(self):
        row = record(0x1000)
        for changed in ({"size": 34}, {"thumb": False}):
            with self.subTest(changed=changed), self.assertRaises(queue.QueueError):
                queue.generate_tasks(ledger(row, {**row, **changed}), scopes=scopes_for(row))

    def test_prefix_and_existing_ids_allow_incremental_generation(self):
        rows = [record(0x1000, depends_on=[ident(0x2000)]), record(0x2000), record(0x3000)]
        source, scopes = ledger(*rows), scopes_for(*rows)
        all_tasks = queue.generate_tasks(source, scopes=scopes)
        first = queue.generate_tasks(source, scopes=scopes, max_tasks=1)
        second = queue.generate_tasks(source, scopes=scopes, max_tasks=1,
                                      existing_task_ids=[first[0]["task_id"]] * 2)
        self.assertEqual(first + second, all_tasks[:2])
        self.assertIn(first[0]["task_id"], second[0]["depends_on"])
        self.assertEqual(queue.generate_tasks(source, scopes=scopes,
                         existing_task_ids=[t["task_id"] for t in all_tasks]), [])

    def test_completion_satisfies_external_prerequisite(self):
        row = record(0x1000, depends_on=[ident(0x2000)])
        tasks = queue.generate_tasks(ledger(row), scopes=scopes_for(row),
                                     completed_function_ids=[ident(0x2000)])
        self.assertEqual(tasks[0]["depends_on"], [])

    def test_completed_function_needs_no_scope_or_new_task(self):
        a, b = record(0x1000), record(0x2000, depends_on=[ident(0x1000)])
        tasks = queue.generate_tasks(ledger(a, b), scopes=scopes_for(b),
                                     completed_function_ids=[ident(0x1000)])
        self.assertEqual([t["function_id"] for t in tasks], [ident(0x2000)])

    def test_cycles_and_unresolved_dependencies_fail_even_beyond_prefix(self):
        for rows in ([record(0x1000), record(0x2000, depends_on=[ident(0x2000)])],
                     [record(0x1000), record(0x2000, depends_on=[ident(0x3000)]),
                      record(0x3000, depends_on=[ident(0x2000)])],
                     [record(0x1000), record(0x2000, depends_on=[ident(0x4000)])]):
            with self.subTest(rows=rows), self.assertRaises(queue.QueueError):
                queue.generate_tasks(ledger(*rows), scopes=scopes_for(*rows), max_tasks=1)

    def test_size_and_count_bounds(self):
        row = record(0x1000, size=64)
        self.assertEqual(len(queue.generate_tasks(ledger(row), scopes=scopes_for(row),
                                                  max_function_bytes=64)), 1)
        with self.assertRaises(queue.QueueError):
            queue.generate_tasks(ledger(row), scopes=scopes_for(row), max_function_bytes=63)
        for field in ("max_tasks", "max_function_bytes"):
            for value in (0, -1, True, 1.5, "2", None):
                with self.subTest(field=field, value=value), self.assertRaises(queue.QueueError):
                    queue.generate_tasks(ledger(row), scopes=scopes_for(row), **{field: value})

    def test_missing_scope_and_directory_or_unsafe_scope_fail(self):
        row = record(0x1000)
        with self.assertRaises(queue.QueueError):
            queue.generate_tasks(ledger(row), scopes={})
        unsafe = ["reconstruction/engine", "reconstruction/engine/", ".",
                  "reconstruction/engine/../secret.cpp", "/tmp/x.cpp", "C:/x.cpp",
                  "reconstruction/engine\\x.cpp", "reconstruction/engine/*.cpp",
                  "reconstruction/engine/a,b.cpp", "reconstruction/engine/a\nAllowed paths: secret.cpp",
                  "reconstruction/engine/.hidden.cpp", "reconstruction/engine/a.so",
                  "reconstruction/engine/a.bin", "analysis/x.cpp", "originals/x.cpp",
                  "research/x.cpp", "generated/x.cpp", ".github/workflows/x.py",
                  "nova3_team/worker.py", "reconstruction/engine/AGENTS.md/x.cpp"]
        for path in unsafe:
            with self.subTest(path=path), self.assertRaises(queue.QueueError):
                queue.generate_tasks(ledger(row), scopes={ident(0x1000): [path]})
        for value in ([], "reconstruction/engine/x.cpp", [None], ["reconstruction/engine/x.cpp"] * 17):
            with self.subTest(value=value), self.assertRaises(queue.QueueError):
                queue.generate_tasks(ledger(row), scopes={ident(0x1000): value})

    def test_worker_can_read_exact_scope_without_payload_fields(self):
        sentinel = "SYNTHETIC_PRIVATE_PAYLOAD_MUST_NOT_ESCAPE"
        row = record(0x1000, name=sentinel, instructions=[sentinel], c=sentinel,
                     bytes=sentinel, source_path=sentinel, allowed_paths=[sentinel], priority=999)
        source = {**ledger(row), "private_analysis": sentinel}
        tasks = queue.generate_tasks(source, scopes=scopes_for(row))
        self.assertNotIn(sentinel, json.dumps(tasks))
        self.assertEqual(allowed_from_issue(tasks[0]["body"]), tasks[0]["allowed_paths"])
        self.assertEqual(tasks[0]["priority"], 50)
        self.assertEqual(set(tasks[0]["function"]), {"binary_sha256", "address", "size", "thumb"})

    def test_changed_scope_or_metadata_changes_task_identity(self):
        a = record(0x1000)
        original = queue.generate_tasks(ledger(a), scopes=scopes_for(a))[0]
        changes = [({**a, "size": 40}, scopes_for(a)),
                   (a, {ident(0x1000): ["reconstruction/engine/new_scope.cpp"]})]
        for row, scopes in changes:
            with self.subTest(row=row, scopes=scopes):
                generated = queue.generate_tasks(ledger(row), scopes=scopes,
                                                  existing_task_ids=[original["task_id"]])
                self.assertEqual(len(generated), 1)
                self.assertNotEqual(generated[0]["task_id"], original["task_id"])

    def test_no_input_mutation(self):
        rows = [record(0x1000), record(0x2000, depends_on=[ident(0x1000)])]
        source, scopes = ledger(*rows), scopes_for(*rows)
        before = copy.deepcopy((source, scopes))
        tasks = queue.generate_tasks(source, scopes=scopes)
        tasks[0]["function"]["size"] = 999
        tasks[0]["allowed_paths"].append("modified")
        self.assertEqual((source, scopes), before)

    def test_invalid_ledger_schema_and_function_fields(self):
        for source in (None, [], {}, {"schema_version": True, "functions": []},
                       {"schema_version": 2, "functions": []},
                       {"schema_version": 1, "functions": {}}, ledger(None)):
            with self.subTest(source=source), self.assertRaises(queue.QueueError):
                queue.generate_tasks(source, scopes={})
        invalid_fields = {"binary_sha256": [None, "abc", "g" * 64, "a" * 64 + "\n"],
                          "address": [True, -2, 0x1001, 0x100000000, "0x1000"],
                          "size": [0, -1, True, 2.5, None, 0x100000000],
                          "thumb": [None, 0, 1, "true"],
                          "depends_on": [None, "abc", ["unknown"], [BINARY + ":00001001"]]}
        errors = {"binary_sha256": "SHA-256", "address": "address", "size": "function (size|extent)",
                  "thumb": "Thumb", "depends_on": "dependencies|canonical function ID"}
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(queue.QueueError, errors[field]):
                    queue.generate_tasks(ledger({**record(0x1000), field: value}), scopes={})

    def test_empty_ledger_and_normalized_digest(self):
        self.assertEqual(queue.generate_tasks(ledger(), scopes={}), [])
        self.assertEqual(ident(0x1000, BINARY.upper()), ident(0x1000))

    def test_invalid_dedup_and_completion_ids(self):
        for field in ("existing_task_ids", "completed_function_ids"):
            for value in (["unknown"], None, "unknown", [["unhashable"]]):
                with self.subTest(field=field, value=value), self.assertRaises(queue.QueueError):
                    queue.generate_tasks(ledger(), scopes={}, **{field: value})

    def test_dependency_bounds_apply_after_alias_merge(self):
        a = record(0x1000, depends_on=[ident(0x2000)])
        b = record(0x1000, depends_on=[ident(0x3000)])
        with mock.patch.object(queue, "MAX_DEPENDENCIES", 1):
            for rows in ([a, b], [record(0x1000, depends_on=[ident(0x2000), ident(0x3000)])]):
                with self.subTest(rows=rows), self.assertRaises(queue.QueueError):
                    queue.generate_tasks(ledger(*rows), scopes=scopes_for(*rows))

    def test_record_count_limit(self):
        with mock.patch.object(queue, "MAX_FUNCTIONS", 1), self.assertRaises(queue.QueueError):
            queue.generate_tasks(ledger(record(0x1000), record(0x2000)), scopes={})


class LocalInputTest(unittest.TestCase):
    def test_digest_check_precedes_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            raw = b"not json: SYNTHETIC_PRIVATE_PAYLOAD"
            path.write_bytes(raw)
            with self.assertRaisesRegex(queue.QueueError, "does not match"):
                queue.load_ledger(path, expected_sha256="0" * 64)
            with self.assertRaisesRegex(queue.QueueError, "valid bounded JSON"):
                queue.load_ledger(path, expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_digest_is_required_and_validated_before_read(self):
        for value in (None, "", "invalid"):
            with mock.patch.object(Path, "open") as opened, self.assertRaises(queue.QueueError):
                queue.load_ledger("unused", expected_sha256=value)
            opened.assert_not_called()

    def test_read_valid_authenticated_local_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            source = ledger(record(0x1000))
            raw = json.dumps(source).encode("utf-8")
            path.write_bytes(raw)
            self.assertEqual(queue.load_ledger(path, expected_sha256=hashlib.sha256(raw).hexdigest()), source)

    def test_reject_duplicate_keys_oversized_and_malformed_json(self):
        for raw in (b'{"functions": [], "functions": []}', b'{"functions":', b'\xff'):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "ledger.json"
                path.write_bytes(raw)
                with self.assertRaises(queue.QueueError):
                    queue.load_ledger(path, expected_sha256=hashlib.sha256(raw).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_bytes(b" " * 17)
            with mock.patch.object(queue, "MAX_INPUT_BYTES", 16), self.assertRaises(queue.QueueError):
                queue.load_ledger(path, expected_sha256="0" * 64)

    def test_cli_is_local_reproducible_and_does_not_echo_payload_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = record(0x1000, c="SYNTHETIC_PRIVATE_PAYLOAD")
            rows = [row, record(0x3000, depends_on=[ident(0x1000), ident(0x2000)]), record(0x2000)]
            path, scopes = Path(tmp) / "ledger.json", Path(tmp) / "scopes.json"
            raw = json.dumps(ledger(*rows)).encode("utf-8")
            path.write_bytes(raw)
            scopes.write_text(json.dumps(scopes_for(*rows)), encoding="utf-8")
            args = [str(path), "--ledger-sha256", hashlib.sha256(raw).hexdigest(),
                    "--scopes", str(scopes), "--max-tasks", "3"]
            outputs = []
            for seed in ("1", "21"):
                process = subprocess.run([sys.executable, "-m", "nova3_team.task_queue", *args],
                                         env={**os.environ, "PYTHONHASHSEED": seed,
                                              "PYTHONDONTWRITEBYTECODE": "1"},
                                         capture_output=True, text=True, check=False)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(process.stderr, "")
                self.assertNotIn("SYNTHETIC_PRIVATE_PAYLOAD", process.stdout)
                outputs.append(process.stdout)
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(len(json.loads(outputs[0])), 3)
            stdout, stderr = io.StringIO(), io.StringIO()
            args[2] = "0" * 64
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(queue.main(args), 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("does not match", stderr.getvalue())
            self.assertNotIn("SYNTHETIC_PRIVATE_PAYLOAD", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
