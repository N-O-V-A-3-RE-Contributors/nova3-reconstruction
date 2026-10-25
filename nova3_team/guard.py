"""Fail closed on original game content, suspicious exports, and large tracked files."""
from __future__ import annotations
import argparse, pathlib, subprocess, sys

DENY_PREFIX = ('analysis/','analysis_db/','Archive/','originals/','generated/','research/','build/','.nova/','.venv/')
DENY_EXT = {'.apk','.obb','.so','.a','.o','.obj','.rar','.7z','.zip','.dex','.png','.jpg','.jpeg','.pvr','.ktx','.bdae','.bin','.sqlite','.db','.gpr','.gbf','.rep','.prefix','.specimen','.ppm'}
MAX_BYTES = 5 * 1024 * 1024
MAGICS = (b'\x7fELF',b'dex\n',b'PK\x03\x04',b'Rar!\x1a\x07',b'\x89PNG\r\n\x1a\n')


def check(repo, paths):
    bad=[]
    for name in paths:
        if not name or name.startswith('../') or name.startswith('/'):
            bad.append((name,'invalid path'));continue
        p=repo/name
        if not p.is_file(): continue
        if name.startswith(DENY_PREFIX) or p.suffix.lower() in DENY_EXT:
            bad.append((name,'original/generated/binary path or file type'));continue
        if p.stat().st_size>MAX_BYTES:
            bad.append((name,'file over 5 MiB'));continue
        with p.open('rb') as f: head=f.read(12)
        if any(head.startswith(sig) for sig in MAGICS):
            bad.append((name,'binary magic signature'))
    return bad


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--repo',default='.')
    a.add_argument('--all',action='store_true')
    args=a.parse_args();repo=pathlib.Path(args.repo).resolve()
    mode=['ls-files'] if args.all else ['diff','--cached','--name-only','--diff-filter=ACMR']
    cp=subprocess.run(['git','-C',str(repo),*mode,'-z'],capture_output=True,check=True)
    paths=[p.decode('utf-8','replace') for p in cp.stdout.split(b'\0') if p]
    bad=check(repo,paths)
    if bad:
        for name,reason in bad:print(f'BLOCKED: {name}: {reason}',file=sys.stderr)
        sys.exit(2)
    print(f'Guard passed: {len(paths)} files checked.')

if __name__=='__main__':main()
