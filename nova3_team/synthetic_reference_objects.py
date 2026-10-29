"""Offline, explicit-TU staging of SYNTHETIC_REFERENCE objects, never original .o.

Only owner-selected linked byte windows are emitted. No instruction decoding,
relocation reconstruction, TU inference, linking, or matching promotion occurs.
All artifacts (including ASM and tool logs) stay in a new private checkout
.cache directory. See docs/progress/SYNTHETIC_REFERENCE_OBJECTS.md for schemas.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys

from .object_compare import CompareError, _span, _string, candidate_window
from .relocation_probe import ArmElf, ProbeError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nova3.synthetic-reference/1"
MANIFEST_SCHEMA = "nova3.explicit-tu/1"
PROFILE_SCHEMA = "nova3.synthetic-assembler/1"
LABELS = ["SYNTHETIC_REFERENCE", "ORIGINAL_O_NOT_RECOVERED"]
MAX_INPUT = 64 * 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_FUNCTIONS = 128
MAX_GROUPS = 64
MAX_WINDOW = 64 * 1024
MAX_TOTAL = 4 * 1024 * 1024
MAX_SYMBOLS = 250_000
MAX_RELOCATIONS = 250_000
MAX_NAME_CHARS = 16 * 1024 * 1024
MAX_STAGED_RELOCATIONS = 8192
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class StagingError(ValueError):
    """Refused unsafe, ambiguous, unsupported, or unpinned input."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fields(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= value.keys()
            or value.keys() - set(required) - set(optional)):
        raise StagingError("missing or unknown schema fields")


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StagingError("invalid identifier")
    return value


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise StagingError("a full lowercase SHA-256 pin is required")
    return value


def _uint(value):
    if type(value) is not int or not 0 <= value < 1 << 32:
        raise StagingError("expected unsigned ARM32 integer")
    return value


def _absolute(path: Path) -> Path:
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise StagingError("parent traversal is refused")
    return Path(os.path.abspath(path))


def _read(path: Path, limit=MAX_INPUT) -> bytes:
    # Nonblocking avoids FIFO hangs; NOFOLLOW refuses final-component symlinks.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise StagingError("input must be a bounded regular file")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (len(data) != before.st_size or len(data) > limit
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                   (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise StagingError("input changed while reading")
        return data


def _executable_digest(path: Path) -> str:
    """Hash a pinned tool without retaining a potentially large compiler in RAM."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 512 * 1024 * 1024:
            raise StagingError("assembler must be a bounded regular executable")
        h, total = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > before.st_size:
                raise StagingError("assembler changed while hashing")
            h.update(chunk)
        after = os.fstat(stream.fileno())
        if (total != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_mtime_ns, after.st_ctime_ns)):
            raise StagingError("assembler changed while hashing")
        return h.hexdigest()


def _json(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise StagingError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise StagingError("nonfinite JSON value")

    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StagingError("invalid JSON input") from exc
    if not isinstance(result, dict):
        raise StagingError("JSON object required")
    return result


def _symbols(elf: ArmElf) -> dict[int, list[dict]]:
    """Both linked SYMTAB and DYNSYM; keep exact table/index identities."""
    tables, total, name_chars = {}, 0, 0
    for sec in elf.sections:
        if sec["type"] not in (2, 11):
            continue
        total += sec["size"] // 16
        if sec["entsize"] != 16 or sec["size"] % 16 or total > MAX_SYMBOLS:
            raise StagingError("malformed or oversized symbol tables")
        if sec["link"] >= len(elf.sections) or elf.sections[sec["link"]]["type"] != 3:
            raise StagingError("invalid symbol string table")
        strings = elf.sections[sec["link"]]
        names = _span(elf.data, strings["offset"], strings["size"])
        rows = []
        for i in range(sec["size"] // 16):
            name, value, size, info, other, shndx = struct.unpack(
                "<IIIBBH", _span(elf.data, sec["offset"] + 16 * i, 16))
            text = _string(names, name)
            name_chars += len(text)
            if name_chars > MAX_NAME_CHARS:
                raise StagingError("symbol names exceed total text bound")
            rows.append(dict(name=text, value=value, size=size,
                             type=info & 15, section_index=shndx, binding=info >> 4,
                             visibility=other, table_index=sec["index"], symbol_index=i))
        tables[sec["index"]] = rows
    return tables


def _loads(elf: ArmElf) -> list[dict]:
    if elf.kind != 3 or elf.phsize != 32 or not 0 < elf.phnum <= 4096:
        raise StagingError("reference must be linked ET_DYN with PT_LOAD headers")
    loads = []
    for i in range(elf.phnum):
        typ, offset, va, _, filesz, memsz, flags, align = struct.unpack(
            "<8I", _span(elf.data, elf.phoff + 32 * i, 32))
        if typ != 1:
            continue
        if (filesz > memsz or va + memsz > 1 << 32
                or (align > 1 and (align & (align - 1) or (va - offset) % align))):
            raise StagingError("invalid PT_LOAD range or alignment")
        _span(elf.data, offset, filesz)
        loads.append(dict(index=i, address=va, file_offset=offset,
                          file_size=filesz, memory_size=memsz, flags=flags))
    return loads


def _relocations(elf: ArmElf, symbols: dict) -> list[dict]:
    """Observed REL/RELA r_offset is a VA in ET_DYN, never a new .o offset.

    Dynamic-only/packed/RELR metadata is refused rather than silently omitted.
    REL implicit addends and original prelink addends remain UNKNOWN.
    """
    rows = []
    sections = elf.sections
    for sec in sections:
        if sec["type"] in (19, 0x60000001, 0x60000002, 0x6fffff00):
            raise StagingError("packed or RELR relocations are unsupported")
        if sec["type"] == 6:  # SHT_DYNAMIC; require conventional tables below.
            if sec["entsize"] != 8 or sec["size"] % 8:
                raise StagingError("malformed dynamic table")
            tags = {}
            terminated = False
            for pos in range(sec["offset"], sec["offset"] + sec["size"], 8):
                tag, value = struct.unpack("<II", _span(elf.data, pos, 8))
                if tag == 0:
                    terminated = True
                    break
                if tag in (35, 36, 37, 0x6000000f, 0x60000010,
                           0x60000011, 0x60000012, 0x6fffe000, 0x6fffe001, 0x6fffe003):
                    raise StagingError("packed or RELR dynamic relocations unsupported")
                if tag in (7, 8, 9, 17, 18, 19, 20, 23, 2):
                    if tag in tags:
                        raise StagingError("duplicate relocation dynamic tag")
                    tags[tag] = value
            if not terminated or (19 in tags and tags[19] != 8) or (9 in tags and tags[9] != 12):
                raise StagingError("unterminated dynamic table or invalid relocation entry width")
            for ptr, length, typ in ((17, 18, 9), (7, 8, 4), (23, 2, None)):
                if ptr not in tags and length not in tags:
                    continue
                if ptr not in tags or length not in tags:
                    raise StagingError("incomplete dynamic relocation table")
                kind = typ if typ else {17: 9, 7: 4}.get(tags.get(20))
                if not kind or not any(s["type"] == kind and s["address"] == tags[ptr]
                                       and s["size"] == tags[length] for s in sections):
                    raise StagingError("dynamic relocations lack exact section coverage")
        if sec["type"] not in (4, 9):
            continue
        width = 12 if sec["type"] == 4 else 8
        if (sec["entsize"] != width or sec["size"] % width
                or len(rows) + sec["size"] // width > MAX_RELOCATIONS
                or sec["link"] not in symbols or sec["info"] >= len(sections)):
            raise StagingError("malformed or oversized relocation table")
        table = symbols[sec["link"]]
        for i in range(sec["size"] // width):
            pos = sec["offset"] + i * width
            va, info = struct.unpack("<II", _span(elf.data, pos, 8))
            if info >> 8 >= len(table):
                raise StagingError("relocation symbol index outside table")
            if sec["info"]:
                target = sections[sec["info"]]
                if not target["address"] <= va < target["address"] + target["size"]:
                    raise StagingError("relocation VA outside declared target section")
            rows.append(dict(table_index=sec["index"], record_index=i,
                             record_file_offset=pos, linked_address=va, type=info & 255,
                             format="RELA" if width == 12 else "REL",
                             symbol=table[info >> 8],
                             observed_explicit_addend=(struct.unpack("<i", _span(elf.data, pos + 8, 4))[0]
                                                       if width == 12 else None),
                             original_prelink_addend="UNKNOWN",
                             relocation_width_and_effect="NOT_INTERPRETED"))
    # PT_DYNAMIC without an identical SHT_DYNAMIC would hide metadata.
    for i in range(elf.phnum):
        typ, offset, _, _, size, _, _, _ = struct.unpack(
            "<8I", _span(elf.data, elf.phoff + 32 * i, 32))
        if typ == 2 and not any(s["type"] == 6 and s["offset"] == offset
                                and s["size"] == size for s in sections):
            raise StagingError("PT_DYNAMIC lacks exact section coverage")
    return rows


def _plan(data: bytes, manifest: dict, expected_sha256: str, edition: str):
    _hash(expected_sha256)
    _fields(manifest, ("schema", "reference", "groups"))
    _fields(manifest["reference"], ("sha256", "edition"))
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise StagingError("unsupported TU manifest schema")
    if (edition not in ("premium", "freemium", "cyrus-donor", "synthetic")
            or manifest["reference"]["edition"] != edition):
        raise StagingError("edition label mismatch")
    if _hash(manifest["reference"]["sha256"]) != expected_sha256 or digest(data) != expected_sha256:
        raise StagingError("reference SHA-256 mismatch")
    elf = ArmElf(data)
    if elf.sections[0]["type"] != 0 or struct.unpack_from("<I", data, 36)[0] & 0x00c00000:
        raise StagingError("invalid section zero or unsupported ARM BE8/LE8 flags")
    if any(s["address"] + s["size"] > 1 << 32 for s in elf.sections):
        raise StagingError("section address range overflows ARM32")
    loads = _loads(elf)
    symbols = _symbols(elf)
    named = {}
    for table in symbols.values():
        for s in table:
            if s["type"] == 2 and s["section_index"]:
                named.setdefault(s["name"], []).append(s)
    groups = manifest["groups"]
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS:
        raise StagingError("invalid or oversized group list")
    rows, group_ids, total = [], set(), 0
    for group in groups:
        _fields(group, ("id", "functions"))
        gid = _identifier(group["id"])
        if gid in group_ids:
            raise StagingError("duplicate group id")
        group_ids.add(gid)
        if not isinstance(group["functions"], list) or not group["functions"]:
            raise StagingError("each group requires explicit functions")
        for fn in group["functions"]:
            _fields(fn, ("id", "address", "size", "mode", "symbol"), ("alias_of", "confidence"))
            fid = _identifier(fn["id"])
            address, size = _uint(fn["address"]), _uint(fn["size"])
            mode = fn["mode"]
            if not isinstance(mode, str) or mode not in ("arm", "thumb"):
                raise StagingError("unknown ARM/Thumb mode")
            alignment = 4 if mode == "arm" else 2
            if (not 0 < size <= MAX_WINDOW or address % alignment or size % alignment
                    or address + size > 1 << 32):
                raise StagingError("invalid, unaligned, or oversized function window")
            if "confidence" in fn and (type(fn["confidence"]) not in (float, int)
                    or not math.isfinite(fn["confidence"]) or not 0 <= fn["confidence"] <= 1):
                raise StagingError("confidence must be finite in [0,1]")
            _fields(fn["symbol"], ("kind", "name"))
            identity = fn["symbol"]
            if (identity["kind"] not in ("elf", "owner") or not isinstance(identity["name"], str)
                    or not 1 <= len(identity["name"]) <= 4096 or "\x00" in identity["name"]):
                raise StagingError("invalid symbol identity")
            observations = named.get(identity["name"], [])
            if identity["kind"] == "elf" and not observations:
                raise StagingError("ELF function identity absent")
            offset = elf.mapped_offset(address, size)
            overlaps = [p for p in loads if address < p["address"] + p["memory_size"]
                        and p["address"] < address + size]
            if len(overlaps) != 1:
                raise StagingError("ambiguous overlapping PT_LOAD mappings")
            for s in observations:
                index = s["section_index"]
                if (s["value"] != (address | (mode == "thumb")) or s["size"] != size
                        or not 0 < index < len(elf.sections)):
                    raise StagingError("symbol identity disagrees with explicit window")
                sec = elf.sections[index]
                delta = address - sec["address"]
                if (sec["type"] != 1 or not sec["flags"] & 4 or delta < 0
                        or delta + size > sec["size"] or sec["offset"] + delta != offset):
                    raise StagingError("symbol section and PT_LOAD disagree")
            total += size
            if len(rows) >= MAX_FUNCTIONS or total > MAX_TOTAL:
                raise StagingError("function count or total bytes exceeds bound")
            rows.append(dict(fn, group_id=gid, file_offset=offset,
                             segment_index=overlaps[0]["index"],
                             window_sha256=digest(data[offset:offset + size]),
                             symbol_evidence="ELF_EXACT" if observations else "OWNER_ASSERTED_ONLY",
                             synthetic_symbol="synthetic_" + fid))
    ids = {r["id"]: r for r in rows}
    if len(ids) != len(rows):
        raise StagingError("duplicate function id")
    for r in rows:
        if "alias_of" in r:
            target = ids.get(_identifier(r["alias_of"]))
            if (not target or "alias_of" in target or target is r or
                    any(r[k] != target[k] for k in ("address", "size", "mode", "group_id"))):
                raise StagingError("detached, chained, or inconsistent alias")
    canonical = sorted((r for r in rows if "alias_of" not in r), key=lambda r: r["address"])
    for a, b in zip(canonical, canonical[1:]):
        if a["address"] + a["size"] > b["address"]:
            raise StagingError("overlapping windows require an exact alias_of rule")
    by_file = sorted(canonical, key=lambda r: r["file_offset"])
    for a, b in zip(by_file, by_file[1:]):
        if a["file_offset"] + a["size"] > b["file_offset"]:
            raise StagingError("overlapping file windows at distinct linked addresses")
    relocations = _relocations(elf, symbols)
    staged_records = sum(sum(r["address"] <= rel["linked_address"] < r["address"] + r["size"]
                             for rel in relocations) for r in rows)
    if staged_records > MAX_STAGED_RELOCATIONS:
        raise StagingError("selected relocation worksheet records exceed bound")
    return sorted(rows, key=lambda r: (r["group_id"], r["address"], r["id"])), relocations


def _assembly(row: dict, code: bytes) -> str:
    name = row["synthetic_symbol"]
    lines = ["/* SYNTHETIC_REFERENCE ORIGINAL_O_NOT_RECOVERED; PRIVATE RESEARCH ONLY. */",
             "/* Linked bytes only; missing prelink relocations UNKNOWN. DO NOT SHIP. */",
             ".syntax unified", ".arch armv7-a", ".thumb" if row["mode"] == "thumb" else ".arm",
             '.section .text.' + name + ', "ax", %progbits',
             ".balign 2" if row["mode"] == "thumb" else ".balign 4",
             ".global " + name, ".type " + name + ", %function"]
    if row["mode"] == "thumb":
        lines.append(".thumb_func")
    lines.append(name + ":")
    for i in range(0, len(code), 16):
        lines.append(".byte " + ", ".join("0x%02x" % b for b in code[i:i + 16]))
    lines.extend([".size " + name + ", .-" + name,
                  '.section .comment, "MS", %progbits, 1',
                  '.asciz "SYNTHETIC_REFERENCE ORIGINAL_O_NOT_RECOVERED"', ""])
    return "\n".join(lines)


@contextmanager
def _private_output(output: Path):
    """Create exclusively through no-follow directory FDs; roll back failures."""
    output = _absolute(output)
    cache = ROOT / ".cache"
    if not output.is_relative_to(cache) or output == cache:
        raise StagingError("output must be a new directory under checkout .cache")
    # Every directory component below the checkout is opened without following links.
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = []
    leaf_created = False
    try:
        parts = output.relative_to(ROOT).parts
        for i, part in enumerate(parts):
            parent = os.dup(fd)
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
                created.append((parent, part))
                leaf_created = i == len(parts) - 1
            except FileExistsError:
                os.close(parent)
                if i == len(parts) - 1:
                    raise StagingError("output already exists; overwrites are refused") from None
            except OSError:
                os.close(parent)
                raise
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield output
    except BaseException:
        # Only the exclusively created leaf can contain our generated files.
        if leaf_created:
            shutil.rmtree(output)
        for parent, part in reversed(created):
            try:
                os.rmdir(part, dir_fd=parent)
            except OSError:
                pass
        raise
    finally:
        os.close(fd)
        for parent, _ in created:
            os.close(parent)


def _write(root: Path, relative: str, data: bytes):
    path = root / relative
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def _encode(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


def _assembler(profile: dict) -> dict:
    _fields(profile, ("schema", "kind", "executable", "sha256", "version_sha256"))
    if profile["schema"] != PROFILE_SCHEMA or profile["kind"] not in ("gnu-as", "clang"):
        raise StagingError("unsupported assembler profile")
    if not isinstance(profile["executable"], str) or not Path(profile["executable"]).is_absolute():
        raise StagingError("assembler executable must be an absolute path")
    executable = _absolute(Path(profile["executable"]))
    _hash(profile["sha256"])
    _hash(profile["version_sha256"])
    # Content and version are separate pins; a version string alone is insufficient.
    if _executable_digest(executable) != profile["sha256"]:
        raise StagingError("assembler executable SHA-256 mismatch")
    result = subprocess.run([str(executable), "--version"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=5, check=False, env=_environment())
    if result.returncode or digest(result.stdout) != _hash(profile["version_sha256"]):
        raise StagingError("assembler version pin mismatch")
    flags = (["-EL", "-march=armv7-a"] if profile["kind"] == "gnu-as" else
             ["--target=armv7a-none-eabi", "-march=armv7-a", "-x", "assembler", "-c"])
    return dict(profile, flags=flags)


def _environment():
    return {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": "0"}


def stage(reference: Path, manifest: dict, output: Path, *, expected_sha256: str,
          edition: str, group_assembly=False, assemble=False, assembler_profile=None) -> dict:
    """Stage a bounded explicit manifest. Only --assemble authorizes tool execution."""
    if type(assemble) is not bool or type(group_assembly) is not bool:
        raise StagingError("boolean staging options required")
    if assemble != (assembler_profile is not None):
        raise StagingError("assembly requires both explicit enable and pinned profile")
    data = _read(Path(reference))
    rows, relocations = _plan(data, manifest, expected_sha256, edition)
    # Validate all input windows before creating scratch or executing a tool.
    output = _absolute(output)
    if not output.is_relative_to(ROOT / ".cache") or output == ROOT / ".cache":
        raise StagingError("output must be a new directory under checkout .cache")
    report = dict(schema=SCHEMA, labels=LABELS, reference_sha256=expected_sha256,
                  edition_label=edition, edition_evidence="OWNER_LABEL_BOUND_TO_SHA_ONLY",
                  manifest_sha256=digest(_encode(manifest)), functions=rows,
                  grouping="EXPLICIT_OWNER_HYPOTHESIS_NOT_PROVEN_ORIGINAL_TU",
                  original_objects_recovered=False, original_prelink_relocations="UNKNOWN",
                  source_match="NOT MATCHED", whole_library_match="NOT MATCHED",
                  assembler=None, surviving_section_relocations=len(relocations), objects=[])
    with _private_output(output) as out:
        profile = _assembler(assembler_profile) if assemble else None
        report["assembler"] = profile
        units = []
        groups = {}
        for row in rows:
            fid = row["id"]
            source = "asm/" + fid + ".s"
            code = data[row["file_offset"]:row["file_offset"] + row["size"]]
            asm = _assembly(row, code)
            _write(out, source, asm.encode("ascii"))
            groups.setdefault(row["group_id"], []).append(asm)
            observed = [dict(r, function_offset=r["linked_address"] - row["address"])
                        for r in relocations if row["address"] <= r["linked_address"] < row["address"] + row["size"]]
            worksheet = dict(schema=SCHEMA, labels=LABELS, function_id=fid,
                             reference_sha256=expected_sha256, records=observed,
                             coverage="SURVIVING_LINKED_REL_RELA_RECORD_STARTS_ONLY",
                             missing_prelink_relocations="UNKNOWN_NOT_RECONSTRUCTED",
                             implicit_addends="UNKNOWN_NOT_DECODED",
                             boundary_straddling_effects="UNKNOWN_WIDTHS_NOT_DECODED",
                             status="OBSERVED_RECORDS_ONLY" if observed else "NO_SURVIVING_RECORD_START_IN_WINDOW",
                             synthesized_object_relocations="NONE_EMITTED")
            _write(out, "relocations/" + fid + ".json", _encode(worksheet))
            target = "reference/" + fid + ".o"
            units.append(dict(name="SYNTHETIC_REFERENCE/" + row["group_id"] + "/" + fid, target_path=target,
                              base_path="candidate/" + fid + "/build.o", metadata=dict(complete=False)))
            if profile:
                command = [profile["executable"], *profile["flags"], source, "-o", target]
                _write(out, "logs/" + fid + ".txt", b"SYNTHETIC_REFERENCE ORIGINAL_O_NOT_RECOVERED\n")
                (out / "reference").mkdir(mode=0o700, exist_ok=True)
                with (out / ("logs/" + fid + ".txt")).open("ab") as log:
                    result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                            env=_environment(), timeout=30, check=False)
                if result.returncode:
                    raise StagingError("assembler failed; private staging rolled back")
                objpath = out / target
                if objpath.is_symlink():
                    raise StagingError("assembler produced symlink")
                obj = _read(objpath)
                if ArmElf(obj).kind != 1:
                    raise StagingError("assembler did not produce ARM EABI5 ET_REL")
                window = candidate_window(obj, row["synthetic_symbol"])
                if (window["bytes"] != code or window["thumb"] != (row["mode"] == "thumb")
                        or window["relocation_offsets"]):
                    raise StagingError("synthetic object did not preserve the selected byte window")
                os.chmod(objpath, 0o600)
                report["objects"].append(dict(function_id=fid, path=target, sha256=digest(obj),
                                              command=command, returncode=result.returncode,
                                              asm_sha256=digest(asm.encode("ascii")), labels=LABELS))
        if profile and _executable_digest(Path(profile["executable"])) != profile["sha256"]:
            raise StagingError("assembler changed during build; staging rolled back")
        if group_assembly:
            for gid, sources in sorted(groups.items()):
                _write(out, "groups/" + gid + ".s", "\n".join(sources).encode("ascii"))
        layout = dict(schema=SCHEMA, labels=LABELS, reference_sha256=expected_sha256,
                      original_link_script="UNKNOWN", relink_to_original_sha="NOT_ESTABLISHED",
                      positions=[{k: r[k] for k in ("id", "group_id", "address", "size", "mode", "file_offset", "segment_index")}
                                 for r in rows],
                      warning="Linked positions only. No linker script, gap bytes, or original .o layout inferred.")
        _write(out, "link-layout.json", _encode(layout))
        _write(out, "objdiff.json", _encode(dict(build_base=False, build_target=False,
                                                units=units)))
        _write(out, "manifest.json", _encode(report))
        _write(out, "README.txt", (" ".join(LABELS) + "\nPRIVATE RESEARCH ONLY. DO NOT COMMIT OR SHIP.\n"
               "NOT MATCHED. Linked bytes retain resolved addresses. Missing prelink relocations UNKNOWN.\n"
               "objdiff.json is a template; candidate objects do not exist yet. Auto-build is disabled.\n").encode())
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--expected-sha256", required=True)
    p.add_argument("--edition", choices=("premium", "freemium", "cyrus-donor", "synthetic"), required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--group-assembly", action="store_true")
    p.add_argument("--assemble", action="store_true")
    p.add_argument("--assembler-profile", type=Path)
    args = p.parse_args(argv)
    try:
        profile = _json(_read(args.assembler_profile, MAX_JSON)) if args.assembler_profile else None
        stage(args.reference, _json(_read(args.manifest, MAX_JSON)), args.out,
              expected_sha256=args.expected_sha256, edition=args.edition,
              group_assembly=args.group_assembly, assemble=args.assemble, assembler_profile=profile)
    except StagingError as exc:
        # All StagingError messages are static, source-owned refusal descriptions.
        print("synthetic reference: refused; " + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, CompareError, ProbeError, subprocess.SubprocessError, RecursionError):
        # No paths, symbol names, byte streams, tool stderr or malformed payloads in public logs.
        print("synthetic reference: refused; validate local inputs, pins, tool and new private output", file=sys.stderr)
        return 2
    print("SYNTHETIC_REFERENCE; ORIGINAL_O_NOT_RECOVERED; NOT MATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
