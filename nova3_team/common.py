"""Minimal REST client and lease codec. No external Python dependencies."""
from __future__ import annotations
import json, os, re, subprocess, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

START = '<!-- NOVA3_LEASE_START\n'
END = '\nNOVA3_LEASE_END -->'
LEASE_RE = re.compile(re.escape(START) + r'(.*?)' + re.escape(END), re.S)


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec='seconds')


def lease_get(body):
    match = LEASE_RE.search(body or '')
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def lease_put(body, lease):
    base = LEASE_RE.sub('', body or '').rstrip()
    if not lease:
        return base
    return base + '\n\n' + START + json.dumps(lease, sort_keys=True) + END


def lease_active(lease):
    if not lease:
        return False
    try:
        return datetime.fromisoformat(lease['expires_at'].replace('Z', '+00:00')) > now()
    except (ValueError, KeyError):
        return False


class GitHub:
    def __init__(self, repo, token=None):
        self.repo = repo
        self.token = token or os.getenv('GITHUB_TOKEN') or os.getenv('GH_TOKEN')
        if not self.token:
            cp = subprocess.run(['gh','auth','token'], capture_output=True, text=True)
            if cp.returncode == 0:
                self.token = cp.stdout.strip()
        if not self.token:
            raise RuntimeError('GitHub authentication missing: run gh auth login')

    def request(self, method, path, payload=None):
        url = f'https://api.github.com/repos/{self.repo}/{path.lstrip("/")}'
        raw = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=raw, method=method, headers={
            'Authorization': f'Bearer {self.token}',
            'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2022-11-28',
            'User-Agent':'nova3-teamkit/0.1',
            **({'Content-Type':'application/json'} if raw is not None else {})
        })
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors='replace')[:700]
            raise RuntimeError(f'GitHub {method} {path}: HTTP {e.code}: {detail}') from e

    def get(self, path):
        return self.request('GET', path)

    def post(self, path, payload):
        return self.request('POST', path, payload)

    def patch(self, path, payload):
        return self.request('PATCH', path, payload)

    def issue(self, number):
        return self.get(f'issues/{number}')

    def comment(self, number, body):
        return self.post(f'issues/{number}/comments', {'body': body})

    def issues(self, state='open'):
        results=[]
        for page in range(1, 16):
            part = self.get(f'issues?state={state}&per_page=100&page={page}')
            results.extend(i for i in part if 'pull_request' not in i)
            if len(part)<100: break
        return results

    def labels(self, number, labels):
        return self.patch(f'issues/{number}', {'labels':labels})

    def me(self):
        # Repository endpoint gives viewer no authentication identity; gh api works.
        cp=subprocess.run(['gh','api','user','--jq','.login'],capture_output=True,text=True)
        if cp.returncode:
            raise RuntimeError('Cannot identify GitHub user; run gh auth login')
        return cp.stdout.strip()


def label_names(issue):
    return [x['name'] for x in issue.get('labels', [])]


def replace_label(issue, old, new):
    labels = [x for x in label_names(issue) if x not in old]
    if new and new not in labels: labels.append(new)
    return labels
