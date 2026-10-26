"""In-memory symbol assertions and reviewable, atomic JSON updates.

Keys are binary SHA-256 + unsigned 64-bit *virtual byte address*, never a file
offset, load-biased runtime address, name or Thumb pointer. Callers must resolve
those distinctions first; no state bits are silently cleared. Types are exact
caller assertions, not parsed C/C++: typedef equivalence, ABI inference, nested
type resolution and behavioural/machine-code matching are outside this module.

Usage: registry = SymbolRegistry(); update = registry.propose([SymbolRecord(
    SymbolKey("a" * 64, 0x1000), "function", "armv7-a", mode="thumb",
    names=("synthetic_function",), evidence=("synthetic fixture",))])
Review update.to_json(), then registry.apply(update). Persist registry.to_json()
using the caller's own approved storage policy. This module does no I/O.
Conflicts reject the entire batch. Unknown (None) layout/signature/mode may be
refined; known assertions cannot be replaced or removed through an update.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Iterable


SCHEMA_VERSION = 1


def _text(value: str, field: str) -> None:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"{field} must be nonempty, trimmed text without controls")


def _uint(value: int, field: str, *, positive: bool = False) -> None:
    if type(value) is not int or not (int(positive) <= value < 2**64):
        raise ValueError(f"{field} must be an unsigned 64-bit integer")


def _texts(value: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple of text")
    for item in value:
        _text(item, field)
    return tuple(sorted(set(value)))


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                      allow_nan=False) + "\n"


@dataclass(frozen=True, order=True)
class SymbolKey:
    binary_sha256: str
    address: int

    def __post_init__(self) -> None:
        if not isinstance(self.binary_sha256, str) or not re.fullmatch(
            r"[a-fA-F0-9]{64}", self.binary_sha256
        ):
            raise ValueError("binary_sha256 must be exactly 64 hex digits")
        object.__setattr__(self, "binary_sha256", self.binary_sha256.lower())
        _uint(self.address, "address")

    @property
    def identifier(self) -> str:
        return f"sha256:{self.binary_sha256}:va:0x{self.address:016x}"

    @classmethod
    def parse(cls, identifier: str) -> SymbolKey:
        if not isinstance(identifier, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}:va:0x[0-9a-f]{16}", identifier
        ):
            raise ValueError("expected canonical SHA-256/virtual-address identifier")
        return cls(identifier[7:71], int(identifier[77:], 16))


@dataclass(frozen=True)
class FieldLayout:
    name: str
    type_name: str
    offset: int
    size: int

    def __post_init__(self) -> None:
        _text(self.name, "field name")
        _text(self.type_name, "field type")
        _uint(self.offset, "field offset")
        _uint(self.size, "field size", positive=True)

    def to_dict(self) -> dict:
        return dict(name=self.name, type_name=self.type_name,
                    offset=self.offset, size=self.size)


@dataclass(frozen=True)
class TypeLayout:
    """Complete byte layout of a named struct/union in this binary's scope.

    Use qualified names to distinguish namespaces/CUs. Bitfields, inheritance,
    flexible arrays and zero-size extensions are intentionally unsupported.
    Packed structs are allowed; field alignment cannot be inferred from names.
    """

    name: str
    size: int
    alignment: int
    fields: tuple[FieldLayout, ...] = ()
    kind: str = "struct"

    def __post_init__(self) -> None:
        _text(self.name, "type name")
        _uint(self.size, "type size", positive=True)
        _uint(self.alignment, "type alignment", positive=True)
        if self.alignment & (self.alignment - 1) or self.size % self.alignment:
            raise ValueError("alignment must be a power of two and divide size")
        if self.kind not in ("struct", "union"):
            raise ValueError("layout kind must be struct or union")
        if not isinstance(self.fields, tuple) or any(
            not isinstance(f, FieldLayout) for f in self.fields
        ):
            raise ValueError("fields must be a tuple of FieldLayout")
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("duplicate field names")
        fields = tuple(sorted(self.fields, key=lambda f: (f.offset, f.name)))
        end = 0
        for field in fields:
            if field.offset + field.size > self.size:
                raise ValueError("field exceeds layout size")
            if self.kind == "union" and field.offset != 0:
                raise ValueError("union fields must start at zero")
            if self.kind == "struct" and field.offset < end:
                raise ValueError("overlapping struct fields")
            end = field.offset + field.size
        object.__setattr__(self, "fields", fields)

    def to_dict(self) -> dict:
        return dict(name=self.name, size=self.size, alignment=self.alignment,
                    kind=self.kind, fields=[f.to_dict() for f in self.fields])


@dataclass(frozen=True)
class Signature:
    return_type: str
    parameter_types: tuple[str, ...]
    calling_convention: str
    variadic: bool = False

    def __post_init__(self) -> None:
        _text(self.return_type, "return type")
        _text(self.calling_convention, "calling convention")
        # Parameter order and repeated types are significant.
        if not isinstance(self.parameter_types, tuple):
            raise ValueError("parameter_types must be a tuple")
        for item in self.parameter_types:
            _text(item, "parameter type")
        if type(self.variadic) is not bool:
            raise ValueError("variadic must be a bool")

    def to_dict(self) -> dict:
        return dict(return_type=self.return_type,
                    parameter_types=list(self.parameter_types),
                    calling_convention=self.calling_convention, variadic=self.variadic)


@dataclass(frozen=True)
class SymbolRecord:
    key: SymbolKey
    kind: str
    architecture: str
    mode: str | None = None
    names: tuple[str, ...] = ()
    layout: TypeLayout | None = None
    signature: Signature | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, SymbolKey):
            raise ValueError("key must be a SymbolKey")
        if self.kind not in ("function", "data"):
            raise ValueError("kind must be function or data")
        _text(self.architecture, "architecture")
        if self.mode is not None:
            _text(self.mode, "instruction mode")
            if self.kind != "function":
                raise ValueError("data symbols cannot have an instruction mode")
        if self.mode in ("arm", "thumb") and self.key.address % (
            4 if self.mode == "arm" else 2
        ):
            raise ValueError("ARM/Thumb code requires an aligned virtual address")
        if self.layout is not None and not isinstance(self.layout, TypeLayout):
            raise ValueError("layout must be TypeLayout or None")
        if self.signature is not None and (
            not isinstance(self.signature, Signature) or self.kind != "function"
        ):
            raise ValueError("only functions may have a Signature")
        for field in ("names", "evidence"):
            object.__setattr__(self, field, _texts(getattr(self, field), field))
        if not self.evidence:
            raise ValueError("at least one caller-supplied evidence reference is required")

    def to_dict(self) -> dict:
        return dict(id=self.key.identifier, kind=self.kind,
                    architecture=self.architecture, mode=self.mode,
                    names=list(self.names), evidence=list(self.evidence),
                    layout=None if self.layout is None else self.layout.to_dict(),
                    signature=None if self.signature is None else self.signature.to_dict())


@dataclass(frozen=True)
class Conflict:
    symbol_id: str
    path: str
    existing: object
    proposed: object
    other_symbol_id: str | None = None

    def to_dict(self) -> dict:
        def value(item):
            return item.to_dict() if hasattr(item, "to_dict") else item
        return dict(symbol_id=self.symbol_id, path=self.path,
                    existing=value(self.existing), proposed=value(self.proposed),
                    other_symbol_id=self.other_symbol_id)


@dataclass(frozen=True)
class Change:
    before: SymbolRecord | None
    after: SymbolRecord

    def to_dict(self) -> dict:
        return dict(before=None if self.before is None else self.before.to_dict(),
                    after=self.after.to_dict())


@dataclass(frozen=True)
class RegistryUpdate:
    base_sha256: str
    records: tuple[SymbolRecord, ...]
    changes: tuple[Change, ...]
    conflicts: tuple[Conflict, ...]

    def to_json(self) -> str:
        """Review report, including evidence for rejected incoming assertions.

        This report is not a snapshot/import envelope; apply this update object
        after review, or propose its original symbol envelope again on resume.
        """
        return _json(dict(schema_version=SCHEMA_VERSION, base_sha256=self.base_sha256,
                          applicable=not self.conflicts,
                          proposed_symbols=[r.to_dict() for r in self.records],
                          changes=[c.to_dict() for c in self.changes],
                          conflicts=[c.to_dict() for c in self.conflicts]))


class RegistryConflict(ValueError):
    """No changes applied; inspect update.conflicts for competing assertions."""


class SymbolRegistry:
    def __init__(self, records: Iterable[SymbolRecord] = ()) -> None:
        self._records: dict[SymbolKey, SymbolRecord] = {}
        self.apply(self.propose(records))

    @property
    def records(self) -> tuple[SymbolRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def to_json(self) -> str:
        return _json(dict(schema_version=SCHEMA_VERSION,
                          symbols=[record.to_dict() for record in self.records]))

    @property
    def revision(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def propose(self, records: Iterable[SymbolRecord]) -> RegistryUpdate:
        """Plan additive assertions; never mutate, delete, or prefer new evidence.

        Repeated keys in a batch merge aliases/evidence but are checked exactly
        like stored keys. Named layouts are also checked across different symbol
        addresses within the same binary. Any disagreement blocks the batch.
        """
        records = tuple(records)
        if any(not isinstance(r, SymbolRecord) for r in records):
            raise ValueError("updates must contain SymbolRecord values")
        # Stable review output independent of incoming batch order.
        records = tuple(sorted(records, key=lambda r: _json(r.to_dict())))
        pending = dict(self._records)
        conflicts = []
        for record in records:
            previous = pending.get(record.key)
            if previous is None:
                pending[record.key] = record
                continue
            refinements = {}
            for field in ("kind", "architecture", "mode", "layout", "signature"):
                old, new = getattr(previous, field), getattr(record, field)
                if old is not None and new is not None and old != new:
                    conflicts.append(Conflict(record.key.identifier, field, old, new))
                refinements[field] = old if old is not None else new
            # Do not construct an invalid hybrid from incompatible symbol kinds.
            if any(c.symbol_id == record.key.identifier for c in conflicts):
                continue
            pending[record.key] = replace(previous, **refinements,
                                         names=previous.names + record.names,
                                         evidence=previous.evidence + record.evidence)
        types: dict[tuple[str, str], SymbolRecord] = {}
        for key in sorted(pending):
            record = pending[key]
            if record.layout is None:
                continue
            type_key = (key.binary_sha256, record.layout.name)
            other = types.get(type_key)
            if other is not None and other.layout != record.layout:
                conflicts.append(Conflict(key.identifier, "layout", other.layout,
                                          record.layout, other.key.identifier))
            else:
                types[type_key] = record
        changes = tuple(Change(self._records.get(key), pending[key])
                        for key in sorted(pending)
                        if self._records.get(key) != pending[key])
        return RegistryUpdate(self.revision, records, changes, tuple(conflicts))

    def propose_json(self, document: str) -> RegistryUpdate:
        """Accept the same strict versioned envelope used for snapshots."""
        return self.propose(_decode_records(document))

    def apply(self, update: RegistryUpdate) -> None:
        """Apply a reviewed proposal atomically; stale/forged reports fail closed.

        This is optimistic validation for a single in-memory owner, not a
        multi-process storage lock or an authenticity/signature mechanism.
        """
        if not isinstance(update, RegistryUpdate):
            raise ValueError("expected a RegistryUpdate")
        if update.base_sha256 != self.revision:
            raise ValueError("stale registry update")
        if self.propose(update.records) != update:
            raise ValueError("update differs from its recomputed proposal")
        if update.conflicts:
            raise RegistryConflict("conflicting assertions; entire update rejected")
        pending = dict(self._records)
        for change in update.changes:
            pending[change.after.key] = change.after
        self._records = pending

    @classmethod
    def from_json(cls, document: str) -> SymbolRegistry:
        return cls(_decode_records(document))


def _decode_records(document: str) -> tuple[SymbolRecord, ...]:
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def fields(obj, expected):
        if not isinstance(obj, dict) or set(obj) != set(expected.split()):
            raise ValueError(f"expected exactly these fields: {expected}")
        return obj

    def array(value):
        if not isinstance(value, list):
            raise ValueError("expected JSON array")
        return tuple(value)

    def invalid_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    if not isinstance(document, str):
        raise ValueError("document must be JSON text")
    root = fields(json.loads(document, object_pairs_hook=unique,
                             parse_constant=invalid_constant), "schema_version symbols")
    if type(root["schema_version"]) is not int or root["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    records = []
    for raw in array(root["symbols"]):
        fields(raw, "id kind architecture mode names layout signature evidence")
        layout = raw["layout"]
        if layout is not None:
            fields(layout, "name size alignment fields kind")
            members = tuple(FieldLayout(**fields(member, "name type_name offset size"))
                            for member in array(layout["fields"]))
            layout = TypeLayout(**{**layout, "fields": members})
        signature = raw["signature"]
        if signature is not None:
            fields(signature, "return_type parameter_types calling_convention variadic")
            signature = Signature(**{**signature,
                                     "parameter_types": array(signature["parameter_types"])})
        records.append(SymbolRecord(
            SymbolKey.parse(raw["id"]), raw["kind"], raw["architecture"], raw["mode"],
            array(raw["names"]), layout, signature, array(raw["evidence"])))
    return tuple(records)
