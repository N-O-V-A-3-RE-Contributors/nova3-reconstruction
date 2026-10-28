"""Read-only NOVA3 reference/source map and roadmap telemetry.

A recognised ELF function boundary is not a source match. A merged PR is not
verification. APK entries are catalogued from ZIP metadata, never extracted.
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import json
import math
import re
import zipfile

HEX64 = re.compile(r"^[a-fA-F0-9]{64}$")
HEX_ADDR = re.compile(r"0x[0-9a-fA-F]{6,16}\b")
FN_TITLE = re.compile(r"\bfunctions?\b", re.I)
PR_ISSUE = re.compile(r"^#(\d+):")
APPS = ("Windows x86_64", "Linux x86_64", "Android arm64-v8a", "Android x86_64", "iOS arm64", "iOS Simulator")
CATEGORIES = ("NATIVE SO", "DEX/JAVA", "RESOURCES", "ASSETS", "MANIFEST", "OTHER")
CAP_FILES = 15000


def hex_int(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            v = value.strip()
            if v.lower().startswith('0x') or (re.fullmatch(r'[a-fA-F0-9]{6,16}',v) and (v.startswith('0') or re.search(r'[a-fA-F]',v))):
                return int(v,16)
            return int(v) if v.isdecimal() else None
    except (ValueError, OverflowError):
        pass
    return None


def local_source_stats(root: Path):
    """Read only safe source trees; don't scan private binaries or large dumps."""
    bases = (root/'reconstruction', root/'src', root/'include')
    extensions = {'.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.hxx', '.mm', '.m'}
    total = lines = tests = 0
    dirs = defaultdict(int)
    for base in bases:
        if not base.is_dir():
            continue
        try:
            for path in base.rglob('*'):
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                total += 1
                if total > CAP_FILES:
                    return {'files': total-1, 'lines': lines, 'test_files': tests,
                            'areas': dict(dirs), 'truncated': True}
                try:
                    relative = path.relative_to(root)
                    dirs[str(relative.parts[0]+'/'+(relative.parts[1] if len(relative.parts)>1 else ''))] += 1
                    if 'test' in str(relative).lower():
                        tests += 1
                    if path.stat().st_size <= 512 * 1024:
                        lines += path.read_bytes().count(b'\n')
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
    return {'files': total, 'lines': lines, 'test_files': tests,
            'areas': dict(dirs), 'truncated': False}


def collect_packages(root: Path):
    """Enumerate local APK central directories and private ELF filenames only."""
    apk_files = []
    scan = [root/'analysis', root/'originals', root/'apks', root]
    for base in scan:
        if not base.is_dir():
            continue
        try:
            apk_files.extend(p for p in base.glob('*.apk') if p.is_file())
            if base != root:
                apk_files.extend(p for p in base.glob('*/*.apk') if p.is_file())
        except OSError:
            pass
    seen = set()
    packages = []
    component_counts = defaultdict(int)
    component_sizes = defaultdict(int)
    for apk in apk_files[:30]:
        try:
            if apk.resolve() in seen:
                continue
            seen.add(apk.resolve())
            with zipfile.ZipFile(apk) as z:
                kinds=defaultdict(int)
                sizes=defaultdict(int)
                for entry in z.infolist()[:50000]:
                    if entry.is_dir():
                        continue
                    name=entry.filename.replace('\\','/')
                    kind = ('NATIVE SO' if name.startswith('lib/') and name.endswith('.so') else
                            'DEX/JAVA' if name.endswith('.dex') else
                            'MANIFEST' if name == 'AndroidManifest.xml' else
                            'RESOURCES' if name.startswith(('res/','resources.arsc')) else
                            'ASSETS' if name.startswith('assets/') else 'OTHER')
                    kinds[kind]+=1; sizes[kind]+=entry.file_size
                    component_counts[kind]+=1;component_sizes[kind]+=entry.file_size
                packages.append({'name': apk.name, 'entries': sum(kinds.values()), 'kinds': dict(kinds),
                                 'bytes': dict(sizes), 'index_only': True})
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
            continue
    # Local .so files are listed as physical references but are NOT scanned.
    libraries = []
    for base in (root/'analysis', root/'originals'):
        if not base.is_dir():
            continue
        try:
            for p in base.glob('**/*.so'):
                if len(libraries) >= 100:
                    break
                try:
                    libraries.append({'name': p.name, 'size': p.stat().st_size,
                                      'edition': str(p.parent.parent.name)[:50]})
                except OSError:
                    continue
        except OSError:
            pass
    if libraries and not component_counts.get('NATIVE SO'):
        component_counts['NATIVE SO']=len(libraries)
        component_sizes['NATIVE SO']=sum(x['size'] for x in libraries)
    return {'apks': packages, 'libraries': libraries,
            'component_counts': dict(component_counts), 'component_bytes': dict(component_sizes),
            'scope': 'APK archive entry listing + local .so filenames; no code/asset equivalence inferred'}


def merged_task_ids(prs):
    ids=set()
    for p in prs:
        if not p.get('mergedAt'):
            continue
        m=PR_ISSUE.match(p.get('title') or '')
        if m:
            ids.add(int(m.group(1)))
        for ref in p.get('closingIssuesReferences') or []:
            if isinstance(ref,dict) and type(ref.get('number')) is int:
                ids.add(ref['number'])
    return ids


def source_map(root: Path, issues: list, prs: list, *, scan_files=True):
    ledger_path=root/'coordination/function_ledger.json'
    fn_records={}
    refs={}
    reason='No local function ledger; using GitHub issue addresses'
    if ledger_path.is_file():
        try:
            if ledger_path.stat().st_size > 50_000_000:
                raise ValueError('Ledger exceeds 50MB')
            data=json.loads(ledger_path.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or not isinstance(data.get('functions'),list):
                raise ValueError('Invalid function ledger')
            for r in data.get('references',[]):
                if isinstance(r,dict) and HEX64.fullmatch(str(r.get('sha256',''))):
                    refs[str(r['sha256']).lower()]=str(r.get('library') or 'unidentified library')
            for f in data['functions'][:200000]:
                if not isinstance(f,dict):
                    continue
                digest=str(f.get('reference_sha256','')).lower()
                addr=hex_int(f.get('address'))
                if not HEX64.fullmatch(digest) or addr is None or f.get('boundary_status') not in ('tool_identified','verified'):
                    continue
                lib=refs.get(digest) or str(f.get('library') or 'unknown ELF')
                key=(digest,addr)
                status=str(f.get('status') or 'identified').lower()
                fn_records[key]={'addr':addr,'lib':lib,'status':status,'identified':True,'submitted':False,
                                 'verified':status in ('matched','verified','byte_matched'),
                                 'integrated':status in ('integrated','matched','verified','byte_matched'),
                                 'source':False}
            reason='Known ledger ELF boundaries only; entire-game inventory completeness UNKNOWN'
        except (OSError,ValueError,TypeError):
            reason='Unreadable ledger; issue-address fallback only'
    merged=merged_task_ids(prs)
    by_addr=defaultdict(list)
    for k, record in fn_records.items():
        by_addr[(record['lib'], record['addr'])].append(k)
    for issue in issues:
        title=issue.get('title') or ''
        if not FN_TITLE.search(title):
            continue
        lib_match=re.search(r'\b(lib[A-Za-z0-9_+.\-]+\.so)\b',title)
        lib=lib_match.group(1) if lib_match else 'unknown ELF'
        addresses={int(t,16) for t in HEX_ADDR.findall(title)}
        for addr in addresses:
            ref_match=re.search(r'Reference SHA256:\s*`?([a-fA-F0-9]{64})',issue.get('body') or '', re.I)
            digest=ref_match.group(1).lower() if ref_match else None
            matches=[k for k in by_addr.get((lib,addr),()) if not digest or k[0]==digest]
            # If two reference versions share this address and the task has no
            # reference hash, a PR cannot prove which original it covers.
            ambiguous=not digest and len(matches)>1
            if ambiguous:
                continue
            if not matches:
                key=('issue:'+lib,addr)
                fn_records.setdefault(key,{'addr':addr,'lib':lib,'status':'identified',
                         'identified':True,'submitted':False,'verified':False,'integrated':False,'source':False})
                by_addr[(lib,addr)].append(key)
                matches=[key]
            for k in matches:
                if issue.get('number') in merged:
                    fn_records[k]['integrated']=True
                    fn_records[k]['submitted']=True
                if any(str(l.get('name')) in ('nova:review','nova:integrated') for l in issue.get('labels') or []):
                    fn_records[k]['submitted']=True
    # Match only task-specific locally generated source filename, never assert that
    # any other arbitrary cpp file implements a particular address.
    for f in fn_records.values():
        loc=root/'reconstruction/recovered'/f['lib'].removesuffix('.so')/f"block_{f['addr']:08x}.cpp"
        f['source']=loc.is_file()
    libraries=defaultdict(list)
    for f in fn_records.values():
        libraries[f['lib']].append(f)
    groups=[]
    for lib,items in sorted(libraries.items()):
        items.sort(key=lambda f:f['addr'])
        groups.append({'name':lib, 'identified':len(items), 'integrated':sum(x['integrated'] for x in items),
                       'submitted':sum(x['submitted'] for x in items),
                       'verified':sum(x['verified'] for x in items),
                       'local':sum(x['source'] for x in items),
                       'functions':items})
    all_fns=[f for group in groups for f in group['functions']]
    result={'libraries': groups,'known':len(all_fns),
            'integrated':sum(f['integrated'] for f in all_fns),
            'submitted':sum(f['submitted'] for f in all_fns),
            'verified':sum(f['verified'] for f in all_fns),
            'local':sum(f['source'] for f in all_fns),
            'coverage_scope':reason,'entire_game_total':None}
    if scan_files:
        result['source']=local_source_stats(root)
        result['package']=collect_packages(root)
    else:
        result['source']={'files':0,'lines':0,'test_files':0,'areas':{},'truncated':False}
        result['package']={'apks':[],'libraries':[],'component_counts':{},'component_bytes':{}}
    return result


def addr_tiles(items: list, width=48, phase=0):
    """Tile per known boundary; never draw empty gaps as reconstructed code."""
    if not items:
        return '·'*max(1,width)
    width=max(1,width)
    tiles=[]
    ordered=sorted(items,key=lambda i:i['addr'])
    for n in range(width):
        ix=min(len(ordered)-1, int(n*len(ordered)/width))
        f=ordered[ix]
        glyph=('█' if f['verified'] else '▓' if f['integrated'] else
               '▒' if f['submitted'] else '▦' if f['source'] else '░')
        tiles.append(glyph)
    return ''.join(tiles)


def roadmap_status(issues, prs, plan=None):
    """Render roadmap stages from issue markers; no completion from closed alone."""
    stages=[]
    merged=merged_task_ids(prs)
    if not isinstance(plan,dict):
        return {'stages':[],'notes':'No roadmap manifest installed'}
    for stage in plan.get('stages',[]):
        if not isinstance(stage,dict):
            continue
        tasks=stage.get('tasks',[])
        counts={'merged':0,'queued':0,'claimed':0,'review':0,'blocked':0,'unissued':0}
        for t in tasks:
            if not isinstance(t,dict):
                continue
            marker='NOVA3_ROADMAP_TASK:'+str(t.get('key',''))
            target=next((x for x in issues if marker in (x.get('body') or '')),None)
            if target is None:
                # Our gh issue list omits body by default; support visible title/key
                target=next((x for x in issues if (x.get('title') or '') == t.get('title')),None)
            if target is None:
                counts['unissued']+=1
            elif target.get('number') in merged:
                counts['merged']+=1
            else:
                labels={x.get('name') for x in target.get('labels') or [] if isinstance(x,dict)}
                if 'nova:blocked' in labels:counts['blocked']+=1
                elif 'nova:review' in labels:counts['review']+=1
                elif 'nova:claimed' in labels:counts['claimed']+=1
                elif 'nova:integrated' in labels:counts['review']+=1  # label alone is not merge proof
                else:counts['queued']+=1
        stages.append({'id':stage.get('id'), 'name':stage.get('name'),
                       'count':len(tasks),**counts,
                       'gate':stage.get('gate') or 'unknown'})
    return {'stages':stages, 'notes':'Roadmap tasks merged != game playable; gates are prerequisite rules'}


def load_roadmap(root: Path):
    for candidate in (root/'coordination/project_roadmap.json',
                      Path(__import__('os').environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'nova3-observatory/project_roadmap.json'):
        try:
            if candidate.is_file() and candidate.stat().st_size < 1_000_000:
                obj=json.loads(candidate.read_text(encoding='utf-8'))
                if isinstance(obj,dict) and isinstance(obj.get('stages'),list):
                    return obj
        except (ValueError, OSError):
            pass
    return {'stages':[]}

# Intentionally explicit what-if engineering budgets, in labour hours per stage
# *task*, not observed team time. More workers reduce time sublinearly because
# dependencies, merge conflicts, testing and review have serial overhead.
STAGE_HOURS = {
    'foundation': {'fast':3, 'likely':10, 'slow':30},
    'native-platforms': {'fast':10, 'likely':40, 'slow':160},
    'integration': {'fast':35, 'likely':120, 'slow':450},
    'extensions': {'fast':12, 'likely':55, 'slow':180},
}
QA_HOURS = {'fast':80, 'likely':350, 'slow':1500}


def project_forecast(roadmap: dict, reconstruction_estimate: dict, workers: int = 1):
    """Speculative build/QA end-to-end forecasts; no completion guarantee.

    Total assumes early architecture/native work can overlap reconstruction.
    Verification backlog and unforeseen original game subsystems will dominate.
    """
    effective_workers=max(1,min(32,workers))**0.75
    stage_plan={}
    for stage in roadmap.get('stages',[]):
        ident=stage.get('id','')
        hours=STAGE_HOURS.get(ident, {'fast':10,'likely':50,'slow':200})
        remaining=max(0,stage.get('count',0)-stage.get('merged',0))
        stage_plan[ident]={k:remaining*v*3600/effective_workers for k,v in hours.items()}
    entire={}
    game=reconstruction_estimate.get('whole_game_seconds') or {}
    for mode in ('fast','likely','slow'):
        recover=game.get(mode)
        backbone=sum(stage_plan.get(s,{}).get(mode,0) for s in ('foundation','native-platforms'))
        later=sum(stage_plan.get(s,{}).get(mode,0) for s in ('integration','extensions'))
        qa=QA_HOURS[mode]*3600/effective_workers
        # If completeness is unknown, do not invent zero reconstruction effort.
        entire[mode]=(max(recover,backbone)+later+qa) if recover is not None else None
    return {'seconds':entire,'stage_seconds':stage_plan,
            'model':'Speculative person-hours per roadmap task + gameplay QA allowance; parallel scaling ^0.75',
            'original_scope':'Known inventory completeness 75%/30%/10% per scenario; NOT verified',
            'excludes':'commercial licensing, original game assets, app store approvals, unplanned features, team downtime'}
