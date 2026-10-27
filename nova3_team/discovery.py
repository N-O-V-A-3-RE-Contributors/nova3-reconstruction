"""NOVA3 private ELF->Ghidra->sanitised-ledger->GitHub autopilot.

Reference binaries live ONLY in a contributor's local analysis directory.
The published ledger contains only reference hashes, library basenames,
function relative addresses and lengths. No disassembly, strings or assets.

Designed as a separate periodic sidecar. The coding worker never waits on it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from typing import Iterable

from . import discovery_resources as resource_guard

# NOVA3_DISCOVERY_SAFE_RESOURCES_V1

REPO_DEFAULT = 'N-O-V-A-3-RE-Contributors/nova3-reconstruction'
LEDGER_PATH = Path('coordination/function_ledger.json')
DIGEST_RE = re.compile(r'^[a-f0-9]{64}$')
LIB_RE = re.compile(r'^lib[A-Za-z0-9_+.-]{1,70}\.so$')
VERSION = 'NOVA3_DISCOVERY_V1'
MAX_LEDGER_FUNCTIONS = 150000
MAX_LEDGER_BYTES = 40 * 1024 * 1024
MAX_LIBRARIES = 32
GHD_TIMEOUT = 2 * 60 * 60
# NOVA3-DISCOVERY-OBSERVABILITY-v2
FAILED_SCAN_RETRY_SECONDS = 90 * 60
MAX_PERSISTED_LOG_BYTES = 3 * 1024 * 1024



def run_cmd(args: list[str], *, cwd: Path | None = None, timeout: int = 120,
            capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=capture,
                        text=True, timeout=timeout, check=False)
    if check and cp.returncode:
        raise RuntimeError(f"{' '.join(map(str,args[:3]))} exit={cp.returncode}: "
                           + ((cp.stderr or '') + '\n' + (cp.stdout or ''))[-1300:])
    return cp


def cache_root() -> Path:
    return Path(os.environ.get('NOVA3_DISCOVERY_CACHE',
             str(Path.home() / '.cache/nova3-team/discovery'))).expanduser()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(data)
        temp.flush()
        os.fsync(temp.fileno())
        tmp = Path(temp.name)
    os.replace(tmp, path)


def status(message: str, *, kind: str = 'info', **extra) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    print(f'[NOVA3 DISCOVERY] {kind.upper()} {message}', flush=True)
    content = {'version': VERSION, 'time': now, 'kind': kind,
               'message': message, **extra}
    write_atomic(cache_root() / 'status.json',
                 (json.dumps(content, indent=2, sort_keys=True) + '\n').encode())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def find_elfs(project: Path, reference_root: Path | None = None) -> list[Path]:
    roots = [reference_root] if reference_root is not None else [project / 'analysis']
    result = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        # Only load actual shared libraries; never scan build outputs, OBB, APK,
        # system directories or the entire home directory.
        for path in sorted(root.rglob('*.so')):
            if len(result) >= MAX_LIBRARIES:
                return result
            if not path.is_file() or path.is_symlink() or not LIB_RE.fullmatch(path.name):
                continue
            try:
                if not 0 < path.stat().st_size <= 1024**3:
                    continue
                with path.open('rb') as f:
                    if f.read(4) != b'\x7fELF':
                        continue
                digest = sha256_file(path)
            except OSError:
                continue
            if digest not in seen:
                seen.add(digest)
                result.append(path.resolve())
    return result


def find_headless() -> Path | None:
    candidates = []
    for k in ('NOVA3_GHIDRA_HEADLESS', 'GHIDRA_HEADLESS'):
        if os.environ.get(k):
            candidates.append(Path(os.environ[k]).expanduser())
    for k in ('GHIDRA_INSTALL_DIR', 'GHIDRA_HOME'):
        if os.environ.get(k):
            candidates.append(Path(os.environ[k]).expanduser() / 'support/analyzeHeadless')
    wh = shutil.which('analyzeHeadless')
    if wh:
        candidates.append(Path(wh))
    for p in ('/usr/share/ghidra/support/analyzeHeadless',
              '/opt/ghidra/support/analyzeHeadless',
              '/usr/local/share/ghidra/support/analyzeHeadless'):
        candidates.append(Path(p))
    tool_root = Path.home() / '.local/share/nova3-team/tools'
    if tool_root.exists():
        candidates.extend(sorted(tool_root.glob('ghidra/*/support/analyzeHeadless')))
        candidates.extend(sorted(tool_root.glob('ghidra/*/*/support/analyzeHeadless')))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def check_java(min_major: int = 25) -> bool:
    java = shutil.which('java')
    if not java:
        return False
    try:
        output = run_cmd([java, '-version'], check=False, timeout=15)
        version = (output.stderr or '') + (output.stdout or '')
        match = re.search(r'\b(?:version\s+)?"?(\d{2})(?:\.\d+)*', version)
        return bool(match and int(match.group(1)) >= min_major)
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_java_if_needed() -> None:
    if check_java():
        return
    if sys.platform.startswith('linux') and shutil.which('pacman'):
        status('JDK25 missing, installing Arch jdk25-openjdk (may request sudo)', kind='setup')
        run_cmd(['sudo', 'pacman', '-S', '--needed', '--noconfirm', 'jdk25-openjdk'],
                check=True, capture=False, timeout=1200)
    elif sys.platform == 'darwin' and shutil.which('brew'):
        run_cmd(['brew', 'install', 'openjdk@25'], capture=False, timeout=1800)
        # Homebrew's keg-only JDK may require JAVA_HOME set by the user.
    elif os.name == 'nt' and shutil.which('winget'):
        run_cmd(['winget','install','-e','--id','EclipseAdoptium.Temurin.25.JDK',
                 '--accept-package-agreements','--accept-source-agreements'],
                capture=False, timeout=1800)
    if not check_java():
        raise RuntimeError('Ghidra requires JDK 25. Install a compatible JDK or set JAVA_HOME; discovery will retry later.')


def latest_official_ghidra() -> tuple[str, str, str]:
    """Get exact official release asset, and verifiable published checksum."""
    result = run_cmd(['gh', 'api', 'repos/NationalSecurityAgency/ghidra/releases/latest'], timeout=60)
    release = json.loads(result.stdout)
    tag = str(release['tag_name'])
    if not re.fullmatch(r'Ghidra_[0-9][A-Za-z0-9_.-]{2,55}_build', tag):
        raise RuntimeError('Unexpected official Ghidra release tag')
    assets = [a for a in release.get('assets', []) if re.fullmatch(
        r'ghidra_[0-9][A-Za-z0-9_.-]{2,75}\.zip', a.get('name', ''))
        and '_PUBLIC_' in a.get('name', '')]
    if len(assets) != 1:
        raise RuntimeError('Official release has no unique prebuilt Ghidra ZIP')
    asset = assets[0]
    digest = str(asset.get('digest') or '')
    if re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        checksum = digest.split(':', 1)[1].lower()
    else:
        # Official release notes contain a SHA-256 for the prebuilt ZIP.
        matches = re.findall(r'(?i)SHA-256\s*:\s*`?([a-f0-9]{64})`?', release.get('body') or '')
        if len(matches) != 1:
            raise RuntimeError('Official download checksum not available: refusing unverified tool installation')
        checksum = matches[0].lower()
    return tag, asset['name'], checksum


def install_official_ghidra() -> Path:
    """Unprivileged, SHA256-verified latest official Ghidra ZIP install."""
    if not shutil.which('gh'):
        raise RuntimeError('gh CLI missing; run normal NOVA3 contributor setup first')
    install_java_if_needed()
    tag, name, expected = latest_official_ghidra()
    dest_root = Path.home() / '.local/share/nova3-team/tools/ghidra'
    dest = dest_root / tag
    if (dest/'support/analyzeHeadless').is_file():
        return (dest/'support/analyzeHeadless').resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    cache_root().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nova3-ghidra-', dir=str(cache_root())) as tmpdir:
        tmp = Path(tmpdir)
        status(f'Downloading official Ghidra {tag} (large download)', kind='setup')
        run_cmd(['gh','release','download',tag, '-R','NationalSecurityAgency/ghidra',
                 '--pattern',name,'--dir',str(tmp)], capture=False, timeout=3600)
        archive = tmp/name
        if not archive.is_file() or sha256_file(archive) != expected:
            raise RuntimeError('Ghidra release SHA-256 mismatch; refusing installation')
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            for member in members:
                rel = Path(member.filename)
                if rel.is_absolute() or '..' in rel.parts or not rel.parts:
                    raise RuntimeError('Unsafe Ghidra ZIP path')
                if member.file_size > 512 * 1024 * 1024:
                    raise RuntimeError('Unexpected oversized ZIP entry')
            z.extractall(tmp/'expanded')
        possible = list((tmp/'expanded').glob('*/support/analyzeHeadless'))
        if len(possible) != 1:
            raise RuntimeError('Official Ghidra ZIP layout not recognised')
        extracted = possible[0].parent.parent
        shutil.move(str(extracted), str(dest))
        headless = dest/'support/analyzeHeadless'
        headless.chmod(headless.stat().st_mode | 0o111)
    return headless


def ensure_ghidra(*, install: bool) -> Path:
    headless = find_headless()
    if headless:
        return headless
    if not install:
        raise RuntimeError('Ghidra analyzeHeadless unavailable and automatic installation disabled')
    if sys.platform.startswith('linux') and shutil.which('pacman'):
        # Arch Extra's signed package is the lowest-friction solution. Elevated
        # installation happens during INTERACTIVE first-run setup, not in the
        # unattended systemd service.
        status('Installing Ghidra from Arch Extra (sudo required once)', kind='setup')
        run_cmd(['sudo','pacman','-S','--needed','--noconfirm','ghidra'],
                capture=False, timeout=2400)
        headless = find_headless()
        if headless:
            return headless
    return install_official_ghidra()


def parse_export(path: Path, *, digest: str, library: str) -> dict:
    if not DIGEST_RE.fullmatch(digest) or not LIB_RE.fullmatch(library):
        raise ValueError('Invalid library identity')
    if path.stat().st_size > MAX_LEDGER_BYTES:
        raise ValueError('Ghidra metadata exceeds limit')
    raw = json.loads(path.read_text(encoding='utf-8'))
    if raw.get('normalization') != 'image-base-relative' or not isinstance(raw.get('functions'), list):
        raise ValueError('Unrecognised Ghidra function export format')
    fn = {}
    for item in raw['functions']:
        if not isinstance(item, dict):
            continue
        addr = item.get('address')
        size = item.get('size')
        if (not isinstance(addr, str) or not re.fullmatch(r'0x[0-9a-f]{1,16}', addr)
                or type(size) is not int or size <= 0 or size > 1048576):
            continue
        value = int(addr, 16)
        if value < 0 or value >= 2**64:
            continue
        if value not in fn:
            fn[value] = {'reference_sha256': digest, 'address': hex(value), 'size': size,
                         'boundary_status': 'tool_identified', 'status':'pending',
                         'source':'headless Ghidra function boundary metadata'}
    if len(fn) > MAX_LEDGER_FUNCTIONS:
        raise ValueError('Too many functions in one export')
    return {'schema_version':1,
            'references':[{'library':library,'sha256':digest,
                           'source':'locally hashed original ELF (metadata only)'}],
            'functions':[fn[n] for n in sorted(fn)]}


def _private_excerpt(path: Path, limit: int = MAX_PERSISTED_LOG_BYTES) -> str:
    """Bounded, private diagnostic; retain beginning AND end of Ghidra output."""
    if not path.exists():
        return ''
    with path.open('rb') as fh:
        first = fh.read(limit // 2)
        fh.seek(0, 2)
        size = fh.tell()
        if size > len(first):
            fh.seek(max(len(first), size - limit // 2))
            last = fh.read()
            body = first + b'\n[... middle omitted ...]\n' + last
        else:
            body = first
    return body.decode('utf-8', 'replace')


def _save_private_log(source: Path, destination: Path) -> None:
    """Never leave Ghidra logs in the repository, journal or published metadata."""
    if not source.exists():
        return
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = _private_excerpt(source).encode('utf-8', 'replace')
    write_atomic(destination, payload)
    if os.name == 'posix':
        destination.chmod(0o600)


def _ghidra_error_summary(logs: list[Path]) -> str:
    """Find actual errors instead of trailing HEADLESS Script Paths listings."""
    lines = []
    pattern = re.compile(
        r'\b(?:ERROR|FATAL|Exception|OutOfMemoryError|Script not found|'
        r'Invalid script|Could not find script|failed to compile|Compilation failed|'
        r'UnsupportedClassVersionError|NoClassDefFoundError)\b', re.I)
    for log in logs:
        for line in _private_excerpt(log).splitlines():
            if pattern.search(line) and not re.fullmatch(r'\s*at\s+.*', line):
                trimmed = ' '.join(line.split())[:250]
                if trimmed and trimmed not in lines:
                    lines.append(trimmed)
                    if len(lines) >= 5:
                        break
        if len(lines) >= 5:
            break
    return ' | '.join(lines) if lines else 'No explicit error line identified; inspect private Ghidra logs'


def _failure_path(digest: str, script: Path, headless: Path) -> Path:
    """Changing exporter or Ghidra binary allows a fresh attempt immediately."""
    stamp = str(headless.stat().st_mtime_ns) if headless.is_file() else 'missing'
    key = hashlib.sha256((digest + sha256_file(script) + str(headless) + stamp + GHIDRA_PROJECT_PATH_FIX).encode()).hexdigest()[:20]
    return cache_root() / 'failures' / (key + '.json')


# Ghidra refuses hidden path elements (e.g. ~/.cache) in project locations.
GHIDRA_PROJECT_PATH_FIX = 'ghidra-nonhidden-project-directory-v1'


def _ghidra_workspace_parent() -> Path:
    """Private disposable projects must live outside dot-prefixed paths."""
    candidates = [Path(tempfile.gettempdir())]
    if os.name == 'posix':
        candidates.extend((Path('/tmp'), Path('/var/tmp')))
    for candidate in candidates:
        path = candidate.resolve()
        if (path.is_absolute() and all(not part.startswith('.') for part in path.parts)
                and path.is_dir() and os.access(path, os.W_OK | os.X_OK)):
            return path
    raise RuntimeError('No writable dot-free Ghidra temporary directory; set TMPDIR=/tmp')


def run_ghidra(headless: Path, elf: Path, *, script: Path, digest: str,
               out: Path, timeout: int = GHD_TIMEOUT) -> None:
    """Ghidra project and output remain private; only post-processed JSON exits."""
    if not script.is_file():
        raise RuntimeError('Nova3FunctionLedger.java missing from installed package')
    out.parent.mkdir(parents=True, exist_ok=True)
    cache_root().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nova3-ghidra-', dir=str(_ghidra_workspace_parent())) as tmpdir:
        workspace = Path(tmpdir)
        project_root = workspace/'projects'
        project_root.mkdir()
        stage = workspace/'result.json'
        output_log = workspace/'headless.log'
        app_log = workspace/'ghidra-application.log'
        script_log = workspace/'ghidra-script.log'
        args = [str(headless), str(project_root), 'scan_'+digest[:16],
                '-import',str(elf), '-scriptPath',str(script.parent),
                '-postScript',script.name,str(stage),
                '-log',str(app_log),'-scriptlog',str(script_log),
                '-deleteProject', '-max-cpu','1']
        # Ghidra's command line is a list: no shell and no command injection.
        env = os.environ.copy()
        # Override Ghidra's default 2G heap on small machines.
        env['GHIDRA_HEADLESS_MAXMEM'] = resource_guard.heap_setting()
        if shutil.which('nice') and sys.platform != 'win32':
            args = ['nice','-n','10'] + args
        timed_out = False
        with output_log.open('wb') as logfile:
            try:
                cp = resource_guard.supervised_run(args,output=logfile,env=env,timeout=timeout)
            except resource_guard.ResourcePressure:
                dest=cache_root()/'logs'/digest
                for src,name in ((output_log,'headless.log'),(app_log,'application.log'),
                                 (script_log,'script.log')):
                    _save_private_log(src,dest/name)
                raise
            except subprocess.TimeoutExpired:
                timed_out = True
                cp = None
        if timed_out or cp.returncode or not stage.is_file():
            # Previous versions discarded this temp directory and logged only
            # the last 1600 bytes (usually a harmless list of script paths).
            # Keep bounded diagnostics, private to this machine, for diagnosis.
            dest = cache_root()/'logs'/digest
            for src, name in ((output_log,'headless.log'),(app_log,'application.log'),
                              (script_log,'script.log')):
                _save_private_log(src,dest/name)
            detail = _ghidra_error_summary([output_log,script_log,app_log])
            outcome = 'timeout' if timed_out else f'exit={cp.returncode}'
            raise RuntimeError(f'Ghidra {outcome} for {elf.name}; {detail}; '
                               f'private logs: {dest}')
        shutil.copy2(stage, out)


def valid_ledger(data: object) -> bool:
    return isinstance(data, dict) and isinstance(data.get('functions'),list) and isinstance(data.get('references'),list)


def merge_ledgers(current: dict, updates: Iterable[dict]) -> tuple[dict, int]:
    """Append only; preserve existing status, sizes and function identities.

    Returns a *new* ledger without modifying either input. Conflicting sizes are
    never automatically overwritten; existing review evidence remains intact.
    """
    if not valid_ledger(current):
        raise ValueError('Existing ledger has an unsupported schema; refusing overwrite')
    refs: dict[str, dict] = {}
    funcs: dict[tuple[str,int], dict] = {}
    for ref in current['references']:
        if isinstance(ref,dict) and DIGEST_RE.fullmatch(str(ref.get('sha256',''))):
            refs[ref['sha256']] = dict(ref)
    for fn in current['functions']:
        if not isinstance(fn,dict):
            continue
        try:
            addr = int(str(fn['address']),16)
            digest = fn['reference_sha256']
        except (ValueError,KeyError,TypeError):
            continue
        funcs[(digest,addr)] = dict(fn)
    added = 0
    for update in updates:
        if not valid_ledger(update):
            raise ValueError('Invalid discovery update')
        for ref in update['references']:
            if not DIGEST_RE.fullmatch(str(ref.get('sha256',''))) or not LIB_RE.fullmatch(str(ref.get('library',''))):
                raise ValueError('Invalid discovered reference')
            if ref['sha256'] in refs and refs[ref['sha256']].get('library') != ref['library']:
                raise ValueError('Same ELF hash has inconsistent library names')
            refs.setdefault(ref['sha256'],dict(ref))
        for fn in update['functions']:
            digest = fn['reference_sha256']
            if digest not in refs:
                raise ValueError('Function missing validated reference')
            addr = int(str(fn['address']),16)
            if not 0 <= addr < 2**64 or type(fn['size']) is not int or not 0 < fn['size'] <= 1048576:
                raise ValueError('Bad discovered function boundary')
            key = (digest,addr)
            if key not in funcs:
                funcs[key] = dict(fn)
                added += 1
    if len(funcs) > MAX_LEDGER_FUNCTIONS:
        raise ValueError('Total function cap exceeded; cannot safely publish')
    new = dict(current)
    new['schema_version'] = current.get('schema_version',1)
    new['references'] = [refs[key] for key in sorted(refs)]
    new['functions'] = [funcs[key] for key in sorted(funcs)]
    new['notes'] = 'Tool-identified function boundaries only. No byte-match or game-completion percentage is claimed.'
    return new, added


def encode_ledger(data: dict) -> bytes:
    payload = (json.dumps(data,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode()
    if len(payload) > MAX_LEDGER_BYTES:
        raise ValueError('Public metadata exceeds configured 40MiB limit')
    return payload


def gh_repo_validate(repo: str) -> None:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):
        raise ValueError('Invalid GitHub repository')


def publish_updates(repo: str, updates: list[dict], *, retries: int = 3) -> dict:
    """Publish metadata from a throwaway clone; never edit the live checkout.

    First attempt an atomic fast-forward push to main (trusted owner). If the
    branch is protected or contributor lacks write access, open a normal PR and
    request GitHub auto-merge. Never force-push or bypass branch protection.
    """
    gh_repo_validate(repo)
    if not updates:
        return {'mode':'no-input','added':0}
    for attempt in range(retries):
        with tempfile.TemporaryDirectory(prefix='nova3-ledger-publish-') as temp:
            clone = Path(temp)/'checkout'
            run_cmd(['gh','repo','clone',repo,str(clone),'--','--depth=1'],timeout=180)
            path = clone/LEDGER_PATH
            if path.is_file():
                current = json.loads(path.read_text(encoding='utf-8'))
            else:
                current = {'schema_version':1,'references':[],'functions':[]}
            merged, added = merge_ledgers(current,updates)
            if not added:
                return {'mode':'up-to-date','added':0}
            write_atomic(path,encode_ledger(merged))
            run_cmd(['git','config','user.name','NOVA3 local discovery'],cwd=clone)
            run_cmd(['git','config','user.email','nova3-discovery@users.noreply.github.com'],cwd=clone)
            run_cmd(['git','add','--',str(LEDGER_PATH)],cwd=clone)
            run_cmd(['git','diff','--cached','--check'],cwd=clone)
            run_cmd(['git','commit','-m',f'Discover {added} local ELF function boundaries (metadata only)'],cwd=clone)
            branch='nova/discovery-'+hashlib.sha256(encode_ledger(merged)).hexdigest()[:16]
            # Rejected push to protected main is normal; never use --force.
            cp = run_cmd(['git','push','origin','HEAD:refs/heads/main'],cwd=clone,
                         check=False,timeout=120)
            if cp.returncode == 0:
                return {'mode':'published-to-main','added':added}
            combined=(cp.stdout or '')+'\n'+(cp.stderr or '')
            if ('non-fast-forward' in combined.lower() or 'fetch first' in combined.lower()
                    or 'stale info' in combined.lower()):
                continue  # Reclone and merge latest metadata, don't overwrite.
            # Protected main: publish a contributor branch instead. Forks are
            # supported where the private GitHub organisation allows them.
            head=branch
            pushed = run_cmd(['git','push','origin',f'HEAD:refs/heads/{branch}'],
                             cwd=clone,check=False,timeout=120)
            if pushed.returncode:
                account=run_cmd(['gh','api','user','--jq','.login']).stdout.strip()
                fork_data = run_cmd(['gh','api',f'repos/{account}/{repo.split("/",1)[1]}'],
                                    check=False,timeout=60)
                if fork_data.returncode:
                    run_cmd(['gh','repo','fork',repo,'--clone=false'],timeout=120)
                    fork_data=run_cmd(['gh','api',f'repos/{account}/{repo.split("/",1)[1]}'],timeout=60)
                fork=json.loads(fork_data.stdout)
                if ((fork.get('parent') or {}).get('full_name') or '').lower() != repo.lower():
                    raise RuntimeError('Private fork unavailable; cannot upload discovery metadata without write permissions')
                run_cmd(['git','remote','add','discovery-fork',fork['clone_url']],cwd=clone)
                run_cmd(['git','push','discovery-fork',f'HEAD:refs/heads/{branch}'],cwd=clone,timeout=120)
                head=f'{account}:{branch}'
            existing=run_cmd(['gh','pr','list','--repo',repo,'--head',head,
                              '--state','open','--json','url','--jq','.[0].url'],
                             check=False,cwd=clone)
            prurl=existing.stdout.strip() if existing.returncode == 0 else ''
            if not prurl:
                pr=run_cmd(['gh','pr','create','--repo',repo,'--base','main',
                            '--head',head,'--title',f'NOVA3: expand verified ELF boundary inventory ({added} new)',
                            '--body', ('Local Ghidra found additional function boundaries. '
                                       'Metadata only (ELF digest, address, size); no original binaries, '
                                       'decompiled bodies or assets. Functions are NOT byte-matched.')],
                           cwd=clone,timeout=120)
                prurl=pr.stdout.strip()
            auto=run_cmd(['gh','pr','merge',prurl,'--repo',repo,'--squash','--auto'],
                         cwd=clone,check=False,timeout=120)
            if auto.returncode:
                return {'mode':'pull-request-needs-review','added':added,'url':prurl,
                        'warning':(auto.stderr or '')[-300:]}
            return {'mode':'pull-request-auto-merge','added':added,'url':prurl}
    raise RuntimeError('main advanced concurrently on every publication attempt; will retry next cycle')


def one_cycle(project: Path, repo: str, *, reference_root: Path | None = None,
              install_tools: bool = False, publish: bool = True,
              timeout: int = GHD_TIMEOUT) -> dict:
    project = project.resolve()
    cache = cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    # Prioritise small libraries on low-memory contributor devices.
    roots = sorted(find_elfs(project,reference_root),
                   key=lambda path:(path.stat().st_size,path.name))
    if not roots:
        status('NO_REFERENCES: no local original ELF .so libraries under analysis/. '
               'This machine can still reconstruct shared tasks, but only machines with references can discover more.',
               kind='blocked')
        return {'libraries':0,'new':0,'mode':'no-references'}
    headless = ensure_ghidra(install=install_tools)
    script=Path(__file__).parent/'ghidra_scripts/Nova3FunctionLedger.java'
    updates=[]
    failures=[]
    scans_this_cycle = 0
    deferred_count = 0
    deferred_reason = ''
    for elf in roots:
        digest=sha256_file(elf)
        path=cache/'exports'/f'{digest}-{VERSION}.json'
        marker=_failure_path(digest,script,headless)
        # NOVA3_SOLO_OOM_ELF_ROTATION_V1: one machine can try other ELF files
        # after a larger one exceeds its protective memory budget.
        oom_marker=cache/'resource-deferrals'/f'{digest}.json'
        try:
            if not path.is_file():
                if oom_marker.is_file() and os.environ.get('NOVA3_DISCOVERY_FORCE') != '1':
                    try:
                        previous=json.loads(oom_marker.read_text(encoding='utf-8'))
                        wait=time.time()-float(previous.get('time',0))
                    except (ValueError,OSError,TypeError):
                        wait=4*60*60
                    if 0 <= wait < 4*60*60:
                        deferred_count += 1
                        deferred_reason=(f'{elf.name} exceeded safe analysis resources; '
                                         'trying other local libraries first')
                        continue
                if scans_this_cycle >= 1:
                    # Cached exports are free to reuse; expensive analysis is
                    # strictly one reference library per scheduler tick.
                    deferred_count += 1
                    continue
                if marker.is_file() and os.environ.get('NOVA3_DISCOVERY_FORCE') != '1':
                    try:
                        previous=json.loads(marker.read_text(encoding='utf-8'))
                        age=time.time()-float(previous.get('time',0))
                    except (OSError,ValueError,TypeError):
                        age=FAILED_SCAN_RETRY_SECONDS
                        previous={}
                    if 0 <= age < FAILED_SCAN_RETRY_SECONDS:
                        remain=int((FAILED_SCAN_RETRY_SECONDS-age+59)//60)
                        failures.append(f'{elf.name}: previous Ghidra failure; retry in '
                                        f'{remain}m; {str(previous.get("error",""))[:250]}')
                        continue
                okay,why=resource_guard.preflight(_ghidra_workspace_parent())
                if not okay:
                    deferred_reason=why
                    deferred_count += 1
                    break
                scans_this_cycle += 1
                status(f'Analysing {elf.name} ({digest[:12]}) with Ghidra; '
                       f'heap={resource_guard.heap_setting()}, CPU=1',kind='running')
                run_ghidra(headless,elf,script=script,digest=digest,out=path,timeout=timeout)
            update=parse_export(path,digest=digest,library=elf.name)
            if update['functions']:
                updates.append(update)
                marker.unlink(missing_ok=True)
                oom_marker.unlink(missing_ok=True)
            else:
                failures.append(f'{elf.name}: Ghidra found zero executable function boundaries')
        except resource_guard.ResourcePressure as exc:
            deferred_reason=str(exc)
            deferred_count += 1
            # Cool down only if a Ghidra process actually hit safe caps.
            # A preflight RAM deferral never poisons a library cache.
            if any(t in deferred_reason.lower() for t in ('killed', 'timed out', 'stopped to protect')):
                oom_marker.parent.mkdir(parents=True,exist_ok=True)
                write_atomic(oom_marker,json.dumps({'time':time.time(),
                            'cause':'resource-pressure'},sort_keys=True).encode())
            break
        except (OSError,ValueError,RuntimeError) as exc:
            failure=f'{elf.name}: {str(exc)[:800]}'
            failures.append(failure)
            marker.parent.mkdir(parents=True,exist_ok=True)
            write_atomic(marker,json.dumps({'time':time.time(),'error':failure}).encode())
            if os.name=='posix':marker.chmod(0o600)
    if not updates:
        if deferred_count and (deferred_reason or not failures):
            reason=deferred_reason or 'Other libraries deferred to the next scan (one per cycle)'
            status(f'Discovery DEFERRED (not failed): {reason}',kind='deferred',
                   deferred=deferred_count)
            return {'libraries':len(roots),'new':0,'mode':'deferred-resource',
                    'deferred':deferred_count,'reason':reason,'failures':failures}
        status('No successful analysis output; '+ '; '.join(failures)[:800],kind='blocked')
        return {'libraries':len(roots),'new':0,'mode':'analysis-failed','failures':failures}
    if not publish:
        summary = merge_ledgers({'references':[],'functions':[]},updates)
        status(f'Preview: {summary[1]} tool-identified local functions. Not published.',kind='preview')
        return {'libraries':len(roots),'identified':summary[1], 'mode':'preview',
                'deferred':deferred_count, 'failures':failures}
    result = publish_updates(repo,updates)
    status(f'{result["mode"]}: {result["added"]} NEW tool-identified functions from '
           f'{len(updates)} local original ELF(s).',kind='done' if not failures else 'warning',
           **result)
    result.update({'libraries':len(roots),'analysed':len(updates),
                   'deferred':deferred_count,'failures':failures})
    return result


def safe_one_cycle(**kw) -> int:
    try:
        with resource_guard.discovery_lock(cache_root()):
            data = one_cycle(**kw)
        print(json.dumps(data,sort_keys=True),flush=True)
        return 0 if data.get('mode') not in ('analysis-failed',) else 2
    except resource_guard.ResourcePressure as exc:
        status(str(exc)[:1200], kind='deferred')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        status(str(exc)[:1500], kind='error')
        return 2


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description='Headless local reference function-discovery sidecar')
    p.add_argument('action',choices=('run','preview','status','install-tools'))
    p.add_argument('--project',type=Path,default=Path.cwd())
    p.add_argument('--repo',default=REPO_DEFAULT)
    p.add_argument('--reference-root',type=Path,
                   default=Path(os.environ['NOVA3_REFERENCE_ROOT']).expanduser()
                   if os.environ.get('NOVA3_REFERENCE_ROOT') else None)
    p.add_argument('--timeout',type=int,default=GHD_TIMEOUT)
    a=p.parse_args(argv)
    if a.action=='status':
        state=cache_root()/'status.json'
        print(state.read_text() if state.exists() else 'No discovery run yet')
        return 0
    if a.action=='install-tools':
        try:
            print('Ghidra:',ensure_ghidra(install=True))
            return 0
        except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
            print(f'Setup failed: {exc}',file=sys.stderr)
            return 2
    gh_repo_validate(a.repo)
    return safe_one_cycle(project=a.project,repo=a.repo,reference_root=a.reference_root,
                          install_tools=False,publish=a.action=='run',timeout=a.timeout)


if __name__=='__main__':
    sys.exit(main())
