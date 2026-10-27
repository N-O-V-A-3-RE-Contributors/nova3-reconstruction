"""NOVA3 unattended task replenishment and merged-PR reconciliation.

Only GitHub issue metadata is mutated; never downloads or publishes original binaries.
This module does not claim a task is a byte-match or invoke an AI provider.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
from dataclasses import dataclass

from .common import GitHub, label_names, lease_put

MAIN = 'main'
MARKER = 'NOVA3_AUTOTASK_KEY'
QUEUE = 'nova:queued'
REVIEW = 'nova:review'
CLAIMED = 'nova:claimed'
BLOCKED = 'nova:blocked'
INTEGRATED = 'nova:integrated'
ATTENTION = 'nova:attention'
READY_LIMIT = 24
BATCH_LIMIT = 8
HEX64 = re.compile(r'^[a-fA-F0-9]{64}$')
VALID_KEY = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.:-]{2,160}$')
PR_BRANCH = re.compile(r'^nova/issue-(\d+)-[0-9a-f]+$')
REF_ISSUE = re.compile(r'(?im)\b(?:refs|closes|fixes|resolves)\s+#(\d+)\b')
MARKER_RE = re.compile(r'<!--\s*'+MARKER+r':\s*([a-zA-Z0-9_.:-]+)\s*-->')
MANIFESTS = ('coordination/tasks.json', 'coordination/task_queue.json', 'coordination/reconstruction_tasks.json')
LEDGER = 'coordination/function_ledger.json'


def key_of(issue):
    match = MARKER_RE.search(issue.get('body') or '')
    return match.group(1) if match else None


def issue_marker(key):
    if not VALID_KEY.fullmatch(key):
        raise ValueError('Invalid task key')
    return f'<!-- {MARKER}: {key} -->'


def clean_path(path):
    if not isinstance(path, str) or '\\' in path or path.startswith('/') or '..' in path.split('/'):
        raise ValueError(f'Invalid path: {path!r}')
    if not re.fullmatch(r'[a-zA-Z0-9_./-]+', path) or '/' not in path:
        raise ValueError(f'Invalid task path: {path!r}')
    denied = ('originals/', 'analysis/', 'research/', 'generated/', 'build/', '.github/', 'Archive/')
    if path.startswith(denied):
        raise ValueError(f'Disallowed upload path: {path!r}')
    return path


def as_integer(value, *, address=False):
    if type(value) is int:
        return value
    if not isinstance(value, str):
        raise ValueError('Not an integer')
    value = value.strip()
    if address and re.fullmatch(r'(?:0x)?[0-9a-fA-F]{1,16}', value):
        return int(value.removeprefix('0x'), 16)
    if re.fullmatch(r'0x[0-9a-fA-F]+', value):
        return int(value, 16)
    if re.fullmatch(r'[0-9]{1,10}', value):
        return int(value)
    raise ValueError('Not a valid size or address')


def generate_from_manifest(data):
    entries = data.get('tasks') if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError('Task manifest must contain a tasks array')
    tasks = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        key = str(item.get('id') or item.get('key') or '')
        if not VALID_KEY.fullmatch(key) or key in seen:
            continue
        # Manifest entries must be explicitly approved for issue publication.
        if item.get('publish') is not True:
            continue
        status = str(item.get('status', 'pending')).lower()
        if status not in ('pending', 'queued', 'ready'):
            continue
        dependencies = item.get('depends_on', [])
        if not isinstance(dependencies, list) or any(not VALID_KEY.fullmatch(str(x)) for x in dependencies):
            continue
        paths = item.get('allowed_paths', [])
        if not isinstance(paths, list) or not paths or len(paths) > 16:
            continue
        paths = [clean_path(x) for x in paths]
        title = str(item.get('title') or '').strip()
        if not title or len(title) > 150:
            continue
        priority = min(99, max(1, int(item.get('priority', 70))))
        description = str(item.get('description') or '').strip()[:5000]
        tasks.append({'key':key,'title':title,'priority':priority,'paths':paths,
                      'description':description, 'provenance':'checked-in manifest', 'depends_on':dependencies})
        seen.add(key)
    return tasks


# NOVA3_AUTODISCOVERY_COVERAGE_V1
# Existing issue content is the durable ownership ledger for identified
# addresses. Never requeue addresses whose functions were already assigned,
# even when the source ledger grows and chunk boundaries change.
def covered_function_addresses(issues):
    covered=set()
    for issue in issues:
        body=issue.get('body') or ''
        if not key_of(issue) or not key_of(issue).startswith('functions-'):
            continue
        sha=re.search(r'(?im)^Reference SHA256: [`]?([0-9a-f]{64})[`]?\s*$',body)
        row=re.search(r'(?im)^Addresses/sizes: (.+)$',body)
        if not sha or not row:
            continue
        # Require proper address:size tokens; no Python evaluation of GitHub text.
        pairs=re.findall(r"['\"]([0-9a-f]{4,16}):([0-9a-f]{1,8})['\"]",row.group(1))
        for addr,size in pairs[:128]:
            if 0 < int(size,16) <= 1048576:
                covered.add((sha.group(1),int(addr,16)))
    return covered


def generate_from_ledger(data, covered=None):
    """Only functions with identified boundaries AND an original reference hash.

    Identified != verified equivalent; a Ghidra/SYMTAB function is NOT a byte match.
    """
    if not isinstance(data, dict) or not isinstance(data.get('functions'), list):
        raise ValueError('Function ledger needs a functions array')
    refs = {}
    for ref in data.get('references', []):
        if not isinstance(ref, dict):
            continue
        digest = str(ref.get('sha256', '')).lower()
        name = str(ref.get('library', ''))
        if HEX64.fullmatch(digest) and re.fullmatch(r'lib[a-zA-Z0-9_+.-]{1,70}\.so', name):
            refs[digest] = name
    groups = defaultdict(list)
    seen = set()
    for fn in data['functions']:
        if not isinstance(fn, dict):
            continue
        digest = str(fn.get('reference_sha256', '')).lower()
        if digest not in refs:
            continue
        if fn.get('boundary_status') not in ('tool_identified', 'verified'):
            continue
        if str(fn.get('status', 'pending')).lower() in ('integrated', 'matched', 'verified', 'blocked', 'done'):
            continue
        try:
            addr = as_integer(fn.get('address'),address=True)
            size = as_integer(fn.get('size'))
        except ValueError:
            continue
        if addr < 0 or size <= 0 or size > 1024 * 1024:
            continue
        ident = (digest, addr)
        if ident in seen or (covered and ident in covered):
            continue
        seen.add(ident)
        groups[digest].append((addr,size))
    tasks=[]
    for digest,functions in sorted(groups.items()):
        functions.sort()
        # Small deterministic, bounded clusters. Larger dependency clusters should
        # be supplied explicitly by the task generator's manifest.
        for index in range(0,len(functions),4):
            chunk=functions[index:index+4]
            ids = [f'{addr:08x}:{size:x}' for addr,size in chunk]
            key='functions-'+hashlib.sha256((digest+','+','.join(ids)).encode()).hexdigest()[:22]
            lib_safe = re.sub(r'[^a-zA-Z0-9_]', '_', refs[digest].removesuffix('.so'))
            basename=f'block_{chunk[0][0]:08x}'
            # NOVA3_CLUSTER_SCOPES_V2: only the task-specific output filenames and subdirectories.
            paths=[f'reconstruction/recovered/{lib_safe}/{basename}.cpp',
                   f'reconstruction/recovered/{lib_safe}/{basename}',
                   f'reconstruction/tests/recovered/{lib_safe}/{basename}_test.cpp',
                   f'reconstruction/tests/recovered/{lib_safe}/{basename}',
                   f'coordination/evidence/{key}.json']
            short = ', '.join('0x'+x.split(':')[0] for x in ids)
            body = (f'Reference library: {refs[digest]}\n'
                    f'Reference SHA256: `{digest}`\n'
                    f'Function boundaries (tool-identified only): {short}\n'
                    f'Addresses/sizes: {ids}\n\n'
                    'Reconstruct this cluster into human-readable C++ using only lawfully held local references. '
                    'Source files beyond the named block file must go inside that block-specific directory. '
                    'Do not patch the APK or library. Verify with reproducible compilation, assembly comparison '
                    'and/or behavioural tests. When reference machine-code comparison is unavailable, record '
                    'NOT MATCHED / UNVERIFIED explicitly; never fabricate a match. '
                    'Preserve existing source and tests. Original binaries and extensive decompiler exports must '
                    'never be committed. This issue requires local reference files for meaningful source matching.')
            tasks.append({'key':key,'title':f'Reconstruct {refs[digest]} functions {short[:65]}',
                          'paths':paths,'description':body,'priority':65,
                          'provenance':'tool-identified ELF/Ghidra metadata'})
    return tasks


def load_tasks(root, covered=None):
    root=Path(root)
    for rel in MANIFESTS:
        p=root/rel
        if p.is_file():
            try:
                obj=json.loads(p.read_text(encoding='utf-8'))
                tasks=generate_from_manifest(obj)
                if tasks:
                    return tasks,rel
            except (ValueError,TypeError) as exc:
                print(f'WARNING: task manifest {rel} cannot be used: {exc}')
    p=root/LEDGER
    if p.is_file():
        try:
            obj=json.loads(p.read_text(encoding='utf-8'))
            return generate_from_ledger(obj,covered=covered),LEDGER
        except (ValueError,TypeError) as exc:
            print(f'WARNING: function ledger {LEDGER} cannot be used: {exc}')
            return [], f'invalid {LEDGER}: {exc}'
    return [], 'missing verified task manifest / function ledger'


def task_body(task):
    lines=[f'Priority: {task["priority"]}',
           'Allowed paths: '+', '.join(task['paths']),
           '',task['description'], '',
           'Verification: independent compilation/tests and explicit match evidence; do not claim verified source without proof.',
           'No original APKs, OBBs, SO binaries or extensive decompiler exports may be uploaded.',
           '',issue_marker(task['key'])]
    return '\n'.join(lines)


def all_issues(gh, max_pages=100):
    items=[]
    for page in range(1,max_pages+1):
        data=gh.get(f'issues?state=all&per_page=100&page={page}')
        items.extend(x for x in data if 'pull_request' not in x)
        if len(data) < 100:
            return items
    raise RuntimeError('Too many GitHub issues to enumerate safely; queue halted to prevent duplicate creation')


def all_prs(gh, max_pages=100):
    items=[]
    for page in range(1,max_pages+1):
        data=gh.get(f'pulls?state=all&per_page=100&page={page}')
        items.extend(data)
        if len(data)<100:
            return items
    raise RuntimeError('Too many pull requests to enumerate safely; reconciliation paused')


def linked_issue_number(gh,pr):
    branch=((pr.get('head') or {}).get('ref') or '')
    match=PR_BRANCH.fullmatch(branch)
    if not match:
        return None
    n=int(match.group(1))
    refs={int(x) for x in REF_ISSUE.findall(pr.get('body') or '')}
    if n not in refs:
        return None
    base=pr.get('base') or {}
    if base.get('ref') != MAIN:
        return None
    upstream=((base.get('repo') or {}).get('full_name') or '')
    if upstream and upstream.lower() != gh.repo.lower():
        return None
    return n


def reconcile(gh,issues,prs):
    """Reconcile historical merges whose pull_request event was missed.

    NEVER auto-close an issue merely because it has the nova:review label.
    """
    indexed={i['number']:i for i in issues}
    merged={}
    for pr in prs:
        n=linked_issue_number(gh,pr)
        if n is not None and pr.get('merged_at'):
            merged[n]=pr
    total=0
    for number,pr in merged.items():
        issue=indexed.get(number)
        if issue is None:
            continue
        labels=label_names(issue)
        if not any(x in labels for x in (QUEUE, CLAIMED, REVIEW, BLOCKED, INTEGRATED)):
            continue
        updated=[x for x in labels if x not in (QUEUE,CLAIMED,REVIEW,BLOCKED,ATTENTION)]
        if INTEGRATED not in updated:
            updated.append(INTEGRATED)
        if issue.get('state') != 'closed' or set(labels)!=set(updated):
            gh.patch(f'issues/{number}',{'state':'closed', 'labels':updated,
                                        'body':lease_put(issue.get('body'),None)})
            total+=1
            issue['state']='closed';issue['labels']=[{'name':s} for s in updated]
    return total


def create_intake(gh, existing):
    key='prepare-verified-function-ledger-v1'
    matches=[i for i in existing if key_of(i)==key]
    if matches:
        i=matches[0]
        # If someone closed it without providing a ledger, show a visible blocker,
        # rather than reopening and re-running the same AI task indefinitely.
        if i.get('state')=='closed':
            labels=[x for x in label_names(i) if x not in (QUEUE,CLAIMED,REVIEW)]
            if BLOCKED not in labels: labels.append(BLOCKED)
            gh.patch(f'issues/{i["number"]}', {'state':'open','labels':labels})
            return 'blocked: prerequisite previously closed without creating verified ledger'
        return 'existing: ledger preparation task'
    body = '\n'.join([
        'Priority: 98',
        'Allowed paths: nova3_team/indexer.py, nova3_team/tests/test_indexer.py, nova3_team/task_queue.py, nova3_team/tests/test_task_queue.py, coordination/function_ledger.json, coordination/tasks.json, docs/AUTOFEED.md',
        '',
        'The initial issue queue has no publishable, reference-grounded function ledger. Finish the verified local ELF/Ghidra metadata exporter and integrate its output with coordination/function_ledger.json (or a validated coordination/tasks.json manifest).',
        'A reference directory is available only when an individual contributor lawfully supplies one. Do not fabricate function addresses, size, matches or game-completion percentages. Local reference data is never uploaded.',
        'Submit tests with synthetic ELF/Ghidra fixtures; prove extracted records carry reference SHA256, address, size, and boundary confidence.',
        'If real local binaries are unavailable, implement and test the exporter but report this issue as BLOCKED, rather than claiming real function coverage or repeatedly closing the task.',
        '',issue_marker(key)
    ])
    gh.post('issues',{'title':'[NOVA3 prerequisite] Populate verified ARM function ledger for automatic task generation',
                      'body':body,'labels':[QUEUE]})
    return 'created: verified function-ledger prerequisite'



def create_expansion(gh, issues, root, origin):
    """Request the NEXT evidence slice once, after known eligible tasks exhaust.

    Never declare the game complete from an empty queue. One stable expansion key
    per manifest/ledger hash prevents endless identical work cycles.
    """
    if origin.startswith('missing') or origin.startswith('invalid'):
        return 'No validated manifest/ledger to expand'
    reference=Path(root)/origin
    if not reference.is_file():
        return 'Metadata source disappeared; unable to expand'
    digest=hashlib.sha256(reference.read_bytes()).hexdigest()[:20]
    key=f'expand-evidence-{digest}'
    existing=next((x for x in issues if key_of(x)==key),None)
    if existing:
        if existing.get('state')=='closed':
            # It was completed/closed without new evidence. Visible blocker, no
            # repeated AI calls or issue-comment storms.
            labels=[lab for lab in label_names(existing) if lab not in (QUEUE,CLAIMED,REVIEW)]
            if BLOCKED not in labels: labels.append(BLOCKED)
            gh.patch(f'issues/{existing["number"]}',{'state':'open','labels':labels})
            return 'Existing expansion closed without fresh evidence; blocked'
        return 'Existing evidence expansion still tracked'
    task={'key':key,'title':'Expand verified ARM ELF function inventory from local references',
          'priority':97,
          'paths':['nova3_team/indexer.py','nova3_team/local_index.py',
                   'nova3_team/task_queue.py','coordination/function_ledger.json',
                   'coordination/tasks.json','docs/AUTOFEED.md'],
          'description':('The current function/task manifest has been exhausted, NOT the complete original game. '
                         'Inspect additional functions using locally and lawfully held original ARM libraries '
                         'and Ghidra analysis. Add independently identified address/size/hash metadata to the '
                         'approved ledger or generate the next dependency-aware task batch. Preserve existing '
                         'metadata. Never invent function identifiers and never commit original binaries, '
                         'decompiled listings or assets. If local reference access is unavailable, report BLOCKED.')}
    gh.post('issues',{'title':task['title'],'body':task_body(task),'labels':[QUEUE]})
    return 'Created one evidence expansion task; never equate manifest exhaustion with game completion'

# NOVA3_SOLO_DISCOVERY_PIPELINE_V1
# No Codex expansion jobs while an actual local discovery system exists.
# Failing analysis or missing RAM must be visible, not converted into AI work.
def discovery_or_expansion(gh, issues, root, origin):
    root=Path(root)
    if ((root/'nova3_team/discovery.py').is_file() or
            (root/'coordination/discovery_config.json').is_file()):
        return ('WAITING_FOR_DISCOVERY: known functions already assigned; '
                'local Ghidra discovery will publish fresh metadata when resources permit')
    return create_expansion(gh,issues,root,origin)


def reconcile_and_feed(gh, root, *, batch=BATCH_LIMIT, cap=READY_LIMIT):
    if not 1 <= batch <= 20 or not 1 <= cap <= 100:
        raise ValueError('Task creation limits out of range')
    issues=all_issues(gh)
    prs=all_prs(gh)
    reconciled=reconcile(gh,issues,prs)
    tasks,origin=load_tasks(root,covered=covered_function_addresses(issues))
    active=sum(i.get('state')=='open' and bool({QUEUE, CLAIMED, REVIEW} & set(label_names(i))) for i in issues)
    before=active
    created=[]
    note=''
    known={key_of(i) for i in issues if key_of(i)}
    if not tasks and origin == LEDGER:
        if (Path(root)/'coordination/discovery_config.json').is_file():
            note='No uncovered function addresses in the checked-in ledger; awaiting local headless Ghidra discovery (NO byte-match claim)'
        else:
            # Legacy installations still use the evidence-expansion fallback.
            ledger_keys={key_of(i) for i in issues if key_of(i) and key_of(i).startswith('functions-')}
            pending=[i['number'] for i in issues if i.get('state')=='open' and key_of(i) in ledger_keys
                and bool({QUEUE,CLAIMED,REVIEW} & set(label_names(i)))]
            note=('Waiting for pending function tasks: '+', '.join(map(str,pending[:12]))) if pending else create_expansion(gh,issues,root,origin)
            if note == 'Existing evidence expansion still tracked':
                note += '; waiting for in-flight expansion to integrate'
    elif not tasks:
        note=create_intake(gh, issues)
    else:
        slots=max(0,min(batch,cap-active))
        integrated_keys={key_of(i) for i in issues
                         if i.get('state') == 'closed' and INTEGRATED in label_names(i)}
        for task in sorted(tasks,key=lambda t:(-t['priority'],t['key'])):
            if not slots:break
            if task['key'] in known:continue
            if any(dep not in integrated_keys for dep in task.get('depends_on', [])):
                continue
            obj=gh.post('issues',{'title':task['title'][:150], 'body':task_body(task), 'labels':[QUEUE]})
            known.add(task['key']);created.append(obj.get('number'))
            slots-=1
        if not created and slots == 0:
            note='Queue cap reached; no new issues created'
        elif not created:
            # NOVA3_LEDGER_SCOPED_EXPANSION_V3
            # A stale infrastructure review (for example #4) must not prevent
            # discovery of new reference-grounded function tasks. Only open
            # issues keyed to THIS ledger count as pending function work.
            ledger_keys={t['key'] for t in tasks}
            pending_functions=[i['number'] for i in issues
                if i.get('state')=='open' and key_of(i) in ledger_keys
                and bool({QUEUE,CLAIMED,REVIEW} & set(label_names(i)))]
            unissued_functions=ledger_keys-known
            if pending_functions:
                note=('Waiting for pending function tasks: '
                      + ', '.join('#'+str(n) for n in pending_functions[:12]))
            elif unissued_functions:
                note=('Function tasks remain unissued but cannot yet be queued '
                      '(capacity or dependencies); no evidence task duplicated')
            else:
                if (Path(root)/'coordination/discovery_config.json').is_file() and origin == LEDGER:
                    note='All known function addresses assigned; waiting for local Ghidra discovery service to publish more metadata'
                else:
                    note=discovery_or_expansion(gh,issues,root,origin)
                if note == 'Existing evidence expansion still tracked':
                    note += '; waiting for in-flight expansion to integrate'
    return {'reconciled_merged':reconciled,'origin':origin,
            'manifest_task_count':len(tasks),'active_before':before,
            'created':created,'note':note}


def main(argv=None):
    p=argparse.ArgumentParser(description='NOVA3 bounded automatic GitHub task reconciliation/feeder')
    p.add_argument('--repo',required=True)
    p.add_argument('--root',default='.')
    p.add_argument('--batch',type=int,default=BATCH_LIMIT)
    p.add_argument('--cap',type=int,default=READY_LIMIT)
    a=p.parse_args(argv)
    gh=GitHub(a.repo)
    result=reconcile_and_feed(gh, a.root, batch=a.batch,cap=a.cap)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
