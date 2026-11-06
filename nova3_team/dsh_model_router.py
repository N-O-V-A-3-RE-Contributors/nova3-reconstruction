"""Task-aware routing over models already configured in the owner's DSH Web profile.

No catalog guesses, keys, installs, logins, or model benchmarks. The current Web
model is preferred for semantic/planning work; explicitly configured flash/mini
models are preferred for routine work. New Web model settings are read on the
next session. Provider failures may fall back, but task failures never do.
Each route gets an immutable, private headless overlay BEFORE it is launched.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, replace

from . import dsh_runtime as runtime


class ConfigError(RuntimeError):
    pass


# Use DSH's already installed Node YAML parser, not an undeclared Python package.
# YAML !!js values remain inert strings; this does not run Cordis expressions.
YAML_TO_JSON = '''
const {createRequire} = require('node:module');
const fs = require('node:fs');
const r = createRequire(process.argv[1]);
const entries = r('yaml').parse(fs.readFileSync(0, 'utf8'), {logLevel: 'silent'});
process.stdout.write(JSON.stringify(entries));
'''
IDENTIFIER = re.compile(r'[A-Za-z0-9_./:@-]+\Z')
MODEL_FIELDS = {'id', 'name', 'reasoning', 'input', 'contextWindow', 'maxTokens',
                'cost', 'compat', 'thinkingLevelMap'}
PROVIDER_FIELDS = {'api', 'baseURL', 'apiKeyEnv'}


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    key_ref: str
    profile: dict | None = None
    plugin: pathlib.Path | None = None
    effort: str | None = None
    preferred: bool = False

    @property
    def identity(self):
        return f'{self.provider}/{self.model}'

    def summary(self):
        return {'provider': self.provider, 'model': self.model,
                'reasoning_effort': self.effort, 'preferred': self.preferred}


def parse_config(text: str, home: pathlib.Path, profile: str = 'web',
                 known_models=()) -> list[Route]:
    try:
        entries = json.loads(text)
    except ValueError as exc:
        raise ConfigError('DSH config must be parsed as data, not executable YAML') from exc
    if not isinstance(entries, list):
        raise ConfigError('DSH composed config is not a patch list')
    active = {e.get('id'): e for e in entries
              if isinstance(e, dict) and not e.get('disabled', False)}
    default = (active.get('agent-default-model') or {}).get('config') or {}
    preferred = (default.get('provider'), default.get('model'))
    effort = default.get('reasoningEffort')
    routes = []
    pi = (active.get('llm-pi-ai') or {}).get('config') or {}
    for provider, cfg in (pi.get('providers') or {}).items():
        if not isinstance(cfg, dict) or not IDENTIFIER.fullmatch(provider):
            continue
        # Do not copy inline credentials, arbitrary headers, or executable YAML.
        # A custom provider is eligible only with an owner-stored key reference.
        ref = cfg.get('apiKeyEnv')
        if not isinstance(ref, str) or not IDENTIFIER.fullmatch(ref):
            continue
        safe = {k: cfg[k] for k in PROVIDER_FIELDS if k in cfg}
        for model in cfg.get('models') or []:
            if not isinstance(model, dict):
                continue
            mid = model.get('id')
            if not isinstance(mid, str) or not IDENTIFIER.fullmatch(mid):
                continue
            selected = (provider, mid) == preferred
            spec = dict(safe, models=[{k: v for k, v in model.items() if k in MODEL_FIELDS}])
            routes.append(Route(provider, mid, ref, profile=spec,
                                effort=effort if selected else None, preferred=selected))
    # This machine's Codex adapter is a Web plugin, NOT a generic pi-ai API-key
    # route. Load that exact installed plugin in headless; never impersonate it
    # with the OpenRouter base URL/key (or register two owners of its route).
    codex = active.get('openai-codex-auth')
    if codex and codex.get('name') == 'dsh-openai-codex-auth':
        package = home / 'profiles' / profile / 'node_modules' / 'dsh-openai-codex-auth'
        entry = package / 'lib' / 'index.js'
        # A model id is only taken from the current Web default or from a route
        # this router already used successfully; the catalog is never guessed.
        codex_models = []
        if preferred[0] == 'openai-codex' and isinstance(preferred[1], str):
            codex_models.append(preferred[1])
        known_effort = {}
        for item in known_models or ():
            if not isinstance(item, dict) or not isinstance(item.get('model'), str):
                continue
            if item.get('provider') != 'openai-codex':
                continue
            known_effort[item['model']] = item.get('effort')
            if item['model'] not in codex_models:
                codex_models.append(item['model'])
        if entry.is_file():
            routes = [r for r in routes if r.provider != 'openai-codex']
            for mid in codex_models:
                if not IDENTIFIER.fullmatch(mid):
                    continue
                is_default = mid == preferred[1]
                routes.append(Route('openai-codex', mid, 'PI_OAUTH_OPENAI_CODEX',
                                    plugin=entry.resolve(),
                                    effort=(effort if is_default
                                            else known_effort.get(mid)),
                                    preferred=is_default))
    return routes


def host_models(timeout_s: float = 3) -> list[dict] | None:
    """Read added models from the local Host; no paid request or credentials.

    None means unavailable; an empty list is authoritative while online.
    """
    base = os.environ.get('NOVA3_DSH_HOST_URL', 'http://127.0.0.1:3080').rstrip('/')
    request = urllib.request.Request(base + '/dsh-agents/api/state',
                                     headers={'Origin': base})
    try:
        with urllib.request.urlopen(request, timeout=max(0.05, min(3, timeout_s))) as response:
            data = json.loads(response.read(1024 * 1024))
        models = data.get('models')
        if not isinstance(models, list):
            return None
        return [item for item in models if isinstance(item, dict)
                and isinstance(item.get('provider'), str)
                and isinstance(item.get('model'), str)
                and IDENTIFIER.fullmatch(item['provider'])
                and IDENTIFIER.fullmatch(item['model'])]
    except (OSError, ValueError, AttributeError):
        return None


def discover(endpoint: runtime.DshEndpoint, profile: str = 'web',
             known_models=(), timeout_s: float = 45) -> list[Route]:
    deadline = time.monotonic() + timeout_s
    def remaining(cap):
        value = deadline - time.monotonic()
        if value <= 0:
            raise ConfigError('Local model discovery deadline reached')
        return min(cap, value)
    try:
        result = subprocess.run([endpoint.binary, '--profile', profile, '--dump-config'],
                                env=endpoint.environment(), capture_output=True,
                                text=True, timeout=remaining(30), check=False)
        if result.returncode:
            raise ConfigError('DSH Web profile composition failed')
        parsed = subprocess.run(['node', '-e', YAML_TO_JSON,
                                 str(pathlib.Path(endpoint.binary).resolve())],
                                input=result.stdout, capture_output=True,
                                text=True, timeout=remaining(10), check=False)
        if parsed.returncode:
            raise ConfigError('DSH bundled YAML parser unavailable')
        catalog = host_models(remaining(3))
        if catalog is not None:
            # Use the live added-model list instead of resurrecting removed
            # routes from history. parse_config still requires the matching
            # configured provider and installed Codex adapter.
            known_models = [dict(item, effort=('high' if 'high' in
                            (item.get('efforts') or []) else None)) for item in catalog]
        routes = parse_config(parsed.stdout, endpoint.home, profile, known_models)
        if catalog is not None:
            allowed = {(item['provider'], item['model']) for item in catalog}
            routes = [r for r in routes if (r.provider, r.model) in allowed]
        return routes
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError('Local model discovery unavailable') from exc


def purpose(title: str) -> str:
    text = re.sub(r'^\[planner\]\s*', '', title.lower())
    # A bounded test/docs operation stays cheap even when the subsystem name
    # contains ARM/native/compiler. Security review and planning remain semantic.
    if not any(token in text for token in ('security', 'review', 'planner')) and \
            re.match(r'^(?:\[planner\]\s*)?(?:offline\s+)?(?:test\b|add tests\b|docs\b|documentation\b|lint\b|format\b)', text):
        return 'routine'
    if any(token in text for token in ('planner', 'inverse', 'decompil', 'arm',
                                       'native', 'reconstruct', 'operand', 'compiler',
                                       'algorithm', 'review', 'security')):
        return 'semantic'
    if any(token in text for token in ('test', 'inventory', 'freshness', 'docs',
                                       'metadata', 'format', 'lint')):
        return 'routine'
    return 'semantic'


HIGH_EFFORT = ('high', 'xhigh', 'max')


def _is_fast(model: str) -> bool:
    return any(token in model.lower() for token in ('flash', 'mini', 'nano', 'haiku'))


def rank(routes: list[Route], task: str) -> list[Route]:
    """Offline capability heuristic: reasoning level and configured default.

    This is a scheduling preference over the owner's own settings, not a
    benchmark and not a claim that one model is better at the mission.
    """
    routine = purpose(task) == 'routine'

    def capability(route: Route) -> int:
        effort = (route.effort or '').lower()
        if routine:
            return (3 if _is_fast(route.model) else 0) + (1 if route.preferred else 0)
        return ((3 if effort in HIGH_EFFORT else 0)
                + (2 if not _is_fast(route.model) else 0)
                + (2 if route.identity == 'openai-codex/gpt-6.1-sol' else 0)
                + (1 if route.preferred else 0))

    return sorted(routes, key=lambda route: (capability(route), int(route.preferred),
                                             route.identity), reverse=True)


def write_route(base: runtime.DshEndpoint, route: Route, folder: pathlib.Path):
    patches = [{'id': 'credentials', 'disabled': False},
               {'id': 'authorization', 'disabled': False}]
    if route.profile is not None:
        patches.append({'id': 'llm-pi-ai', 'config': {
            'providers': {route.provider: route.profile}}})
    if route.plugin is not None:
        patches.append({'insert': [{'id': 'openai-codex-auth',
                                    'name': str(route.plugin)}]})
    config = {'provider': route.provider, 'model': route.model}
    if route.effort:
        config['reasoningEffort'] = route.effort
    patches.append({'id': 'agent-default-model', 'config': config})
    if base.role_patch is not None:
        role = json.loads(pathlib.Path(base.role_patch).read_text())
        if not isinstance(role, list):
            raise ConfigError('Role enforcement overlay must be a patch list')
        patches.extend(role)
    raw = json.dumps(patches, indent=2, sort_keys=True) + '\n'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (hashlib.sha256(raw.encode()).hexdigest()[:20] + '.json')
    # Content-addressed paths cannot switch a different live session's model.
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(raw)
    return replace(base, route=route.provider, model=route.model, patch=path,
                   key_ref=route.key_ref if route.profile is not None else '')


class Router:
    def __init__(self, base: runtime.DshEndpoint, folder: pathlib.Path):
        self.base = base
        self.folder = pathlib.Path(folder)
        self.state_path = self.folder / 'status.json'
        try:
            self.state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.cooldowns = self.state.get('cooldowns') or {}

    def save(self, **fields):
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.update(fields, cooldowns=self.cooldowns, updated_unix=int(time.time()))
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True) + '\n')
        tmp.chmod(0o600)
        os.replace(tmp, self.state_path)

    def run(self, prompt, *, task, cwd, log_path, timeout_s, cancel, on_failure,
            on_selected=lambda route: None):
        deadline = time.monotonic() + timeout_s
        remembered = self.state.get('known') or []
        routes = rank(discover(self.base, known_models=remembered, timeout_s=timeout_s), task)
        # Remember routes that still compose and are still authenticated, so a
        # model keeps being offered after the Web default moves elsewhere. The
        # list is revalidated against the live profile on every session.
        merged = {f'{r.provider}/{r.model}': {'provider': r.provider, 'model': r.model,
                                              'effort': r.effort} for r in routes}
        for item in remembered:
            if not isinstance(item, dict):
                continue
            key = f'{item.get("provider")}/{item.get("model")}'
            merged.setdefault(key, {'provider': item.get('provider'),
                                    'model': item.get('model'),
                                    'effort': item.get('effort')})
        known = list(merged.values())[-12:]
        now = time.time()
        eligible = [r for r in routes
                    if runtime.credential_present(self.base.home, r.key_ref)
                    and self.cooldowns.get(r.identity, 0) <= now]
        self.save(task=task, purpose=purpose(task), known=known,
                  catalog=[r.summary() for r in routes],
                  eligible=[r.identity for r in eligible])
        if not eligible:
            raise ConfigError('No configured authenticated model outside its cooldown')
        last = None
        previous_route = None
        depleted_providers = set()
        for index, route in enumerate(eligible):
            # Account credit exhaustion cannot be fixed by trying seven model
            # names on the same account. Move directly to another provider.
            if route.provider in depleted_providers:
                continue
            if cancel():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ep = write_route(self.base, route, self.folder / 'overlays')
            if last is not None:
                on_failure(last, previous_route.summary())
            on_selected(route.summary())
            previous_route = route
            self.save(active=route.summary(), status='RUNNING')
            attempt_log = pathlib.Path(log_path)
            if index:
                attempt_log = attempt_log.with_name(
                    attempt_log.stem + f'-route-{index}' + attempt_log.suffix)
            outcome = runtime.run_session(ep, prompt, cwd=cwd, log_path=attempt_log,
                                          timeout_s=remaining, cancel=cancel)
            self.save(active=route.summary(), status=outcome.status,
                      session_id=outcome.session_id)
            last = outcome
            if outcome.status not in runtime.FAILOVER_STATUSES:
                return outcome
            until = int(time.time()) + 600
            if outcome.status in (runtime.WAITING_CREDITS, runtime.AUTH):
                depleted_providers.add(route.provider)
                for sibling in routes:
                    if sibling.provider == route.provider:
                        self.cooldowns[sibling.identity] = until
            else:
                self.cooldowns[route.identity] = until
            self.save()
        if last is not None:
            return last
        raise ConfigError('Routing cancelled before a session could start')
