"""Offline method-boundary tests; no Host, plugin, model or game inputs.

The runner is not bundled in every reconstruction checkout. Skip explicitly
when the local dsh_runner package is absent; never fetch it or start a Host.
These tests establish no game-behaviour or machine-code equivalence.
"""

import importlib
import importlib.util
import os
import socket
import subprocess
import urllib.request

import pytest


ENDPOINT = '/dsh-runner/api/session'
WORKSPACE = '/synthetic checkout/../task-228/naïve directory '
PROMPT = '  Keep this prompt exactly.\nUnicode: λ\tand trailing space. \n'
REQUIRED = {'workspacePath': WORKSPACE, 'prompt': PROMPT}
OPTIONAL_FIELDS = (
    ('permission', 'permission', 'workspace-write'),
    ('model', 'model', 'synthetic/model-228'),
    ('effort', 'reasoningEffort', 'high'),
    ('title', 'title', '  Offline session λ  '),
    ('agent_preset', 'agentPreset', 'synthetic-preset'),
)


@pytest.fixture(autouse=True)
def forbid_external_execution(monkeypatch):
    """Fail immediately if this method boundary escapes its recording fake."""
    def forbidden(*args, **kwargs):
        pytest.fail('Native session payload tests forbid network and process execution')

    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    for method in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
        if hasattr(socket.socket, method):
            monkeypatch.setattr(socket.socket, method, forbidden)
    for method in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, method, forbidden)
    monkeypatch.setattr(os, 'system', forbidden)
    monkeypatch.setattr(os, 'popen', forbidden)


@pytest.fixture
def recording_host(monkeypatch, forbid_external_execution):
    if importlib.util.find_spec('dsh_runner') is None:
        pytest.skip('Local dsh_runner package absent; HostClient boundary not tested')
    # Import errors inside an installed runner are failures, not missing-package skips.
    host = importlib.import_module('dsh_runner.host')
    calls = []
    response = {'sessionId': 'session-228'}

    def record_call(self, path, payload=None):
        calls.append((path, dict(payload)))
        return response

    monkeypatch.setattr(host.HostClient, '_call', record_call)
    client = host.HostClient(home='/synthetic/dsh-home')
    return client, calls, response, host.HostUnavailable


def test_required_payload_preserves_workspace_and_prompt(recording_host):
    client, calls, _, _ = recording_host

    assert client.create_session(workspace_path=WORKSPACE, prompt=PROMPT) == 'session-228'
    assert calls == [(ENDPOINT, REQUIRED)]


def test_all_populated_settings_use_exact_bridge_keys(recording_host):
    client, calls, _, _ = recording_host
    settings = {argument: value for argument, _, value in OPTIONAL_FIELDS}
    expected = dict(REQUIRED)
    expected.update({key: value for _, key, value in OPTIONAL_FIELDS})

    assert client.create_session(
        workspace_path=WORKSPACE, prompt=PROMPT, **settings
    ) == 'session-228'
    assert calls == [(ENDPOINT, expected)]


@pytest.mark.parametrize('argument,bridge_key,populated', OPTIONAL_FIELDS)
@pytest.mark.parametrize('value_kind', ('populated', 'none', 'empty'))
def test_optional_settings_map_or_are_omitted(
    recording_host, argument, bridge_key, populated, value_kind
):
    client, calls, _, _ = recording_host
    value = {'populated': populated, 'none': None, 'empty': ''}[value_kind]
    expected = dict(REQUIRED)
    if value_kind == 'populated':
        expected[bridge_key] = populated

    assert client.create_session(
        workspace_path=WORKSPACE, prompt=PROMPT, **{argument: value}
    ) == 'session-228'
    # Exact equality also rejects snake_case aliases, nulls and extra defaults.
    assert calls == [(ENDPOINT, expected)]


@pytest.mark.parametrize(
    'response,valid_id',
    [
        pytest.param({'sessionId': 'session-228'}, 'session-228', id='valid'),
        pytest.param({'sessionId': '  session-λ  '}, '  session-λ  ', id='unmodified-id'),
        pytest.param({}, None, id='missing'),
        pytest.param({'sessionId': None}, None, id='null'),
        pytest.param({'sessionId': ''}, None, id='empty'),
        pytest.param({'sessionId': 0}, None, id='zero'),
        pytest.param({'sessionId': 228}, None, id='integer'),
        pytest.param({'sessionId': 2.28}, None, id='float'),
        pytest.param({'sessionId': False}, None, id='false'),
        pytest.param({'sessionId': True}, None, id='true'),
        pytest.param({'sessionId': ['session-228']}, None, id='list'),
        pytest.param({'sessionId': {'id': 'session-228'}}, None, id='object'),
        pytest.param({'sessionId': b'session-228'}, None, id='bytes'),
    ],
)
def test_session_id_validation(recording_host, response, valid_id):
    client, calls, fake_response, unavailable = recording_host
    fake_response.clear()
    fake_response.update(response)

    if valid_id is None:
        with pytest.raises(unavailable):
            client.create_session(workspace_path=WORKSPACE, prompt=PROMPT)
    else:
        assert client.create_session(workspace_path=WORKSPACE, prompt=PROMPT) == valid_id
    assert calls == [(ENDPOINT, REQUIRED)]
