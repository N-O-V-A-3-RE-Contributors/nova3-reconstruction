"""Versioned evidence for comparisons of explicitly supplied ARM code regions.

``verified`` means exact, nonempty instruction bytes with recorded compiler
version, flags and source SHA-256. ``approximate`` means some corresponding
instructions match, or exact bytes lack build provenance. ``unknown`` means
there is no comparable evidence or no identical corresponding instructions.
Only ``verified`` permits a MATCHED claim, scoped to the supplied streams.

This is byte comparison, not a disassembler, ELF matcher or semantic verifier.
It checks A32/Thumb instruction framing, not opcode validity, relocations,
function coverage, calling conventions or execution context. Identical PC-
relative code at different addresses need not behave identically. Compiler
metadata is caller-supplied provenance, not proof that a build was reproduced.
No files are discovered/read, code executed, or instruction bytes serialized.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
from itertools import zip_longest
import re


SCHEMA_VERSION = 1
SCOPE = "supplied_arm_instruction_streams"


class Classification(str, Enum):
    VERIFIED = "verified"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BuildEvidence:
    """Provenance of the candidate build; None means evidence is missing.

    Flags preserve argument boundaries/order. An explicitly supplied empty
    tuple records no flags; None records unknown flags. source_sha256 hashes
    the source input (or reproducible source manifest), never candidate code.
    """

    compiler_version: str | None = None
    flags: tuple[str, ...] | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.compiler_version is not None and (
            not isinstance(self.compiler_version, str)
            or not self.compiler_version.strip()
        ):
            raise ValueError("compiler_version must be nonempty text or None")
        if self.flags is not None and (
            not isinstance(self.flags, tuple)
            or any(not isinstance(flag, str) or not flag.strip() for flag in self.flags)
        ):
            raise ValueError("flags must be a tuple of nonempty arguments or None")
        if self.source_sha256 is not None:
            if not isinstance(self.source_sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.source_sha256
            ):
                raise ValueError("source_sha256 must be a SHA-256 hex digest or None")
            object.__setattr__(self, "source_sha256", self.source_sha256.lower())

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name in ("compiler_version", "flags", "source_sha256")
                     if getattr(self, name) is None)

    def to_dict(self) -> dict:
        return {"compiler_version": self.compiler_version,
                "flags": None if self.flags is None else list(self.flags),
                "source_sha256": self.source_sha256}


@dataclass(frozen=True)
class InstructionStream:
    """A caller-selected contiguous region, not necessarily a whole function.

    mode is 'arm' (A32) or 'thumb' (T16/T32); architecture must be explicit
    (for example 'armv7-a'). Only little-endian framing is supported. Unsupported
    byte orders produce UNKNOWN comparisons. address, if known, is the actual
    instruction address without a Thumb function pointer's low state bit.
    """

    data: bytes
    mode: str
    architecture: str
    byte_order: str = "little"
    address: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("data must be immutable bytes")
        if self.mode not in ("arm", "thumb"):
            raise ValueError("mode must be 'arm' or 'thumb'")
        if not isinstance(self.architecture, str) or not self.architecture.strip():
            raise ValueError("architecture must be explicit nonempty text")
        if self.byte_order not in ("little", "big"):
            raise ValueError("byte_order must be 'little' or 'big'")
        alignment = 4 if self.mode == "arm" else 2
        if self.address is not None and (
            type(self.address) is not int or not 0 <= self.address < 2**32
            or self.address % alignment or self.address + len(self.data) > 2**32
        ):
            raise ValueError("address must be an aligned, bounded ARM32 code address")


@dataclass(frozen=True)
class StreamEvidence:
    # Hashes cover precisely the supplied region, never an enclosing ELF.
    sha256: str
    byte_count: int
    instruction_count: int | None
    mode: str
    architecture: str
    byte_order: str
    address: int | None


@dataclass(frozen=True)
class Difference:
    instruction_index: int
    candidate_offset: int | None
    reference_offset: int | None


@dataclass(frozen=True)
class ComparisonEvidence:
    method: str
    exact_bytes: bool
    corresponding_instruction_count: int
    identical_instruction_count: int
    first_difference: Difference | None


@dataclass(frozen=True)
class MatchResult:
    classification: Classification
    reason: str
    candidate: StreamEvidence | None
    reference: StreamEvidence | None
    build: BuildEvidence
    comparison: ComparisonEvidence | None

    @property
    def matching_claim(self) -> str:
        return "MATCHED" if self.classification == Classification.VERIFIED else "NOT MATCHED"

    def to_dict(self) -> dict:
        """JSON-ready, byte-free report with an explicit, restricted scope."""
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "complete_elf_match": False,
            "classification": self.classification.value,
            "matching_claim": self.matching_claim,
            "reason": self.reason,
            "candidate": None if self.candidate is None else asdict(self.candidate),
            "reference": None if self.reference is None else asdict(self.reference),
            "build": self.build.to_dict(),
            "missing_build_evidence": list(self.build.missing_fields),
            "comparison": None if self.comparison is None else asdict(self.comparison),
        }


def _instruction_spans(stream: InstructionStream) -> list[tuple[int, int]]:
    """Frame only: A32 words, or T16/T32 using the first halfword prefix."""
    if stream.byte_order != "little":
        raise ValueError("Only little-endian instruction framing is supported.")
    if not stream.data:
        raise ValueError("Empty streams cannot establish a match.")
    spans = []
    offset = 0
    while offset < len(stream.data):
        width = 4
        if stream.mode == "thumb":
            if offset + 2 > len(stream.data):
                raise ValueError("Truncated Thumb halfword.")
            halfword = int.from_bytes(stream.data[offset:offset + 2], "little")
            width = 4 if halfword >> 11 in (0b11101, 0b11110, 0b11111) else 2
        if offset + width > len(stream.data):
            raise ValueError("Truncated instruction.")
        spans.append((offset, offset + width))
        offset += width
    return spans


def _summarize(stream: InstructionStream | None, count: int | None = None) -> StreamEvidence | None:
    if stream is None:
        return None
    return StreamEvidence(hashlib.sha256(stream.data).hexdigest(), len(stream.data), count,
                          stream.mode, stream.architecture, stream.byte_order, stream.address)


def compare_instruction_streams(
    *, candidate: InstructionStream | None = None,
    reference: InstructionStream | None = None,
    build: BuildEvidence | None = None,
) -> MatchResult:
    """Compare corresponding instruction encodings without normalization.

    No register renaming, relocation masking, padding removal, instruction
    reordering or address-based realignment is performed. A partial identical
    prefix is only approximate, even with full provenance. Missing, truncated,
    empty or incompatible streams fail closed to UNKNOWN / NOT MATCHED.
    """
    if candidate is not None and not isinstance(candidate, InstructionStream):
        raise TypeError("candidate must be an InstructionStream or None")
    if reference is not None and not isinstance(reference, InstructionStream):
        raise TypeError("reference must be an InstructionStream or None")
    if build is not None and not isinstance(build, BuildEvidence):
        raise TypeError("build must be BuildEvidence or None")
    build = build if build is not None else BuildEvidence()
    candidate_summary, reference_summary = _summarize(candidate), _summarize(reference)

    def unknown(reason: str) -> MatchResult:
        return MatchResult(Classification.UNKNOWN, reason, candidate_summary,
                           reference_summary, build, None)

    if candidate is None or reference is None:
        return unknown("Both candidate and reference instruction streams must be explicitly supplied.")
    if (candidate.architecture, candidate.mode, candidate.byte_order) != (
        reference.architecture, reference.mode, reference.byte_order
    ):
        return unknown("Architecture, mode and byte order must agree.")
    try:
        candidate_spans = _instruction_spans(candidate)
        reference_spans = _instruction_spans(reference)
    except ValueError as exc:
        return unknown(str(exc))
    candidate_summary = _summarize(candidate, len(candidate_spans))
    reference_summary = _summarize(reference, len(reference_spans))
    identical = 0
    first_difference = None
    for index, (left, right) in enumerate(zip_longest(candidate_spans, reference_spans)):
        if left is not None and right is not None and (
            candidate.data[left[0]:left[1]] == reference.data[right[0]:right[1]]
        ):
            identical += 1
        elif first_difference is None:
            first_difference = Difference(index, None if left is None else left[0],
                                          None if right is None else right[0])
    exact = candidate.data == reference.data
    comparison = ComparisonEvidence("positional_instruction_bytes_v1", exact,
                                    min(len(candidate_spans), len(reference_spans)),
                                    identical, first_difference)
    if exact and not build.missing_fields:
        classification = Classification.VERIFIED
        reason = "Exact supplied instruction bytes with recorded build provenance; stream scope only."
    elif exact:
        classification = Classification.APPROXIMATE
        reason = "Exact supplied bytes, but required build provenance is missing."
    elif identical:
        classification = Classification.APPROXIMATE
        reason = "Some corresponding instruction bytes agree; streams differ."
    else:
        classification = Classification.UNKNOWN
        reason = "No corresponding instructions have identical bytes; no matching claim is supported."
    return MatchResult(classification, reason, candidate_summary, reference_summary, build, comparison)
