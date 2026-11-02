"""Synthetic adapter contract, not process isolation or native Agents integration."""
from types import SimpleNamespace
import subprocess

from dsh_runner import backends, profile as profiles


def _profile(name, workspace, member, verify):
    """Validate in-memory data without reading project configuration or fixtures."""
    return profiles.parse({
        "schema": profiles.SCHEMA,
        "name": name,
        "repo": f"synthetic/{name}",
        "workspace": str(workspace),
        "execution": {
            "backend": "headless",
            "isolation": "per-task-checkout",
            "timeout_s": 1200,
            "max_steps": 120,
            "worker_member": member,
        },
        "members": {
            member: {"preset": "standard", "permission": "workspace-write"},
        },
        "safety": {"safe_paths": ["src/"], "verify": [verify]},
    })


def test_exact_checkout_forwarding_across_independent_profiles(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("adapter contract must not use a real runtime or Host")

    # An injected runtime must bypass the lazy nova3_team.dsh_runtime import.
    monkeypatch.setattr(backends.HeadlessBackend, "_default_runner", forbidden)
    monkeypatch.setattr(backends.HostClient, "__init__", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    alpha = _profile("alpha", tmp_path / "alpha-workspace", "alpha-worker",
                     "python -m unittest discover -s alpha_tests")
    beta = _profile("beta", tmp_path / "beta-workspace", "beta-worker",
                    "python -m pytest beta_tests -q")
    assert alpha.repo != beta.repo
    assert alpha.workspace != beta.workspace
    assert alpha.members != beta.members
    assert alpha.worker.name != beta.worker.name
    assert alpha.safety.verify != beta.safety.verify

    requests = [
        {
            "prompt": "  Inspect synthetic alpha task.\nKeep checkout attribution.\n",
            "cwd": tmp_path / "alpha tasks" / "task-17",
            "log_path": tmp_path / "alpha logs" / "round-17.log",
            "timeout_s": 41,
        },
        {
            "prompt": "Inspect synthetic beta task.\nUse beta verification only.\n",
            "cwd": tmp_path / "beta tasks" / "task-29",
            "log_path": tmp_path / "beta logs" / "round-29.log",
            "timeout_s": 83,
        },
    ]
    outcomes = [
        SimpleNamespace(status="COMPLETED", session_id="alpha-session-17",
                        steps=3, tool_calls=7, elapsed_s=1.25, error_code=None),
        SimpleNamespace(status="COMPLETED", session_id="beta-session-29",
                        steps=11, tool_calls=19, elapsed_s=2.5, error_code=None),
    ]
    seen = []

    def record_run(**kwargs):
        seen.append(kwargs)
        return outcomes[len(seen) - 1]

    runtime = SimpleNamespace(run=record_run)
    # Construct both adapters before running either: construction must not replace
    # another adapter's project configuration. Both share the same recording sink.
    adapters = [backends.HeadlessBackend(alpha, runner=runtime),
                backends.HeadlessBackend(beta, runner=runtime)]
    results = [adapter.run(**request)
               for adapter, request in zip(adapters, requests)]

    assert len(seen) == 2
    for received, project, request in zip(seen, (alpha, beta), requests):
        assert received == {"profile": project, **request}
        assert received["profile"] is project
        assert received["cwd"] is request["cwd"]
        assert received["log_path"] is request["log_path"]
        assert str(request["cwd"]) != project.workspace
        assert request["timeout_s"] != project.execution.timeout_s

    # Check both results after both calls, catching reused or cross-wired state.
    assert results[0] is not results[1]
    for result, outcome in zip(results, outcomes):
        assert result.ok
        assert result.status == "completed"
        assert result.backend == "headless"
        assert result.visible_in_agents_tab is False
        assert result.session_id == outcome.session_id
        assert result.steps == outcome.steps
        assert result.tool_calls == outcome.tool_calls
        assert result.elapsed_s == outcome.elapsed_s
        assert result.detail == ""
