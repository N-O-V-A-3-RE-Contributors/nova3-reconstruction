"""Source-owned fabricated ELF fixtures only; never opens a game binary."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import synthetic_reference_objects as synth
from nova3_team.tests.test_indexer import elf_fixture, patch_section, symbol


def section_rows(data):
    start = struct.unpack_from("<I", data, 32)[0]
    count = struct.unpack_from("<H", data, 48)[0]
    return [struct.unpack_from("<10I", data, start + 40 * i) for i in range(count)]


def append_sections(data, extras):
    result = bytearray(data)
    rows = section_rows(data)
    names = bytearray(data[rows[6][4]:rows[6][4] + rows[6][5]])
    for name, typ, va, payload, link, info, width in extras:
        nameoff = len(names)
        names.extend(name.encode() + b"\0")
        rows.append((nameoff, typ, 0, va, len(result), len(payload), link, info, 1, width))
        result.extend(payload)
    rows[6] = (*rows[6][:4], len(result), len(names), *rows[6][6:])
    result.extend(names)
    result.extend(bytes(-len(result) % 4))
    struct.pack_into("<I", result, 32, len(result))
    struct.pack_into("<H", result, 48, len(rows))
    for row in rows:
        result.extend(struct.pack("<10I", *row))
    return bytes(result)


def fixture(*, relocations=True, aliases=False, dynamic_symbols=False):
    # These small instruction encodings are authored test data, not game bytes.
    entries = [symbol("arm_entry", 0x1000, 8), symbol("thumb_entry", 0x1021, 8),
               symbol("external", 0, 0, section=0)]
    if aliases:
        entries.append(symbol("arm_alias", 0x1000, 8))
    data = bytearray(elf_fixture(entries if not dynamic_symbols else [],
                                 dynamic=entries if dynamic_symbols else []))
    text = section_rows(data)[1]
    data[text[4]:text[4] + 8] = bytes.fromhex("0000a0e11eff2fe1")
    data[text[4] + 32:text[4] + 40] = bytes.fromhex("00bf704700bf00bf")
    struct.pack_into("<I", data, 28, len(data))
    struct.pack_into("<HH", data, 42, 32, 1)
    data.extend(struct.pack("<8I", 1, text[4], 0x1000, 0x1000, 96, 96, 5, 4))
    if relocations:
        table = 5 if dynamic_symbols else 3
        data = append_sections(data, [
            (".rel.test", 9, 0, struct.pack("<IIII", 0x1004, (3 << 8) | 28,
                                           0x2000, 23), table, 0, 8),
            (".rela.test", 4, 0, struct.pack("<IIi", 0x1022, (3 << 8) | 10, -4), table, 1, 12),
        ])
    return bytes(data)


def function(fid="arm", address=0x1000, size=8, mode="arm", name="arm_entry", **extra):
    return dict(id=fid, address=address, size=size, mode=mode,
                symbol=dict(kind="elf", name=name), **extra)


def manifest(data, functions=None):
    return dict(schema=synth.MANIFEST_SCHEMA,
                reference=dict(sha256=synth.digest(data), edition="synthetic"),
                groups=[dict(id="fixture_tu", functions=functions if functions is not None else [
                    function(), function("thumb", 0x1020, mode="thumb", name="thumb_entry")])])


class SyntheticReferenceTests(unittest.TestCase):
    def setUp(self):
        (synth.ROOT / ".cache").mkdir(mode=0o700, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-reference-test-", dir=synth.ROOT / ".cache")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture()
        self.reference = self.root / "fixture.so"
        self.reference.write_bytes(self.data)

    def stage(self, spec=None, *, data=None, name="stage", **kwargs):
        data = self.data if data is None else data
        self.reference.write_bytes(data)
        return synth.stage(self.reference, manifest(data) if spec is None else spec,
                           self.root / name, expected_sha256=synth.digest(data), edition="synthetic", **kwargs)

    def refused(self, spec=None, *, data=None, pattern=None, **kwargs):
        with self.assertRaisesRegex((ValueError, OSError), pattern or ".*"):
            self.stage(spec, data=data, **kwargs)
        self.assertFalse((self.root / kwargs.get("name", "stage")).exists())

    def test_stage_a32_thumb_and_only_observed_relocations(self):
        with mock.patch.object(synth.subprocess, "run") as run:
            report = self.stage(group_assembly=True)
        run.assert_not_called()
        self.assertEqual(report["labels"], synth.LABELS)
        self.assertEqual(report["source_match"], "NOT MATCHED")
        self.assertFalse(report["original_objects_recovered"])
        self.assertEqual(report["objects"], [])
        self.assertEqual(report["surviving_section_relocations"], 3)
        self.assertEqual(self.reference.read_bytes(), self.data)
        out = self.root / "stage"
        arm = (out / "asm/arm.s").read_text()
        thumb = (out / "asm/thumb.s").read_text()
        self.assertIn(".arm", arm)
        self.assertIn(".thumb_func", thumb)
        self.assertIn("ORIGINAL_O_NOT_RECOVERED", arm)
        self.assertIn(".byte 0x00, 0x00, 0xa0, 0xe1", arm)
        self.assertNotIn(".reloc", arm)
        self.assertIn("synthetic_thumb", (out / "groups/fixture_tu.s").read_text())
        rel = json.loads((out / "relocations/arm.json").read_text())
        self.assertEqual(len(rel["records"]), 1)
        self.assertEqual(rel["records"][0]["linked_address"], 0x1004)
        self.assertEqual(rel["records"][0]["function_offset"], 4)
        self.assertEqual(rel["records"][0]["symbol"]["name"], "external")
        self.assertIsNone(rel["records"][0]["observed_explicit_addend"])
        self.assertEqual(rel["missing_prelink_relocations"], "UNKNOWN_NOT_RECONSTRUCTED")
        rela = json.loads((out / "relocations/thumb.json").read_text())
        self.assertEqual(rela["records"][0]["observed_explicit_addend"], -4)
        self.assertEqual(rela["records"][0]["format"], "RELA")
        config = json.loads((out / "objdiff.json").read_text())
        self.assertFalse(config["build_base"])
        self.assertFalse(config["build_target"])
        self.assertEqual(config["units"][0]["target_path"], "reference/arm.o")
        self.assertEqual(config["units"][0]["base_path"], "candidate/arm/build.o")
        self.assertFalse(config["units"][0]["metadata"]["complete"])
        layout = json.loads((out / "link-layout.json").read_text())
        self.assertEqual(layout["positions"][0]["address"], 0x1000)
        self.assertEqual(layout["original_link_script"], "UNKNOWN")

    def test_no_surviving_records_does_not_claim_no_original_relocations(self):
        self.stage(data=fixture(relocations=False))
        rel = json.loads((self.root / "stage/relocations/arm.json").read_text())
        self.assertEqual(rel["records"], [])
        self.assertEqual(rel["status"], "NO_SURVIVING_RECORD_START_IN_WINDOW")
        self.assertEqual(rel["missing_prelink_relocations"], "UNKNOWN_NOT_RECONSTRUCTED")

    def test_dynsym_relocation_symbol_identity(self):
        report = self.stage(data=fixture(dynamic_symbols=True))
        self.assertEqual(report["functions"][0]["symbol_evidence"], "ELF_EXACT")
        rel = json.loads((self.root / "stage/relocations/arm.json").read_text())
        self.assertEqual(rel["records"][0]["symbol"]["table_index"], 5)

    def test_explicit_alias_and_detached_alias_refusal(self):
        data = fixture(aliases=True)
        alias = function("alias", name="arm_alias", alias_of="arm")
        spec = manifest(data, [alias, function()])  # forward aliases allowed
        report = self.stage(spec, data=data)
        self.assertEqual(len(report["functions"]), 2)
        for changes in ({"address": 0x1020}, {"size": 4}, {"mode": "thumb"},
                        {"alias_of": "absent"}, {"alias_of": "alias"}):
            bad = copy.deepcopy(spec)
            bad["groups"][0]["functions"][0].update(changes)
            with self.subTest(changes=changes):
                self.refused(bad, data=data, name="bad")
                self.assertFalse((self.root / "bad").exists())

    def test_alias_rules_cannot_hide_overlap_chains_or_cross_group_ownership(self):
        data = fixture(aliases=True)
        for functions in ([function(), function("other", name="arm_alias")],
                          [function(), function("alias", name="arm_alias", alias_of="arm"),
                           function("chain", name="arm_alias", alias_of="alias")]):
            self.refused(manifest(data, functions), data=data)
        spec = manifest(data, [function()])
        spec["groups"].append(dict(id="detached", functions=[function("alias", name="arm_alias", alias_of="arm")]))
        self.refused(spec, data=data, pattern="alias")

    def test_owner_identity_requires_explicit_kind_and_cannot_override_elf(self):
        fn = function("owner", 0x1040, name="unlabelled")
        fn["symbol"]["kind"] = "owner"
        result = self.stage(manifest(self.data, [fn]))
        self.assertEqual(result["functions"][0]["symbol_evidence"], "OWNER_ASSERTED_ONLY")
        fn["symbol"]["name"] = "arm_entry"
        self.refused(manifest(self.data, [fn]), name="bad")
        fn["symbol"] = dict(kind="elf", name="absent")
        self.refused(manifest(self.data, [fn]), name="bad")

    def test_unknown_modes_alignment_and_ranges_refused_before_writes(self):
        for changes in ({"mode": "UNKNOWN"}, {"mode": None}, {"address": 0x1001},
                        {"address": -4}, {"address": True}, {"address": 1 << 32},
                        {"size": 0}, {"size": 6}, {"size": synth.MAX_WINDOW + 4},
                        {"address": 0xfffffffc, "size": 8},
                        {"address": 0x105c, "size": 8}, {"address": 0x2000}):
            fn = function()
            fn.update(changes)
            with self.subTest(changes=changes):
                self.refused(manifest(self.data, [fn]))
        for confidence in (True, -1, 1.1, float("nan"), float("inf"), "reviewed"):
            self.refused(manifest(self.data, [function(confidence=confidence)]))

    def test_owner_overlap_and_duplicate_ids_refused(self):
        f = function("inside", 0x1004, size=4, name="unlabelled")
        f["symbol"]["kind"] = "owner"
        self.refused(manifest(self.data, [function(), f]), pattern="overlapping")
        self.refused(manifest(self.data, [function(), function()]), pattern="duplicate")
        spec = manifest(self.data)
        spec["groups"] *= 2
        self.refused(spec, pattern="duplicate")

    def test_architecture_eabi_linked_type_and_truncation(self):
        for offset, fmt, value in ((4, "B", 2), (5, "B", 2), (16, "H", 1),
                                   (18, "H", 62), (36, "I", 0x04000000), (44, "H", 0)):
            data = bytearray(self.data)
            struct.pack_into("<" + fmt, data, offset, value)
            self.refused(data=bytes(data))
        for end in (0, 51, len(self.data) - 1):
            self.refused(data=self.data[:end])

    def test_pt_load_and_section_mapping_must_agree(self):
        phoff = struct.unpack_from("<I", self.data, 28)[0]
        for field, value in ((4, 4), (5, 4), (6, 4), (7, 3), (1, len(self.data))):
            data = bytearray(self.data)
            struct.pack_into("<I", data, phoff + field * 4, value)
            self.refused(data=bytes(data))
        self.refused(data=patch_section(self.data, 1, 4, 56))
        self.refused(data=patch_section(self.data, 7, 3, 0xfffffff4), pattern="overflows")
        # A second mapping only partially intersects; not merely duplicate containment.
        data = bytearray(self.data)
        struct.pack_into("<I", data, 28, len(data))
        struct.pack_into("<H", data, 44, 2)
        data.extend(self.data[phoff:phoff + 32])
        data.extend(struct.pack("<8I", 1, 56, 0x1004, 0x1004, 4, 4, 5, 4))
        self.refused(data=bytes(data), pattern="overlapping")

    def test_distinct_virtual_windows_cannot_reuse_overlapping_file_bytes(self):
        phoff = struct.unpack_from("<I", self.data, 28)[0]
        data = bytearray(self.data)
        struct.pack_into("<I", data, 28, len(data))
        struct.pack_into("<H", data, 44, 2)
        data.extend(self.data[phoff:phoff + 32])
        data.extend(struct.pack("<8I", 1, 52, 0x3000, 0x3000, 96, 96, 5, 4))
        fn = function("file_alias", 0x3000, name="owner_window")
        fn["symbol"]["kind"] = "owner"
        self.refused(manifest(bytes(data), [function(), fn]), data=bytes(data), pattern="file windows")

    def test_hash_and_edition_pin_required(self):
        for pin in ("0" * 64, "abc", None, "A" * 64):
            with self.assertRaises(ValueError):
                synth.stage(self.reference, manifest(self.data), self.root / "stage",
                            expected_sha256=pin, edition="synthetic")
        spec = manifest(self.data)
        spec["reference"]["edition"] = "premium"
        self.refused(spec, pattern="edition")
        spec = manifest(self.data)
        spec["reference"]["sha256"] = "0" * 64
        self.refused(spec, pattern="SHA-256")

    def test_bounded_counts_sizes_and_strict_schema(self):
        spec = manifest(self.data)
        spec["groups"] *= synth.MAX_GROUPS + 1
        self.refused(spec, pattern="group")
        with mock.patch.object(synth, "MAX_FUNCTIONS", 1):
            self.refused(pattern="count")
        with mock.patch.object(synth, "MAX_TOTAL", 8):
            self.refused(pattern="total")
        for mutate in (lambda s: s.update(translation_unit_proven=True),
                       lambda s: s["groups"][0].update(original_object=True),
                       lambda s: s["groups"][0]["functions"][0].update(id="../escape"),
                       lambda s: s["groups"][0]["functions"][0]["symbol"].update(kind="guess")):
            spec = manifest(self.data)
            mutate(spec)
            self.refused(spec)
        for payload in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'{invalid', b'\xff'):
            with self.assertRaises(ValueError):
                synth._json(payload)

    def test_malformed_and_unsupported_relocations_refused(self):
        # Last two sections are fabricated REL and RELA.
        for index, field, value in ((8, 9, 4), (8, 6, 7), (8, 7, 999),
                                    (8, 5, 15), (9, 1, 19), (8, 1, 0x60000001)):
            self.refused(data=patch_section(self.data, index, field, value))
        data = bytearray(self.data)
        rel = section_rows(data)[8]
        struct.pack_into("<I", data, rel[4] + 4, 9999 << 8)
        self.refused(data=bytes(data), pattern="symbol index")
        data = bytearray(self.data)
        rela = section_rows(data)[9]
        struct.pack_into("<I", data, rela[4], 0x3000)
        self.refused(data=bytes(data), pattern="outside declared")
        with mock.patch.object(synth, "MAX_RELOCATIONS", 1):
            self.refused(pattern="oversized")

    def test_dynamic_only_relocation_metadata_is_not_silently_ignored(self):
        data = append_sections(self.data, [(".dynamic", 6, 0,
                    struct.pack("<IIIIII", 17, 0x4000, 18, 8, 0, 0), 0, 0, 8)])
        self.refused(data=data, pattern="exact section coverage")
        data = append_sections(self.data, [(".dynamic", 6, 0,
                    struct.pack("<IIII", 36, 0x4000, 0, 0), 0, 0, 8)])
        self.refused(data=data, pattern="RELR")

    def test_dynamic_rel_section_coverage_and_entry_width(self):
        # Every dynamic pointer must identify a conventional table exactly.
        data = bytearray(self.data)
        data = patch_section(data, 8, 3, 0x4000)
        data = append_sections(data, [(".dynamic", 6, 0,
                    struct.pack("<8I", 17, 0x4000, 18, 16, 19, 8, 0, 0), 0, 0, 8)])
        report = self.stage(data=data)
        self.assertEqual(report["surviving_section_relocations"], 3)
        for payload in (struct.pack("<8I", 17, 0x4000, 18, 16, 19, 4, 0, 0),
                        struct.pack("<4I", 17, 0x4000, 18, 16),
                        struct.pack("<6I", 17, 0x4000, 17, 0x4000, 0, 0)):
            bad = append_sections(self.data, [(".dynamic", 6, 0, payload, 0, 0, 8)])
            self.refused(data=bad, name="bad")

    def test_dynamic_program_header_without_section_cannot_hide_relocations(self):
        phoff = struct.unpack_from("<I", self.data, 28)[0]
        data = bytearray(self.data)
        struct.pack_into("<I", data, 28, len(data))
        struct.pack_into("<H", data, 44, 2)
        data.extend(self.data[phoff:phoff + 32])
        data.extend(struct.pack("<8I", 2, 52, 0x4000, 0x4000, 8, 8, 4, 4))
        self.refused(data=bytes(data), pattern="PT_DYNAMIC lacks exact")

    def test_symbol_text_and_selected_relocation_output_bounds(self):
        with mock.patch.object(synth, "MAX_NAME_CHARS", 1):
            self.refused(pattern="text bound")
        with mock.patch.object(synth, "MAX_STAGED_RELOCATIONS", 1):
            self.refused(pattern="worksheet records")

    def test_source_symbol_names_never_become_assembly_directives(self):
        fn = function("owner", 0x1040, name='untrusted\n.incbin "PRIVATE_SENTINEL"')
        fn["symbol"]["kind"] = "owner"
        self.stage(manifest(self.data, [fn]))
        self.assertNotIn("PRIVATE_SENTINEL", (self.root / "stage/asm/owner.s").read_text())

    def test_private_modes_deterministic_outputs_and_ignored_paths(self):
        self.stage(name="one", group_assembly=True)
        self.stage(name="two", group_assembly=True)
        one, two = self.root / "one", self.root / "two"
        files = sorted(p.relative_to(one) for p in one.rglob("*") if p.is_file())
        for rel in files:
            self.assertEqual((one / rel).read_bytes(), (two / rel).read_bytes())
        for path in [one, *one.rglob("*")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        result = subprocess.run(["git", "check-ignore", "--quiet", "--", str(one / "asm/arm.s")], cwd=synth.ROOT)
        self.assertEqual(result.returncode, 0)

    def test_existing_output_and_symlink_paths_refused_without_modification(self):
        self.stage()
        snapshot = (self.root / "stage/manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(snapshot, (self.root / "stage/manifest.json").read_bytes())
        (self.root / "empty").mkdir()
        with self.assertRaises(ValueError):
            self.stage(name="empty")
        (self.root / "link").symlink_to(self.root / "stage", target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.stage(name="link")
        with self.assertRaises((ValueError, OSError)):
            self.stage(name="link/child")
        self.assertFalse((self.root / "stage/child").exists())
        with self.assertRaises(ValueError):
            self.stage(name="../escape")
        with self.assertRaises(ValueError):
            synth.stage(self.reference, manifest(self.data), Path("/tmp/stage-reference-refused"),
                        expected_sha256=synth.digest(self.data), edition="synthetic")

    def test_nonregular_or_symlink_inputs_do_not_hang(self):
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        for source in (fifo, self.root):
            with self.assertRaises((ValueError, OSError)):
                synth._read(source)
        link = self.root / "input-link"
        link.symlink_to(self.reference)
        with self.assertRaises(OSError):
            synth._read(link)
        with self.assertRaises(ValueError):
            synth._read(self.reference, 52)

    def test_assembler_requires_explicit_enable_and_pins_missing_tool_rolls_back(self):
        profile = dict(schema=synth.PROFILE_SCHEMA, kind="gnu-as", executable=str(self.root / "missing-as"),
                       sha256="0" * 64, version_sha256="0" * 64)
        with mock.patch.object(synth.subprocess, "run") as run:
            self.refused(assembler_profile=profile, pattern="explicit enable")
            self.refused(assemble=True, pattern="explicit enable")
            self.refused(assemble=True, assembler_profile=profile)
        run.assert_not_called()
        self.assertFalse((self.root / "stage").exists())

    def test_write_failure_rolls_back_new_leaf_and_created_parents(self):
        with mock.patch.object(synth, "_write", side_effect=OSError("fixture refusal")):
            with self.assertRaises(OSError):
                self.stage(name="new-parent/leaf")
        self.assertFalse((self.root / "new-parent").exists())
        self.assertEqual(self.reference.read_bytes(), self.data)

    def test_assembler_failure_or_wrong_output_rolls_back(self):
        profile = dict(executable="fixture-as", flags=[])
        with mock.patch.object(synth, "_assembler", return_value=profile):
            with mock.patch.object(synth.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                self.refused(assemble=True, assembler_profile={})
            with mock.patch.object(synth.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 30)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.stage(assemble=True, assembler_profile={})
                self.assertFalse((self.root / "stage").exists())
            def bad_output(command, **kwargs):
                (kwargs["cwd"] / command[-1]).write_bytes(b"not an ELF")
                return subprocess.CompletedProcess(command, 0)
            with mock.patch.object(synth.subprocess, "run", side_effect=bad_output):
                self.refused(assemble=True, assembler_profile={})

    def test_existing_output_refusal_never_executes_even_enabled_tool(self):
        (self.root / "stage").mkdir()
        with mock.patch.object(synth, "_assembler") as assembler:
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.stage(assemble=True, assembler_profile={})
        assembler.assert_not_called()
        self.assertEqual(list((self.root / "stage").iterdir()), [])

    def test_cli_sanitizes_failures_and_wrapper_works_offline(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest(self.data)))
        args = ["--reference", str(self.reference), "--manifest", str(path),
                "--expected-sha256", synth.digest(self.data), "--edition", "synthetic",
                "--out", str(self.root / "cli")]
        result = subprocess.run([sys.executable, str(synth.ROOT / "tools/stage_reference_objects.py"), *args],
                                cwd="/tmp", capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "SYNTHETIC_REFERENCE; ORIGINAL_O_NOT_RECOVERED; NOT MATCHED")
        args[1] = str(self.root / "PRIVATE_PATH_SENTINEL")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(synth.main(args), 2)
        self.assertNotIn("PRIVATE_PATH_SENTINEL", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    @unittest.skipUnless(shutil.which("clang"), "optional offline ARM assembler unavailable")
    def test_real_pinned_clang_synthetic_objects_deterministic(self):
        executable = Path(shutil.which("clang")).resolve()
        version = subprocess.run([str(executable), "--version"], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, env=synth._environment(), check=True).stdout
        profile = dict(schema=synth.PROFILE_SCHEMA, kind="clang", executable=str(executable),
                       sha256=synth._executable_digest(executable),
                       version_sha256=synth.digest(version))
        a = self.stage(name="object-one", assemble=True, assembler_profile=profile)
        b = self.stage(name="object-two", assemble=True, assembler_profile=profile)
        self.assertEqual(a, b)
        self.assertEqual(len(a["objects"]), 2)
        for row in a["objects"]:
            one = self.root / "object-one" / row["path"]
            two = self.root / "object-two" / row["path"]
            self.assertEqual(one.read_bytes(), two.read_bytes())
            self.assertIn(b"SYNTHETIC_REFERENCE ORIGINAL_O_NOT_RECOVERED", one.read_bytes())
            self.assertEqual(stat.S_IMODE(one.stat().st_mode), 0o600)
        bad = dict(profile, sha256="0" * 64)
        self.refused(name="wrong-tool", assemble=True, assembler_profile=bad, pattern="executable SHA")
        bad = dict(profile, version_sha256="0" * 64)
        self.refused(name="wrong-version", assemble=True, assembler_profile=bad, pattern="version pin")


if __name__ == "__main__":
    unittest.main()
