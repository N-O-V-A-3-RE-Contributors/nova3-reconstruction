"""Synthetic metadata only. No reference binary or matching claim is used."""
from dataclasses import replace
import hashlib
import itertools
import json
import unittest

from nova3_team.symbols import (
    Change, FieldLayout, RegistryConflict, Signature, SymbolKey, SymbolRecord,
    SymbolRegistry, TypeLayout,
)


SHA = hashlib.sha256(b"synthetic binary identity A").hexdigest()
OTHER_SHA = hashlib.sha256(b"synthetic binary identity B").hexdigest()


def layout(**changes):
    args = dict(name="toy::Pair", size=8, alignment=4, fields=(
        FieldLayout("first", "u32", 0, 4), FieldLayout("second", "u32", 4, 4)))
    args.update(changes)
    return TypeLayout(**args)


def signature(**changes):
    args = dict(return_type="u32", parameter_types=("toy::Pair*", "u32"),
                calling_convention="synthetic-aapcs")
    args.update(changes)
    return Signature(**args)


def record(address=0x1000, digest=SHA, **changes):
    args = dict(key=SymbolKey(digest, address), kind="function", architecture="armv7-a",
                mode="thumb", names=("toy_function",), evidence=("synthetic fixture A",))
    args.update(changes)
    return SymbolRecord(**args)


class SymbolKeyTests(unittest.TestCase):
    def test_hash_case_and_address_canonical_form(self):
        key = SymbolKey(SHA.upper(), 0x1000)
        self.assertEqual(key.identifier, f"sha256:{SHA}:va:0x0000000000001000")
        self.assertEqual(SymbolKey.parse(key.identifier), key)
        self.assertEqual(hash(key), hash(SymbolKey(SHA, 4096)))

    def test_boundaries_and_binary_isolation(self):
        for address in (0, 1, 2**32, 2**64 - 1):
            key = SymbolKey(SHA, address)
            self.assertEqual(SymbolKey.parse(key.identifier), key)
        self.assertNotEqual(SymbolKey(SHA, 0), SymbolKey(OTHER_SHA, 0))
        self.assertNotEqual(SymbolKey(SHA, 0), SymbolKey(SHA, 1))

    def test_malformed_keys_rejected(self):
        for digest in (None, 12, "x" * 64, "a" * 63, "a" * 65, SHA + "\n"):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                SymbolKey(digest, 0)
        for address in (-1, 2**64, True, False, 1.0, "0x1000", None):
            with self.subTest(address=address), self.assertRaises(ValueError):
                SymbolKey(SHA, address)

    def test_noncanonical_identifiers_rejected(self):
        key = SymbolKey(SHA, 0x1000).identifier
        for identifier in (None, 3, key.upper(), key + "\n", key.replace("va", "offset"),
                           key.replace("0000000000001000", "1000"),
                           key.replace("0x", ""), key + ":thumb"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                SymbolKey.parse(identifier)

    def test_thumb_pointer_and_alignment_are_explicit(self):
        with self.assertRaises(ValueError):
            record(0x1001)  # Never clear the low bit of a Thumb pointer silently.
        with self.assertRaises(ValueError):
            record(0x1002, mode="arm")
        self.assertEqual(record(0x1002).key.address, 0x1002)
        self.assertEqual(record(0x1001, kind="data", mode=None).key.address, 0x1001)


class AssertionValidationTests(unittest.TestCase):
    def test_layout_order_is_canonical_and_union_overlap_allowed(self):
        value = layout()
        self.assertEqual(layout(fields=tuple(reversed(value.fields))), value)
        union = layout(kind="union", fields=(FieldLayout("wide", "u64", 0, 8),
                                             FieldLayout("small", "u32", 0, 4)))
        self.assertEqual(union.kind, "union")

    def test_packed_fields_do_not_infer_alignment(self):
        packed = TypeLayout("Packed", 5, 1, (FieldLayout("a", "u8", 0, 1),
                                            FieldLayout("b", "u32", 1, 4)))
        self.assertEqual(packed.size, 5)

    def test_invalid_layouts_fail_before_registry_mutation(self):
        cases = [dict(name=""), dict(size=0), dict(size=True), dict(size=2**64),
                 dict(alignment=0), dict(alignment=3), dict(size=7), dict(kind="class"),
                 dict(fields=[]), dict(fields=("not a field",)),
                 dict(fields=(FieldLayout("same", "u32", 0, 4),
                              FieldLayout("same", "u32", 4, 4))),
                 dict(fields=(FieldLayout("beyond", "u8", 8, 1),)),
                 dict(fields=(FieldLayout("a", "u32", 0, 4),
                              FieldLayout("b", "u32", 2, 4))),
                 dict(kind="union", fields=(FieldLayout("a", "u32", 4, 4),))]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                layout(**changes)

    def test_invalid_field_values(self):
        for changes in (dict(name="\n"), dict(type_name=""), dict(offset=-1),
                        dict(offset=True), dict(size=0), dict(size=2**64)):
            args = dict(name="field", type_name="u32", offset=0, size=4)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                FieldLayout(**args)

    def test_signature_order_multiplicity_and_validation(self):
        self.assertNotEqual(signature(parameter_types=("u32", "u8")),
                            signature(parameter_types=("u8", "u32")))
        self.assertEqual(signature(parameter_types=("u32", "u32")).parameter_types,
                         ("u32", "u32"))
        for changes in (dict(return_type=""), dict(calling_convention=" "),
                        dict(parameter_types=[]), dict(parameter_types=("",)),
                        dict(parameter_types=(3,)), dict(variadic=1)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                signature(**changes)

    def test_record_validation_and_canonical_aliases(self):
        value = record(names=("z", "a", "a"), evidence=("z", "a", "z"))
        self.assertEqual(value.names, ("a", "z"))
        self.assertEqual(value.evidence, ("a", "z"))
        for changes in (dict(key="bad"), dict(kind="type"), dict(architecture=""),
                        dict(mode="\x00"), dict(names=["a"]), dict(names=(" a",)),
                        dict(evidence=()), dict(evidence=("a\nb",)), dict(layout={}),
                        dict(signature={}), dict(kind="data"),
                        dict(kind="data", mode=None, signature=signature())):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                record(**changes)


class RegistryTests(unittest.TestCase):
    def test_propose_is_read_only_then_apply_and_repeat_are_idempotent(self):
        registry = SymbolRegistry()
        initial = registry.to_json()
        value = record(layout=layout(), signature=signature())
        update = registry.propose([value])
        self.assertEqual(registry.to_json(), initial)
        self.assertEqual(update.changes, (Change(None, value),))
        self.assertEqual(update.conflicts, ())
        registry.apply(update)
        self.assertEqual(registry.records, (value,))
        noop = registry.propose([value])
        self.assertEqual(noop.changes, ())
        registry.apply(noop)
        self.assertEqual(registry.records, (value,))

    def test_aliases_and_evidence_merge_without_affecting_identity(self):
        old = record()
        registry = SymbolRegistry([old])
        renamed = record(names=("renamed",), evidence=("synthetic fixture B",))
        update = registry.propose([renamed])
        registry.apply(update)
        merged, = registry.records
        self.assertEqual(merged.key, old.key)
        self.assertEqual(merged.names, ("renamed", "toy_function"))
        self.assertEqual(merged.evidence, ("synthetic fixture A", "synthetic fixture B"))

    def test_unknown_refinements_preserve_known_assertions(self):
        unknown = record(mode=None, names=())
        known = record(layout=layout(), signature=signature())
        registry = SymbolRegistry([unknown])
        registry.apply(registry.propose([known]))
        before = registry.to_json()
        registry.apply(registry.propose([unknown]))
        self.assertEqual(registry.to_json(), before)
        self.assertEqual(registry.records, (known,))

    def test_all_signature_components_conflict(self):
        cases = [dict(return_type="u64"), dict(parameter_types=()),
                 dict(parameter_types=("u32", "toy::Pair*")),
                 dict(calling_convention="other-abi"), dict(variadic=True)]
        old = record(signature=signature())
        for changes in cases:
            with self.subTest(changes=changes):
                registry = SymbolRegistry([old])
                incoming = record(signature=signature(**changes))
                update = registry.propose([incoming])
                conflict, = update.conflicts
                self.assertEqual(conflict.path, "signature")
                self.assertEqual(conflict.existing, old.signature)
                self.assertEqual(conflict.proposed, incoming.signature)
                with self.assertRaises(RegistryConflict):
                    registry.apply(update)
                self.assertEqual(registry.records, (old,))

    def test_all_layout_components_conflict(self):
        cases = [dict(name="Other"), dict(size=16), dict(alignment=8),
                 dict(kind="union", fields=()), dict(fields=()),
                 dict(fields=(FieldLayout("first", "i32", 0, 4),
                              FieldLayout("second", "u32", 4, 4))),
                 dict(fields=(FieldLayout("first", "u16", 0, 2),
                              FieldLayout("second", "u32", 4, 4)))]
        old = record(layout=layout())
        for changes in cases:
            with self.subTest(changes=changes):
                registry = SymbolRegistry([old])
                update = registry.propose([record(layout=layout(**changes))])
                self.assertEqual(update.conflicts[0].path, "layout")
                with self.assertRaises(RegistryConflict):
                    registry.apply(update)

    def test_identity_metadata_conflicts(self):
        for changes, path in ((dict(architecture="armv6"), "architecture"),
                              (dict(mode="arm"), "mode"),
                              (dict(kind="data", mode=None), "kind")):
            with self.subTest(changes=changes):
                registry = SymbolRegistry([record()])
                update = registry.propose([record(**changes)])
                self.assertIn(path, [c.path for c in update.conflicts])
                with self.assertRaises(RegistryConflict):
                    registry.apply(update)

    def test_named_layouts_checked_across_addresses_including_batch(self):
        first = record(layout=layout())
        second = record(0x2000, layout=layout(size=16))
        for registry, incoming in ((SymbolRegistry([first]), [second]),
                                   (SymbolRegistry(), [first, second])):
            with self.subTest(existing=len(registry.records)):
                before = registry.to_json()
                update = registry.propose(incoming)
                conflict, = update.conflicts
                self.assertEqual(conflict.other_symbol_id, first.key.identifier)
                self.assertEqual(conflict.symbol_id, second.key.identifier)
                with self.assertRaises(RegistryConflict):
                    registry.apply(update)
                self.assertEqual(registry.to_json(), before)

    def test_named_layouts_are_scoped_to_binary_and_qualified_name(self):
        values = [record(layout=layout()),
                  record(0x2000, digest=OTHER_SHA, layout=layout(size=16)),
                  record(0x3000, layout=layout(name="other::Pair", size=16))]
        self.assertEqual(len(SymbolRegistry(values).records), 3)

    def test_identical_type_layouts_at_different_addresses_accepted(self):
        registry = SymbolRegistry([record(layout=layout()), record(0x2000, layout=layout())])
        self.assertEqual(len(registry.records), 2)

    def test_duplicate_keys_cannot_hide_conflicts(self):
        registry = SymbolRegistry()
        update = registry.propose([record(signature=signature()),
                                   record(signature=signature(return_type="u64"))])
        self.assertTrue(update.conflicts)
        with self.assertRaises(RegistryConflict):
            registry.apply(update)
        self.assertEqual(registry.records, ())

    def test_rejected_assertions_keep_their_provenance_in_review(self):
        registry = SymbolRegistry([record(signature=signature())])
        incoming = record(signature=signature(return_type="void"),
                          evidence=("synthetic disputed evidence",))
        report = json.loads(registry.propose([incoming]).to_json())
        self.assertEqual(report["proposed_symbols"], [incoming.to_dict()])
        self.assertEqual(report["changes"], [])
        self.assertFalse(report["applicable"])

    def test_one_conflict_rejects_entire_batch(self):
        registry = SymbolRegistry([record(signature=signature())])
        before = registry.to_json()
        update = registry.propose([record(0x2000),
                                   record(signature=signature(return_type="void"))])
        self.assertTrue(update.changes)  # Review can show nonconflicting candidates.
        self.assertFalse(json.loads(update.to_json())["applicable"])
        with self.assertRaises(RegistryConflict):
            registry.apply(update)
        self.assertEqual(registry.to_json(), before)

    def test_stale_proposal_cannot_overwrite_new_state(self):
        registry = SymbolRegistry()
        stale = registry.propose([record()])
        registry.apply(registry.propose([record(0x2000)]))
        before = registry.to_json()
        with self.assertRaisesRegex(ValueError, "stale"):
            registry.apply(stale)
        self.assertEqual(registry.to_json(), before)

    def test_forged_change_and_suppressed_conflicts_rejected(self):
        registry = SymbolRegistry([record(signature=signature())])
        good = registry.propose([record(0x2000)])
        bad = registry.propose([record(signature=signature(return_type="void"))])
        for forged in (replace(good, changes=(Change(None, record(0x3000)),)),
                       replace(bad, conflicts=()), replace(good, records=())):
            with self.subTest(forged=forged), self.assertRaisesRegex(ValueError, "recomputed"):
                registry.apply(forged)
        self.assertEqual(len(registry.records), 1)

    def test_batch_permutations_produce_identical_reports_and_snapshots(self):
        values = [record(names=("alias",)), record(layout=layout()),
                  record(0x2000, signature=signature())]
        reports, snapshots = set(), set()
        for permutation in itertools.permutations(values):
            registry = SymbolRegistry()
            update = registry.propose(permutation)
            reports.add(update.to_json())
            registry.apply(update)
            snapshots.add(registry.to_json())
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(snapshots), 1)

    def test_conflict_reports_stable_across_batch_order(self):
        values = [record(layout=layout()), record(layout=layout(size=16)),
                  record(0x2000, layout=layout(size=24))]
        reports = {SymbolRegistry().propose(p).to_json()
                   for p in itertools.permutations(values)}
        self.assertEqual(len(reports), 1)

    def test_input_errors_do_not_mutate_registry(self):
        registry = SymbolRegistry([record()])
        before = registry.to_json()
        with self.assertRaises(ValueError):
            registry.propose([record(0x2000), {}])
        with self.assertRaises(ValueError):
            registry.apply({})
        self.assertEqual(registry.to_json(), before)


class JsonTests(unittest.TestCase):
    def test_snapshot_round_trip_and_revision(self):
        registry = SymbolRegistry([record(layout=layout(), signature=signature()),
                                   record(0x2001, kind="data", mode=None)])
        document = registry.to_json()
        self.assertEqual(SymbolRegistry.from_json(document).to_json(), document)
        self.assertEqual(registry.revision, hashlib.sha256(document.encode()).hexdigest())
        self.assertTrue(document.endswith("\n"))

    def test_json_update_before_after_and_conflict_values_reviewable(self):
        registry = SymbolRegistry([record()])
        source = SymbolRegistry([record(signature=signature())]).to_json()
        update = registry.propose_json(source)
        report = json.loads(update.to_json())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["base_sha256"], registry.revision)
        change, = report["changes"]
        self.assertIsNone(change["before"]["signature"])
        self.assertEqual(change["after"]["signature"], signature().to_dict())
        registry.apply(update)
        report = json.loads(registry.propose([
            record(signature=signature(return_type="void"))]).to_json())
        conflict, = report["conflicts"]
        self.assertEqual(conflict["existing"]["return_type"], "u32")
        self.assertEqual(conflict["proposed"]["return_type"], "void")
        self.assertEqual(conflict["symbol_id"], record().key.identifier)

    def test_exported_dicts_cannot_mutate_registry(self):
        value = record(layout=layout(), signature=signature())
        registry = SymbolRegistry([value])
        before = registry.to_json()
        exported = value.to_dict()
        exported["names"].append("changed")
        exported["layout"]["fields"][0]["offset"] = 99
        exported["signature"]["parameter_types"].clear()
        self.assertEqual(registry.to_json(), before)
        with self.assertRaises(AttributeError):
            value.mode = "arm"

    def test_malformed_json_envelopes_fail_closed(self):
        documents = ["{", "[]", "null", "{}", "true", "NaN",
                     '{"schema_version":1,"schema_version":1,"symbols":[]}',
                     '{"schema_version":true,"symbols":[]}',
                     '{"schema_version":1.0,"symbols":[]}',
                     '{"schema_version":2,"symbols":[]}',
                     '{"schema_version":1,"symbols":null}',
                     '{"schema_version":1,"symbols":{},"unknown":0}',
                     '{"schema_version":1,"symbols":[null]}']
        registry = SymbolRegistry()
        for document in documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                registry.propose_json(document)
        with self.assertRaises(ValueError):
            SymbolRegistry.from_json(b"{}")
        self.assertEqual(registry.records, ())

    def test_unknown_missing_and_wrongly_typed_nested_fields_rejected(self):
        value = record(layout=layout(), signature=signature()).to_dict()
        cases = []
        for field in value:
            missing = dict(value)
            del missing[field]
            cases.append(missing)
        cases += [{**value, "unexpected": True}, {**value, "names": "not an array"},
                  {**value, "layout": {**value["layout"], "size": True}},
                  {**value, "layout": {**value["layout"], "fields": {}}},
                  {**value, "signature": {**value["signature"], "variadic": 1}},
                  {**value, "signature": {**value["signature"], "parameter_types": None}}]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                SymbolRegistry.from_json(json.dumps(dict(schema_version=1, symbols=[raw])))

    def test_conflicting_json_snapshots_and_updates_rejected(self):
        values = [record(layout=layout()).to_dict(), record(0x2000, layout=layout(size=16)).to_dict()]
        document = json.dumps(dict(schema_version=1, symbols=values))
        with self.assertRaises(RegistryConflict):
            SymbolRegistry.from_json(document)
        registry = SymbolRegistry()
        update = registry.propose_json(document)
        self.assertTrue(update.conflicts)
        self.assertEqual(registry.records, ())

    def test_duplicate_nested_json_keys_rejected(self):
        document = SymbolRegistry([record()]).to_json()
        document = document.replace('"mode": "thumb"', '"mode": null, "mode": "thumb"')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            SymbolRegistry.from_json(document)


if __name__ == "__main__":
    unittest.main()
