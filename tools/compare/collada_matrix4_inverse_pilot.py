#!/usr/bin/env python3
"""Measure the reconstructed getInverse against the reference 776-byte window.

Compiles `reconstruction/recovered/libNOVA3_neon/collada_matrix4_inverse.cpp`
with the verified NDK r8e GCC 4.4.3, then reports two independent things:

* whether the instruction window matches, at the three strictness levels of
  ``tools/compare/gcc443_window_check.py`` (raw / calls-normalized / different);
* the instruction-class profile from ``tools/compare/gcc443_explain.py``, which
  says *what* differs when the window does not match.

Honesty rules: a size match is not a match; the status is taken verbatim from
the window checker, and ``source_matching_verified`` stays false unless that
checker returns a matched level.  This never executes original code and emits
only sizes, addresses, counts and booleans.

    tools/compare/collada_matrix4_inverse_pilot.py \
        --gcc .cache/toolchains/android-ndk-r8e/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++ \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
        --out .cache/gcc443-sweep/inverse-pilot.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'reconstruction/recovered/libNOVA3_neon/collada_matrix4_inverse.cpp'
INCLUDE = ROOT / 'reconstruction/recovered/libNOVA3_neon'
SYMBOL = '_ZNK6glitch4core8CMatrix4IfE10getInverseERS2_'
ADDRESS = 0x000EBB08
FLAGS = ['-std=gnu++98', '-marm', '-mfpu=vfpv3-d16', '-O3', '-mfloat-abi=softfp',
         '-fPIC', '-fno-rtti', '-fno-exceptions']


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gcc', required=True, type=pathlib.Path)
    parser.add_argument('--reference', required=True, type=pathlib.Path)
    parser.add_argument('--out', type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        obj = pathlib.Path(tmp) / 'inverse.o'
        compile_result = run([str(args.gcc), *FLAGS, f'-I{INCLUDE}',
                              '-c', str(SOURCE), '-o', str(obj)])
        report: dict = {
            'schema': 'nova3.collada-matrix4-inverse-pilot/1',
            'source': str(SOURCE.relative_to(ROOT)),
            'symbol': SYMBOL,
            'address': f'0x{ADDRESS:08x}',
            'compiler': 'NDK r8e GCC 4.4.3',
            'flags': ' '.join(FLAGS),
            'compiled': compile_result.returncode == 0,
            'source_matching_verified': False,
        }
        if compile_result.returncode != 0:
            report['compile_error'] = compile_result.stderr[-2000:]
            print(json.dumps(report, indent=2))
            return 1

        window = run([sys.executable, str(ROOT / 'tools/compare/gcc443_window_check.py'),
                      '--reference', str(args.reference), '--object', str(obj),
                      '--address', hex(ADDRESS), '--symbol', SYMBOL])
        explanation = run([sys.executable, str(ROOT / 'tools/compare/gcc443_explain.py'),
                           '--gcc', str(args.gcc), '--reference', str(args.reference),
                           '--source', str(SOURCE), '--address', hex(ADDRESS),
                           '--symbol', SYMBOL, '--flags', ' '.join(FLAGS)])
        if window.returncode == 0:
            payload = json.loads(window.stdout)
            report['window'] = {key: payload.get(key) for key in
                                ('original_bytes', 'candidate_bytes', 'status',
                                 'call_sites', 'original_window_sha256',
                                 'candidate_window_sha256')}
            report['source_matching_verified'] = payload.get('status') in (
                'MATCHED_RAW', 'MATCHED_NORMALIZED_CALLS')
        else:
            report['window_error'] = window.stderr[-2000:]
        if explanation.returncode == 0:
            payload = json.loads(explanation.stdout)
            report['instructions'] = {
                'original': payload.get('original_instructions'),
                'candidate': payload.get('candidate_instructions'),
            }
            report['class_count_deltas'] = payload.get('count_deltas')
            report['classes_only_in_original'] = payload.get('classes_only_in_original')
            report['classes_only_in_candidate'] = payload.get('classes_only_in_candidate')
            report['first_divergence'] = payload.get('first_divergence')
        else:
            report['explain_error'] = explanation.stderr[-2000:]

    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
        print(f'wrote {args.out}')
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
