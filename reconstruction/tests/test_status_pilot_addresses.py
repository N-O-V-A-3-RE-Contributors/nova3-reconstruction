"""Read-only STATUS freshness gate; runnable as a CLI as well as with pytest.

    python reconstruction/tests/test_status_pilot_addresses.py \
        --reference /local/path/libNOVA3_neon.so [--status /tmp/STATUS.md]

Only .symtab STT_FUNC entry addresses and st_size are measured. No instructions,
pilot execution, compiler comparison, reconstruction approval or byte matching
is involved. ``approval`` records report availability only (report-present or
report-absent); neither value grants approval. Missing reports do not fail.

Current STATUS uses historical narrative sections, not a disposition table.
The bounded legacy adapter below extracts its fourteen principal dispositions
and dependency claims. Explicit tables with function/address/size/status/pilot
columns are also supported, without a fixed function allowlist. Pilot filenames
in this repo are semantic names: the address must occur *inside* the named file,
as a complete hex token (case and zero padding ignored), not in its basename.
Missing literals are deliberately not excused by a pilot's symbol lookup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = Path("analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so")
HEX = r"0x[0-9a-fA-F]+"
NUMBER = r"[0-9][0-9,]*"
VA_SIZE = re.compile(rf"`?({HEX})`?\s*,\s*({NUMBER})\s*(?:bytes|B)\b")
PILOT = re.compile(r"tools/compare/[\w.-]+_pilot\.py")
REPORT = re.compile(r"\.cache/[\w./-]+\.json\b")


def clean(value: str) -> str:
    return " ".join(value.replace("`", "").replace("**", "").split())


def claim(function: str, address: str, size: str, status: str,
          pilot: str, context: str) -> dict:
    return dict(function=clean(function), address=hex(int(address, 16)),
                cited_size=int(size.replace(",", "")), status=clean(status),
                pilot=pilot, reports=sorted(set(REPORT.findall(context))))


def cells(line: str) -> list[str]:
    # Protect pipes in inline code, e.g. enum flags, before splitting Markdown.
    return [clean(cell) for cell in re.split(r"\|(?=(?:[^`]*`[^`]*`)*[^`]*$)",
                                           line.strip().strip("|"))]


def explicit_rows(text: str) -> list[dict]:
    """Parse disposition tables by header, never by a fixed column position."""
    aliases = {"elf va": "address", "va": "address", "ref": "size",
               "cited_size": "size", "cited size": "size",
               "byte size": "size", "report path": "report"}
    header = []
    rows = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            header = []
            continue
        values = cells(line)
        keys = [aliases.get(v.lower(), v.lower()) for v in values]
        if {"function", "address", "size", "status", "pilot"} <= set(keys):
            header = keys
            continue
        if not header or all(re.fullmatch(r"[-: ]+", v) for v in values):
            continue
        if len(values) != len(header):
            raise ValueError("malformed disposition row")
        row = dict(zip(header, values))
        address = re.fullmatch(HEX, row["address"])
        size = re.fullmatch(rf"({NUMBER})(?:\s*(?:B|bytes))?", row["size"])
        if not address or not size:
            raise ValueError("malformed disposition address/size")
        rows.append(claim(row["function"], address[0], size[1], row["status"],
                          row["pilot"], line))
    return rows


def legacy_rows(text: str) -> list[dict]:
    """Compatibility with this checkout's narrative, failing closed on drift.

    Section anchors select claims, never supply an address or byte size. Keep
    dependency dispositions distinct from implemented-source dispositions.
    """
    sections = re.split(r"(?m)^## ", text)

    def section(title: str) -> str:
        hits = [s for s in sections if s.splitlines()[0].startswith(title)]
        if len(hits) != 1:
            raise ValueError(f"missing/ambiguous STATUS section: {title}")
        return hits[0]

    def first_pilot(body: str) -> str:
        match = PILOT.search(body)
        if not match:
            raise ValueError("STATUS section names no pilot")
        return match[0]

    rows = []
    primary = (
        "The matrix transpose is recovered",
        "The copy-with-mode matrix constructor is recovered",
        "The shared matrix constructor is recovered",
        "The matrix product behind prepareCache",
        "prepareCache is recovered",
        "DriverCallbackInterface::Init is source",
        "DetectNeon probe recovered",
    )
    for title in primary:
        body = section(title)
        marker = re.search(r"ELF VA\s*", body)
        match = VA_SIZE.match(body, marker.end()) if marker else None
        names = re.findall(r"`([^`]+)`", body[:marker.start()]) if marker else []
        if not match or not names:
            raise ValueError(f"missing function/VA/size: {title}")
        disposition = body.split("**Disposition:**", 1)
        # Without a disposition sentence, retain UNKNOWN instead of inferring
        # a verification grade from a section heading or a successful test.
        status = (disposition[1].split(".", 1)[0]
                  if len(disposition) == 2 else "UNKNOWN")
        rows.append(claim(names[-1], match[1], match[2], status,
                          first_pilot(body), body))

    transpose = section(primary[0])
    inverse = re.search(rf"`getInverse`\s*\(({HEX}),\s*({NUMBER})\s*B\)"
                        r"\s*remains\s+([A-Z ]+?)\s+and\b",
                        transpose)
    if not inverse:
        raise ValueError("missing getInverse dependency bounds")
    rows.append(claim("getInverse", inverse[1], inverse[2], inverse[3],
                      first_pilot(section(primary[1])), inverse[0]))

    leaves = section("Three CSoftwareSkinTechnique leaves")
    leaf_count = 0
    for line in leaves.splitlines():
        if not line.startswith("|"):
            continue
        cols = cells(line)
        if len(cols) == 5 and re.fullmatch(HEX, cols[1]):
            size = re.fullmatch(rf"({NUMBER}) B", cols[2])
            if not size:
                raise ValueError("malformed leaf reference size")
            rows.append(claim(cols[0], cols[1], size[1], cols[4],
                              first_pilot(leaves), line))
            leaf_count += 1
    if leaf_count != 3:
        raise ValueError("missing/extra software-skin leaf rows")

    cache = re.search(r"tools/compare/skin_cpu_cache\.py", leaves)
    skin = re.search(rf"`skin`\s*\(`({HEX})`,\s*({NUMBER})\s*B\)", leaves)
    init = re.search(rf"\|\s*`([^`]*CSoftwareSkinTechnique::init[^`]*)`\s*"
                     rf"\|\s*`({HEX})`,\s*({NUMBER})\s*B\s*\|",
                     section(primary[-1]))
    dependency_status = re.search(r"The `init`/`skin` cpu-features call sites are "
                                  r"([^.]+)\.", leaves)
    if not cache or not skin or not init or not dependency_status:
        raise ValueError("missing software-skin dependency bounds/pilot")
    rows.append(claim(init[1], init[2], init[3], dependency_status[1],
                      cache[0], init[0]))
    rows.append(claim("CSoftwareSkinTechnique::skin", skin[1], skin[2],
                      dependency_status[1], cache[0], skin[0]))

    cpu = section("All three cpu-features getters now match")
    initializer = re.search(rf"\|\s*`android_cpuInit`\s*({HEX})\s*\|\s*"
                            rf"({NUMBER}) B\s*\|[^|]+\|([^|]+)\|", cpu)
    if not initializer:
        raise ValueError("missing android_cpuInit reference bounds")
    rows.append(claim("android_cpuInit", initializer[1], initializer[2],
                      initializer[3], first_pilot(cpu), initializer[0]))
    return rows


def parse_status(text: str) -> list[dict]:
    rows = explicit_rows(text) + (legacy_rows(text) if "## The matrix transpose" in text
                                  else [])
    if not rows:
        raise ValueError("no function dispositions found (cannot pass vacuously)")
    return rows


def symtab_sizes(reference: Path) -> dict[int, set[int]]:
    """Read symbol metadata only; accept defined STT_FUNCs from .symtab.

    Iterate fixed-size entries directly to avoid decoding every unused symbol
    name. Preserve st_value exactly: no rounding or guessed Thumb adjustment.
    Same-address aliases are accepted only if their sizes agree.
    """
    from elftools.elf.elffile import ELFFile

    result: dict[int, set[int]] = {}
    with reference.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or elf["e_machine"] != "EM_ARM":
            raise ValueError("reference must be an ELF32 ARM Premium engine")
        table = elf.get_section_by_name(".symtab")
        if table is None or table["sh_type"] != "SHT_SYMTAB":
            raise ValueError("reference has no .symtab")
        entry = struct.Struct(("<" if elf.little_endian else ">") + "IIIBBH")
        if table["sh_entsize"] != entry.size:
            raise ValueError("unexpected .symtab entry size")
        for _name, address, size, info, _other, index in entry.iter_unpack(table.data()):
            if info & 15 == 2 and index != 0:  # STT_FUNC, defined (not SHN_UNDEF)
                result.setdefault(address, set()).add(size)
    return result


def confined(root: Path, value: str, prefix: str) -> Path | None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value.startswith(prefix):
        return None
    resolved = (root / path).resolve()
    return resolved if resolved.is_relative_to((root / prefix).resolve()) else None


def check_rows(rows: list[dict], sizes: dict[int, set[int]], root: Path) -> list[dict]:
    output = []
    for row in rows:
        address = int(row["address"], 16)
        measured = sizes.get(address, set())
        state = "ok" if measured == {row["cited_size"]} else "stale"
        pilot = confined(root, row["pilot"], "tools/compare/")
        if pilot is None or pilot.suffix != ".py" or not pilot.is_file():
            state = "missing"
        else:
            literals = {int(token, 16) for token in
                        re.findall(rf"\b{HEX}\b", pilot.read_text(encoding="utf-8"))}
            if address not in literals:
                state = "stale"
        reports = [confined(root, p, ".cache/") for p in row["reports"]]
        present = bool(reports) and all(p is not None and p.is_file() for p in reports)
        output.append({key: row[key] for key in
                       ("function", "address", "cited_size", "status", "pilot")}
                      | dict(symtab_size=next(iter(measured)) if len(measured) == 1 else None,
                             row_state=state,
                             approval="report-present" if present else "report-absent"))
    return sorted(output, key=lambda r: (int(r["address"], 16), r["function"], r["pilot"]))


def default_reference(root: Path) -> Path:
    if os.environ.get("NOVA3_PREMIUM_ELF"):
        return Path(os.environ["NOVA3_PREMIUM_ELF"]).expanduser()
    local = root / REFERENCE
    return local if local.is_file() else Path.home() / "Projects/nova3_re" / REFERENCE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--reference", "--elf", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        rows = parse_status((args.status or root / "docs/progress/STATUS.md")
                            .read_text(encoding="utf-8"))
        report = check_rows(rows, symtab_sizes(args.reference or default_reference(root)), root)
    except (OSError, ValueError, ImportError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(any(row["row_state"] != "ok" for row in report))


# Test fixtures contain only source-owned synthetic metadata, never ELF code.
def _synthetic_elf(path: Path) -> None:
    names = b"\0.shstrtab\0.symtab\0"
    # Two valid aliases, an OBJECT and an undefined FUNC negative control.
    entries = [(0, 0, 0, 0, 0, 0), (0, 0x100, 8, 2, 0, 1),
               (0, 0x100, 8, 18, 0, 1), (0, 0x200, 8, 1, 0, 1),
               (0, 0x300, 8, 2, 0, 0)]
    symbols = b"".join(struct.pack("<IIIBBH", *e) for e in entries)
    offset = 52 + len(names) + len(symbols)
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    header = struct.pack("<16sHHIIIIIHHHHHH", ident, 3, 40, 1, 0, 0,
                         offset, 0, 52, 0, 0, 40, 3, 1)
    shstr = struct.pack("<10I", 1, 3, 0, 0, 52, len(names), 0, 0, 1, 0)
    symtab = struct.pack("<10I", 11, 2, 0, 0, 52 + len(names), len(symbols),
                         1, 1, 4, 16)
    path.write_bytes(header + names + symbols + bytes(40) + shstr + symtab)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    pilot = tmp_path / "tools/compare/probe_pilot.py"
    pilot.parent.mkdir(parents=True, exist_ok=True)
    pilot.write_text("TARGET = 0x00000100\n", encoding="utf-8")
    status = tmp_path / "STATUS.md"
    status.write_text(
        "| function | ELF VA | size | status | pilot | report |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| probe | `0x100` | 8 B | NOT MATCHED | "
        "`tools/compare/probe_pilot.py` | `.cache/probe.json` |\n", encoding="utf-8")
    reference = tmp_path / "metadata.elf"
    _synthetic_elf(reference)
    return status, reference


def test_metadata_gate_and_optional_report(tmp_path, capsys):
    status, reference = _fixture(tmp_path)
    args = ["--root", str(tmp_path), "--status", str(status), "--reference", str(reference)]
    assert symtab_sizes(reference) == {0x100: {8}}
    assert main(args) == 0
    row, = json.loads(capsys.readouterr().out)
    assert row == dict(function="probe", address="0x100", cited_size=8, symtab_size=8,
                       status="NOT MATCHED", row_state="ok",
                       pilot="tools/compare/probe_pilot.py", approval="report-absent")
    report = tmp_path / ".cache/probe.json"
    report.parent.mkdir()
    report.write_text("{}", encoding="utf-8")
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["approval"] == "report-present"


def test_mutated_status_and_missing_pilot(tmp_path, capsys):
    status, reference = _fixture(tmp_path)
    original = status.read_text()
    args = ["--root", str(tmp_path), "--status", str(status), "--reference", str(reference)]
    for before, after, state in (("0x100", "0x104", "stale"),
                                 ("8 B", "9 B", "stale"),
                                 ("probe_pilot.py", "absent_pilot.py", "missing")):
        status.write_text(original.replace(before, after))
        assert main(args) == 1
        assert json.loads(capsys.readouterr().out)[0]["row_state"] == state


def test_pilot_address_requires_complete_token_and_confined_path(tmp_path, capsys):
    status, reference = _fixture(tmp_path)
    pilot = tmp_path / "tools/compare/probe_pilot.py"
    args = ["--root", str(tmp_path), "--status", str(status), "--reference", str(reference)]
    for text in ("TARGET = 0x1000", "TARGET = 0x101", "# no address"):
        pilot.write_text(text)
        assert main(args) == 1
        assert json.loads(capsys.readouterr().out)[0]["row_state"] == "stale"
    for name in ("tools/compare/../../outside.py", "/tmp/probe_pilot.py"):
        status.write_text(status.read_text().replace("tools/compare/probe_pilot.py", name))
        assert main(args) == 1
        assert json.loads(capsys.readouterr().out)[0]["row_state"] == "missing"
        status, reference = _fixture(tmp_path)


def test_empty_status_cannot_pass(tmp_path, capsys):
    status = tmp_path / "empty.md"
    status.write_text("# No dispositions\n")
    assert main(["--status", str(status)]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_real_status_sizes_determinism_and_negative_controls(tmp_path):
    import subprocess
    import pytest

    reference = default_reference(ROOT)
    if not reference.is_file():
        pytest.skip("Premium reference unavailable; synthetic gates still run")
    status = ROOT / "docs/progress/STATUS.md"
    original = status.read_bytes()
    known = {0x6e7e60, 0x108c54, 0xeba84, 0xebb08, 0x108c8c, 0x73f0a4,
             0x739630, 0x72ad90, 0x710b50, 0x710b48, 0x710b58, 0x9d53f4,
             0x9dad14, 0xd2f98}

    def run(path):
        started = time.monotonic()
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                 "--status", str(path), "--reference", str(reference)],
                                capture_output=True, timeout=20, check=False)
        assert time.monotonic() - started < 20
        return result

    first, second = run(status), run(status)
    assert first.stdout == second.stdout and first.returncode == second.returncode
    rows = json.loads(first.stdout)
    assert {int(r["address"], 16) for r in rows} == known
    assert all(r["symtab_size"] == r["cited_size"] for r in rows)
    # The current repo has two missing address literals. Do not conceal those
    # failures just to satisfy the planner's incorrect "real STATUS exits 0".
    for row in rows:
        pilot = ROOT / row["pilot"]
        assert pilot.is_file()
        literals = {int(token, 16) for token in re.findall(rf"\b{HEX}\b", pilot.read_text())}
        assert row["row_state"] == ("ok" if int(row["address"], 16) in literals else "stale")
    assert first.returncode == int(any(r["row_state"] != "ok" for r in rows))

    # Mutate a disposable copy of the actual legacy STATUS, not a model of it.
    changed = tmp_path / "STATUS.md"
    text = original.decode()
    changed.write_text(text.replace("ELF VA `0x00108c54`, 56 B",
                                    "ELF VA `0x00108c58`, 56 B")
                          .replace("ELF VA `0x006e7e60`, 564 B",
                                   "ELF VA `0x006e7e60`, 568 B"))
    mutated = run(changed)
    assert mutated.returncode == 1
    by_address = {r["address"]: r for r in json.loads(mutated.stdout)}
    assert by_address["0x108c58"]["row_state"] == "stale"
    assert by_address["0x6e7e60"]["row_state"] == "stale"
    changed.write_text(text.replace("collada_matrix4_transpose_pilot.py", "absent_pilot.py"))
    missing = run(changed)
    assert missing.returncode == 1
    assert any(r["row_state"] == "missing" for r in json.loads(missing.stdout))
    assert status.read_bytes() == original


if __name__ == "__main__":
    raise SystemExit(main())
