"""Per-platform project toolchain: what is needed, and how to install it.

Three tiers, so a contributor can stop at "worker runs" or go all the way to
"can analyse the original binaries locally":

``core``     required to run the worker at all (python, git, gh)
``project``  required to build and verify this source tree (CMake, Ninja, a
             C/C++ compiler, JDK for Ghidra, archive tools)
``full``     the optional analysis stack (Ghidra, JADX, Apktool, Android NDK,
             LLVM/binutils, rev.ng) where the platform has a package source

Design rules:

* Commands are **argv lists**, never shell strings: the installer runs them
  with ``subprocess`` and no shell, so a package name cannot inject commands.
* Elevation is explicit: Linux/macOS use ``sudo`` (or Homebrew, which must not
  run as root), Windows uses ``winget`` which elevates through the OS prompt.
  Nothing here silently escalates; ``--install`` is an explicit request.
* Every platform declares the same tiers. A package that has no reliable
  package-manager source (IDA Pro, rev.ng) is listed as ``manual`` with a URL,
  never faked as installable.

CLI:

    python -m tools.dsh_loop.toolchain --tier project --plan
    python -m tools.dsh_loop.toolchain --tier full --install
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

TIERS = ('core', 'project', 'full')

# Each entry: check (executable or None), packages per platform manager.
# manager: 'pacman' | 'apt' | 'dnf' | 'brew' | 'winget'
SPECS = {
    'python': {'check': 'python3', 'tiers': ('core',),
               'pacman': ['python'], 'apt': ['python3'], 'dnf': ['python3'],
               'brew': ['python'], 'winget': ['Python.Python.3.12']},
    'git': {'check': 'git', 'tiers': ('core',),
            'pacman': ['git'], 'apt': ['git'], 'dnf': ['git'], 'brew': ['git'],
            'winget': ['Git.Git']},
    'github-cli': {'check': 'gh', 'tiers': ('core',),
                   'pacman': ['github-cli'], 'apt': ['gh'], 'dnf': ['gh'],
                   'brew': ['gh'], 'winget': ['GitHub.cli']},
    'cmake': {'check': 'cmake', 'tiers': ('project',),
              'pacman': ['cmake'], 'apt': ['cmake'], 'dnf': ['cmake'],
              'brew': ['cmake'], 'winget': ['Kitware.CMake']},
    'ninja': {'check': 'ninja', 'tiers': ('project',),
              'pacman': ['ninja'], 'apt': ['ninja-build'], 'dnf': ['ninja-build'],
              'brew': ['ninja'], 'winget': ['Ninja-build.Ninja']},
    'compiler': {'check': 'cc', 'tiers': ('project',),
                 'pacman': ['gcc', 'clang'], 'apt': ['build-essential'],
                 'dnf': ['gcc', 'gcc-c++'], 'brew': ['llvm'],
                 'winget': ['Microsoft.VisualStudio.2022.BuildTools']},
    'jdk': {'check': 'java', 'tiers': ('project',),
            'pacman': ['jdk21-openjdk'], 'apt': ['openjdk-21-jdk'],
            'dnf': ['java-21-openjdk'], 'brew': ['openjdk@21'],
            'winget': ['EclipseAdoptium.Temurin.21.JDK']},
    'archive-tools': {'check': '7z', 'tiers': ('project',),
                      'pacman': ['p7zip', 'unrar'], 'apt': ['p7zip-full', 'unrar'],
                      'dnf': ['p7zip', 'unrar'], 'brew': ['p7zip', 'unar'],
                      'winget': ['7zip.7zip']},
    'ghidra': {'check': None, 'tiers': ('full',), 'manual':
               'https://github.com/NationalSecurityAgency/ghidra/releases'},
    'jadx': {'check': 'jadx', 'tiers': ('full',),
             'pacman': ['jadx'], 'apt': ['jadx'], 'dnf': ['jadx'],
             'brew': ['jadx'], 'winget': ['skylot.jadx']},
    'apktool': {'check': 'apktool', 'tiers': ('full',),
                'pacman': ['apktool'], 'apt': ['apktool'], 'dnf': ['apktool'],
                'brew': ['apktool'], 'winget': []},
    'android-ndk': {'check': None, 'tiers': ('full',), 'manual':
                    'https://developer.android.com/ndk/downloads'},
    'llvm-binutils': {'check': 'llvm-readelf', 'tiers': ('full',),
                      'pacman': ['llvm', 'binutils'], 'apt': ['llvm', 'binutils'],
                      'dnf': ['llvm', 'binutils'], 'brew': ['llvm', 'binutils'],
                      'winget': ['LLVM.LLVM']},
    'ida-pro': {'check': None, 'tiers': ('full',), 'manual':
                'https://www.hex-rays.com/products/ida/'},
}


def platform_family() -> str:
    system = platform.system().lower()
    if system == 'windows':
        return 'windows'
    if system == 'darwin':
        return 'darwin'
    return 'linux'


def package_manager() -> str | None:
    """The manager this host actually uses, in preference order."""
    family = platform_family()
    if family == 'windows':
        return 'winget' if shutil.which('winget') else None
    if family == 'darwin':
        return 'brew' if shutil.which('brew') else None
    for manager, exe in (('pacman', 'pacman'), ('apt', 'apt-get'), ('dnf', 'dnf')):
        if shutil.which(exe):
            return manager
    return None


def install_argv(manager: str, packages: list[str]) -> list[str]:
    """Exact argv for one manager. Never a shell string."""
    if manager == 'pacman':
        return ['sudo', 'pacman', '-S', '--needed', '--noconfirm', *packages]
    if manager == 'apt':
        return ['sudo', 'apt-get', 'install', '-y', *packages]
    if manager == 'dnf':
        return ['sudo', 'dnf', 'install', '-y', *packages]
    if manager == 'brew':
        return ['brew', 'install', *packages]
    if manager == 'winget':
        argv = ['winget', 'install', '-e', '--accept-package-agreements',
                '--accept-source-agreements']
        for package in packages:
            argv += ['--id', package]
        return argv
    raise ValueError(f'unknown package manager: {manager}')


def plan(tier: str = 'project', *, family: str | None = None,
         manager: str | None = None, present=None) -> dict:
    """What is missing for a tier, and the exact installation commands."""
    if tier not in TIERS:
        raise ValueError(f'tier must be one of {TIERS}')
    family = family or platform_family()
    manager = manager or package_manager()
    found = present or shutil.which
    selected = [name for name, spec in SPECS.items() if tier in spec['tiers']]
    missing, manual, commands = [], [], []
    for name in selected:
        spec = SPECS[name]
        check = spec.get('check')
        if check and found(check):
            continue
        packages = spec.get(manager or '', [])
        if not packages:
            if spec.get('manual'):
                manual.append({'name': name, 'url': spec['manual']})
            else:
                manual.append({'name': name, 'url': 'no package source for this platform'})
            continue
        missing.append(name)
        if manager == 'winget':
            # winget takes one --id per invocation; keep commands unambiguous.
            commands.extend(install_argv(manager, [package]) for package in packages)
        else:
            commands.append(install_argv(manager, packages))
    return {'schema': 'nova3-toolchain/1', 'family': family, 'manager': manager,
            'tier': tier, 'missing': missing, 'manual': manual,
            'commands': commands,
            'note': 'commands are argv lists; --install runs them without a shell'}


def run_plan(plan_dict: dict) -> list[dict]:
    """Execute the planned installs. Caller opted in; failures are reported."""
    results = []
    for argv in plan_dict['commands']:
        try:
            completed = subprocess.run(argv, timeout=1800, check=False)
            results.append({'argv': argv, 'exit': completed.returncode})
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({'argv': argv, 'exit': None, 'error': str(exc)[:200]})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='NOVA3 per-platform toolchain plan')
    parser.add_argument('--tier', choices=TIERS, default='project')
    parser.add_argument('--install', action='store_true',
                        help='run the commands (may elevate via sudo/winget)')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    result = plan(args.tier)
    if args.install:
        result['results'] = run_plan(result)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"tier={result['tier']} platform={result['family']} "
              f"manager={result['manager']}")
        print('missing: ' + (', '.join(result['missing']) or 'none'))
        for entry in result['manual']:
            print(f"  manual: {entry['name']} -> {entry['url']}")
        for argv in result['commands']:
            print('  ' + ' '.join(argv))
        if result.get('results'):
            for item in result['results']:
                print(f"  exit={item['exit']}: {' '.join(item['argv'])}")
    outcomes = result.get('results') or []
    return 0 if all(r.get('exit') == 0 for r in outcomes) else (1 if outcomes else 0)


if __name__ == '__main__':
    raise SystemExit(main())
