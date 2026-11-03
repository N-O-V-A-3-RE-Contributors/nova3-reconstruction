"""Synthetic, text-only fixtures; no prebuilt library is built or executed."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "cpufeatures_pilot", ROOT / "tools/compare/cpufeatures_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_report_state_layout_from_text_inventory(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fixture.c").write_text("""
/* static int comment_only; */
static pthread_once_t g_once;
static AndroidCpuFamily g_cpuFamily;
static uint64_t
    g_cpuFeatures;
static int g_cpuCount;
static const int fixture_debug = 0;
static void helper(void) {
    static int local_only;
    const char *text = "static int string_only; {";
}
""", encoding="utf-8")
    readelf = """
Symbol table '.dynsym' contains 1 entries:
  1: 00009000 4 OBJECT GLOBAL DEFAULT 8 fixture_debug
Symbol table '.symtab' contains 4 entries:
  1: 00001000 4 OBJECT LOCAL DEFAULT 8 g_once
  2: 00001004 4 OBJECT LOCAL DEFAULT 8 g_cpuCount
  3: 00001008 8 OBJECT LOCAL DEFAULT 8 g_cpuFeatures
  4: 00001010 4 OBJECT LOCAL DEFAULT 8 g_cpuFamily
"""
    nm = "g_once b 1000 4\ng_cpuCount b 1004 4\ng_cpuFeatures b 1008 8\ng_cpuFamily b 1010 4\n"
    commands = []

    def listing(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] and kwargs["timeout"] == 60
        return SimpleNamespace(stdout={"readelf": readelf, "nm": nm}[command[0]])

    monkeypatch.setattr(pilot.subprocess, "run", listing)
    reference = tmp_path / "fixture-reference"
    reference.write_bytes(b"unused synthetic reference; no candidate builds")
    report = pilot.run(reference, tmp_path / "candidate.c", tmp_path / "work",
                       [], [], False, state_sources=sources)
    layout = json.loads(json.dumps(report))["state_layout"]
    assert layout["matching"] == "NOT MATCHED"
    assert report["source_matching_verified"] is False
    assert [command[0] for command in commands] == ["readelf", "nm"]
    rows = layout["rows"]
    assert [(r["name"], r["declared_type"], r["declaration_order"]) for r in rows] == [
        ("g_once", "pthread_once_t", 1), ("g_cpuFamily", "AndroidCpuFamily", 2),
        ("g_cpuFeatures", "uint64_t", 3), ("g_cpuCount", "int", 4),
        ("fixture_debug", "const int", 5),
    ]
    for row, offset in zip(rows, [0, 16, 8, 4, None]):
        assert row["source_file"] == "fixture.c"
        for tool in ("readelf", "nm"):
            observation = row[tool]
            assert observation["status"] == ("absent" if offset is None else "present")
            if offset is None:
                assert observation["symbols"] == []
            else:
                assert observation["symbols"][0]["value"] == f"0x{0x1000 + offset:08x}"
                assert observation["symbols"][0]["offset_from_g_once"] == offset


def test_duplicate_anchor_leaves_relative_offsets_unknown():
    inventory = [{"name": "g_once", "declared_type": "int", "declaration_order": 1}]
    report = pilot.state_layout(inventory, "", "g_once b 1000 4\ng_once b 2000 4\n")
    row = report["rows"][0]
    assert row["readelf"]["status"] == "absent"
    assert len(row["nm"]["symbols"]) == 2
    assert all(s["offset_from_g_once"] is None for s in row["nm"]["symbols"])


def test_reference_resolution_in_disposable_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pilot.Path, "home", lambda: tmp_path / "home")
    relative = Path("analysis/edition/lib/engine.so")
    local = tmp_path / relative
    fallback = pilot.Path.home() / "Projects/nova3_re" / relative
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"synthetic home reference")
    monkeypatch.delenv("NOVA3_REFERENCE_ROOT", raising=False)
    assert pilot.resolve_reference(relative) == fallback

    configured = tmp_path / "external-analysis"
    external = configured.joinpath(*relative.parts[1:])
    external.parent.mkdir(parents=True)
    external.write_bytes(b"synthetic configured reference")
    monkeypatch.setenv("NOVA3_REFERENCE_ROOT", str(configured))
    assert pilot.resolve_reference(relative) == external
    local.parent.mkdir(parents=True)
    local.write_bytes(b"synthetic checkout reference")
    assert pilot.resolve_reference(relative) == relative
    assert pilot.resolve_reference(external) == external
    with pytest.raises(FileNotFoundError, match="reference not found"):
        pilot.resolve_reference(tmp_path / "missing" / relative)
    with pytest.raises(FileNotFoundError, match="reference not found"):
        pilot.resolve_reference(Path("other/engine.so"))
