"""Read YAML without adding a Python dependency.

Profiles should be readable YAML. PyYAML is not part of this project's pinned
runtime, and DSH already ships a YAML parser, so when PyYAML is absent we convert
through the installed DSH runtime. Falls back with a clear error instead of
silently mis-parsing.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

NODE_SCRIPT = r'''
const {createRequire} = require('node:module');
const fs = require('node:fs');
const requireFromDsh = createRequire(process.argv[1]);
const parsed = requireFromDsh('yaml').parse(fs.readFileSync(0, 'utf8'), {logLevel: 'silent'});
process.stdout.write(JSON.stringify(parsed));
'''


class YamlUnavailable(RuntimeError):
    """No YAML parser on this machine."""


def _runtime_anchor() -> pathlib.Path | None:
    home = os.environ.get('DSH_HOME') or str(
        pathlib.Path.home() / '.local/share/nova3-dsh/data')
    runtime = pathlib.Path(home).expanduser().parent / 'runtime'
    anchor = runtime / 'package.json'
    return anchor if anchor.is_file() else None


def loads(text: str) -> dict:
    try:                                   # preferred when the operator has it
        import yaml                        # type: ignore
        value = yaml.safe_load(text)
        return value if isinstance(value, dict) else {}
    except ImportError:
        pass
    anchor = _runtime_anchor()
    if anchor is None:
        raise YamlUnavailable('no PyYAML and no DSH runtime YAML parser')
    try:
        result = subprocess.run(['node', '-e', NODE_SCRIPT, str(anchor)],
                                input=text, capture_output=True, text=True,
                                timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise YamlUnavailable(str(exc)) from None
    if result.returncode:
        raise YamlUnavailable('the DSH YAML parser rejected this document')
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise YamlUnavailable('the DSH YAML parser returned no data') from None
    return value if isinstance(value, dict) else {}
