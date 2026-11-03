"""Extract every defined function from an ELF .symtab with decoded references.

This is the first half of the source-correspondence pipeline: it turns the
Premium engine's non-stripped symbol table into a machine-queryable inventory of
``(address, size, ARM/Thumb, mangled name, demangled name, direct callees,
literal-pool data references)``.  The second half
(``build_source_correspondence.py``) matches those functions against the private
pre-release source tree.

Evidence rules:

* ARM/Thumb state is taken from the ``$a``/``$t``/``$d`` mapping symbols in the
  same section (a linked ELF has even ``st_value`` for Thumb functions), never
  guessed from the address.
* Direct callees come from decoded ``BL``/``BLX`` immediates only; an indirect
  call is recorded as ``<indirect>`` and never turned into a name.
* Data references come from decoded ``LDR`` literal-pool loads; the loaded word
  is treated as a pointer only when it lands inside a ``PT_LOAD`` segment, and
  named only when a symbol covers that address.

Nothing here reads or writes the private source tree.  Output is private
(``.cache/source_ref``) because it is derived from a proprietary binary; the
committed artifact is the metadata summary written by the correspondence tool.

    python -m tools.source_ref.extract_elf_symbols            # summary only
    python -m tools.source_ref.extract_elf_symbols --write    # + jsonl cache
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import subprocess
import sys

import capstone
import capstone.arm as arm
from elftools.elf.elffile import ELFFile

SCHEMA = 'nova3-elf-symbols/1'
DEFAULT_ELF = pathlib.Path('analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so')
DEFAULT_OUT = pathlib.Path('.cache/source_ref/elf_symbols.jsonl')
DEFAULT_SUMMARY = pathlib.Path('.cache/source_ref/elf_symbols_summary.json')


def demangle(names: list[str]) -> list[str]:
    """Batch demangle with GNU c++filt; unknown names are returned unchanged."""
    if not names:
        return []
    text = '\n'.join(names) + '\n'
    proc = subprocess.run(['c++filt'], input=text.encode(), capture_output=True,
                          check=False)
    if proc.returncode != 0:
        return list(names)
    out = proc.stdout.decode('utf-8', 'replace').split('\n')
    return out[:len(names)] if len(out) >= len(names) else list(names)


class Image:
    def __init__(self, path: pathlib.Path):
        self.handle = open(path, 'rb')
        self.elf = ELFFile(self.handle)
        self.sections = []
        for sec in self.elf.iter_sections():
            self.sections.append({
                'name': sec.name,
                'addr': sec['sh_addr'],
                'size': sec['sh_size'],
                'offset': sec['sh_offset'],
                'type': sec['sh_type'],
                'flags': sec['sh_flags'],
                'section': sec,
            })
        self.load_segments = []
        for seg in self.elf.iter_segments():
            if seg['p_type'] == 'PT_LOAD':
                self.load_segments.append((seg['p_vaddr'],
                                           seg['p_vaddr'] + seg['p_filesz']))
        self._raw = None

    def raw(self) -> bytes:
        if self._raw is None:
            self._raw = pathlib.Path(self.path_name).read_bytes()
        return self._raw

    def read(self, addr: int, size: int) -> bytes | None:
        for sec in self.sections:
            if sec['addr'] <= addr and addr + size <= sec['addr'] + sec['size']:
                start = sec['offset'] + (addr - sec['addr'])
                return self.raw()[start:start + size]
        return None

    def is_loaded(self, addr: int) -> bool:
        return any(lo <= addr < hi for lo, hi in self.load_segments)

    def section_of(self, addr: int):
        for sec in self.sections:
            if sec['addr'] <= addr < sec['addr'] + sec['size']:
                return sec
        return None


def build_symbol_tables(image: Image):
    symtab = image.elf.get_section_by_name('.symtab')
    if symtab is None:
        raise SystemExit('no .symtab: this ELF is stripped')
    funcs = []          # (index, symbol)
    by_addr = {}        # addr -> best name (STT_FUNC preferred)
    data_by_addr = {}   # addr -> STT_OBJECT name
    mapping = []        # (addr, mode, shndx)
    file_hints = {}     # symbol index -> preceding STT_FILE name
    current_file = None
    first_non_local = None
    for index, sym in enumerate(symtab.iter_symbols()):
        stype = sym['st_info']['type']
        if stype == 'STT_FILE':
            current_file = sym.name
            continue
        if first_non_local is None and sym['st_info']['bind'] != 'STB_LOCAL':
            first_non_local = index
        if stype == 'STT_FUNC':
            funcs.append((index, sym))
            # A STT_FILE only scopes the *local* symbols that follow it; the
            # global half of .symtab is grouped by section, so inheriting the
            # last FILE name there would be a fabricated hint.
            if current_file and (first_non_local is None or index < first_non_local):
                file_hints[index] = current_file
            if sym['st_value']:
                by_addr.setdefault(sym['st_value'], sym.name)
        elif stype == 'STT_OBJECT' and sym['st_value']:
            data_by_addr.setdefault(sym['st_value'], sym.name)
        elif sym.name in ('$a', '$t', '$d'):
            mapping.append((sym['st_value'], sym.name, sym['st_shndx']))
    mapping.sort()
    by_section: dict[int, tuple[list[int], list[str]]] = {}
    grouped: dict[int, list[tuple[int, str]]] = collections.defaultdict(list)
    for maddr, name, mshndx in mapping:
        grouped[mshndx].append((maddr, name))
    for mshndx, items in grouped.items():
        by_section[mshndx] = ([m for m, _ in items], [n for _, n in items])
    return funcs, by_addr, data_by_addr, by_section, file_hints


def build_plt_map(image: Image) -> dict[int, str]:
    """Map PLT entry addresses to the imported symbol they jump through.

    Without this, every libc call shows up as ``<addr 0x...>`` and a function's
    body evidence is lost exactly where the source calls ``open``/``read``/...
    """
    mapping: dict[int, str] = {}
    for section in image.elf.iter_sections():
        if section['sh_type'] not in ('SHT_REL', 'SHT_RELA'):
            continue
        target = image.elf.get_section(section['sh_info'])
        if target is None or target.name not in ('.plt', '.glue_7', '.glue_7t'):
            continue
        symtab = image.elf.get_section(section['sh_link'])
        if symtab is None:
            continue
        for rel in section.iter_relocations():
            index = rel['r_info_sym']
            if not index:
                continue
            symbol = symtab.get_symbol(index)
            if symbol and symbol.name:
                mapping[rel['r_offset']] = symbol.name
    return mapping


def mode_for(by_section, addr: int, shndx: int) -> str:
    import bisect
    entry = by_section.get(shndx)
    if not entry:
        return 'unknown'
    addrs, names = entry
    at = bisect.bisect_right(addrs, addr) - 1
    best = names[at] if at >= 0 else None
    if best == '$t':
        return 'thumb'
    if best in ('$a', '$d'):
        return 'arm' if best == '$a' else 'data'
    return 'unknown'


def decode_function(image: Image, addr: int, size: int, mode: str,
                    by_addr: dict[int, str], data_by_addr: dict[int, str],
                    data_symbols: list[tuple[int, str]],
                    plt_map: dict[int, str] | None = None):
    """Return (callees, data_refs, strings, n_instructions).

    ``callees`` keeps mangled names where the direct ``BL`` target is an exact
    symbol start and ``<addr 0x...>`` / ``<indirect>`` otherwise, so an unknown
    target never silently becomes a name.
    """
    empty = ([], [], [], 0)
    if size <= 0:
        return empty
    code = image.read(addr, size)
    if code is None:
        return empty
    md = capstone.Cs(capstone.CS_ARCH_ARM,
                     capstone.CS_MODE_ARM if mode == 'arm' else capstone.CS_MODE_THUMB)
    md.detail = True
    callees: list[str] = []
    data_refs: list[str] = []
    strings: list[str] = []
    movw: dict[int, int] = {}
    n = 0

    def resolve(value: int) -> None:
        if not value:
            return
        name = data_by_addr.get(value)
        if name:
            data_refs.append(name)
            return
        sec = image.section_of(value)
        if sec is None or not image.is_loaded(value):
            return
        if sec['name'].startswith('.rodata') or sec['name'] in ('.data.rel.ro',):
            raw = image.read(value, 160)
            if raw:
                end = raw.find(b'\0')
                if end >= 4:
                    try:
                        text = raw[:end].decode('ascii')
                    except UnicodeDecodeError:
                        text = None
                    if text and text.isprintable():
                        strings.append(text)
                        return
        # nearest preceding named data symbol inside the same section
        import bisect
        addrs = [a for a, _ in data_symbols]
        at = bisect.bisect_right(addrs, value) - 1
        if at >= 0:
            daddr, dname = data_symbols[at]
            dsec = image.section_of(daddr)
            if dsec is not None and dsec['name'] == sec['name'] and value - daddr < 4096:
                data_refs.append(dname if value == daddr else f'{dname}+0x{value-daddr:x}')
                return
        data_refs.append(f'<ptr 0x{value:08x} in {sec["name"]}>')

    def literal_word(at: int) -> int | None:
        raw = image.read(at, 4)
        if not raw or len(raw) != 4:
            return None
        return int.from_bytes(raw, 'little')

    for insn in md.disasm(code, addr):
        n += 1
        mnem = insn.mnemonic
        ops = insn.operands
        if mnem in ('bl', 'blx') and ops:
            op = ops[0]
            if op.type == arm.ARM_OP_IMM:
                target = op.imm & ~1
                name = by_addr.get(target)
                if name is None and plt_map:
                    name = plt_map.get(target)
                callees.append(name or f'<addr 0x{target:08x}>')
            else:
                callees.append('<indirect>')
        elif mnem in ('ldr', 'ldr.w') and len(ops) == 2:
            dest, mem = ops
            if (dest.type == arm.ARM_OP_REG and mem.type == arm.ARM_OP_MEM
                    and mem.mem.base == arm.ARM_REG_PC
                    and mem.mem.index == arm.ARM_REG_INVALID):
                word = literal_word(insn.address + 8 + mem.mem.disp)
                if word is not None:
                    resolve(word)
        elif mnem == 'movw' and len(ops) == 2 and ops[1].type == arm.ARM_OP_IMM:
            movw[ops[0].reg] = ops[1].imm & 0xffff
        elif mnem == 'movt' and len(ops) == 2 and ops[1].type == arm.ARM_OP_IMM:
            low = movw.pop(ops[0].reg, None)
            if low is not None:
                resolve((ops[1].imm << 16) | low)
    return callees, data_refs, strings, n


def extract(elf_path: pathlib.Path) -> tuple[list[dict], dict]:
    image = Image(elf_path)
    image.path_name = str(elf_path)
    funcs, by_addr, data_by_addr, mapping, file_hints = build_symbol_tables(image)
    plt_map = build_plt_map(image)
    data_symbols = sorted(data_by_addr.items())
    mangled = [sym.name for _, sym in funcs]
    demangled = demangle(mangled)
    rows = []
    for (index, sym), dname in zip(funcs, demangled):
        addr = sym['st_value']
        size = sym['st_size']
        mode = mode_for(mapping, addr, sym['st_shndx'])
        if mode == 'data':
            mode = 'unknown'
        callees, data_refs, strings, ninstr = decode_function(
            image, addr, size, mode, by_addr, data_by_addr, data_symbols, plt_map)
        rows.append({
            'address': addr,
            'size': size,
            'mode': mode,
            'bind': sym['st_info']['bind'],
            'mangled': sym.name,
            'demangled': dname,
            'file_hint': file_hints.get(index),
            'callees': callees,
            'data_refs': data_refs,
            'strings': strings,
            'instructions': ninstr,
        })
    summary = {
        'schema': SCHEMA,
        'elf': str(elf_path),
        'functions': len(rows),
        'by_mode': dict(collections.Counter(r['mode'] for r in rows)),
        'by_bind': dict(collections.Counter(r['bind'] for r in rows)),
        'named': sum(1 for r in rows if r['mangled']),
        'demangled': sum(1 for r in rows if r['demangled'] != r['mangled']),
        'with_file_hint': sum(1 for r in rows if r['file_hint']),
        'sized': sum(1 for r in rows if r['size']),
        'total_code_bytes': sum(r['size'] for r in rows),
        'total_instructions': sum(r['instructions'] for r in rows),
        'with_callees': sum(1 for r in rows if r['callees']),
        'with_data_refs': sum(1 for r in rows if r['data_refs']),
        'with_strings': sum(1 for r in rows if r['strings']),
        'distinct_file_hints': len({r['file_hint'] for r in rows if r['file_hint']}),
        'plt_symbols': len(plt_map),
    }
    image.handle.close()
    return rows, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', type=pathlib.Path, default=DEFAULT_ELF)
    parser.add_argument('--out', type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument('--summary', type=pathlib.Path, default=DEFAULT_SUMMARY)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args(argv)
    rows, summary = extract(args.elf)
    if args.limit:
        rows = rows[:args.limit]
        summary['truncated_to'] = args.limit
    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + '\n')
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n',
                                encoding='utf-8')
        print(f'wrote {args.out} ({len(rows)} rows) and {args.summary}')
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
