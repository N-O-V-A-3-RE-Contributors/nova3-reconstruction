"""Native JSONL integration protocol tests. No provider/network calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


HERE = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = load("nova_test_native_planner", "planner_transport.py")
chat = load("nova_test_native_planner_chat", "planning_chat.py")


class NativePlannerTests(unittest.TestCase):
    def setUp(self):
        self.runtime = transport._runtime()
        self.status = self.runtime.HermesStatus(
            self.runtime.RuntimeState.AVAILABLE, "/fixture/hermes", "1.0.0",
            self.runtime.REQUIRED_CAPABILITIES, probed_at=int(transport.time.time()))
        self.calls = []

    def adapter(self, events=None, *, script=None, timeout=5):
        if script is None:
            script = "\n".join("print(" + repr(json.dumps(event)) + ", flush=True)"
                               for event in (events or []))

        def spawn(argv, **kwargs):
            self.calls.append({"argv": argv, "kwargs": kwargs,
                               "prompt": Path(argv[argv.index("--query-file") + 1]).read_text(),
                               "mode": os.stat(argv[argv.index("--query-file") + 1]).st_mode & 0o777})
            return subprocess.Popen([sys.executable, "-c", script], **kwargs)

        return transport.HermesPlanner(
            env={"PATH": os.environ.get("PATH", ""),
                 "NOVA3_HERMES_PLANNER_MODEL": "fixture/test-model"},
            probe=lambda _: self.status, popen=spawn, timeout=timeout,
            cancelled_error=chat.Cancelled)

    def invoke(self, adapter, cancelled=None):
        events = []
        result = adapter.stream("Owner message", {"ledger": {"accepted_decisions": ["Retain fact"]}},
                                emit=events.append, cancelled=cancelled or threading.Event(),
                                provider="hermes", model="fixture/test-model")
        return result, events

    def test_real_subprocess_stream_protocol_and_role_isolation(self):
        adapter = self.adapter([
            {"type": "system", "subtype": "init"},
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "owner"},
            {"type": "result", "exit_code": 0, "text": "Hello owner"}])
        self.assertEqual(adapter.state, "UNKNOWN")
        result, events = self.invoke(adapter)
        self.assertEqual(result, "Hello owner")
        self.assertEqual("".join(e["text"] for e in events), result)
        self.assertEqual(adapter.state, "CONNECTED")
        call = self.calls[0]
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--toolsets") + 1], "")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertIn("--safe-mode", argv)
        self.assertNotIn("--resume", argv)
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("Owner message", argv)
        self.assertIn("Retain fact", call["prompt"])
        self.assertIn("Owner message", call["prompt"])
        self.assertEqual(call["mode"], 0o600)
        self.assertEqual(call["kwargs"]["env"]["HERMES_NOVA3_ROLE"], "planner")
        self.assertEqual(call["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertEqual(call["kwargs"]["stderr"], subprocess.DEVNULL)
        self.assertTrue(call["kwargs"]["start_new_session"])
        self.assertFalse(Path(call["kwargs"]["cwd"]).exists())

    def test_fallback_final_text_emitted_when_model_does_not_stream(self):
        reply, events = self.invoke(self.adapter([
            {"type": "result", "exit_code": 0, "text": "Final answer"}]))
        self.assertEqual(reply, "Final answer")
        self.assertEqual(events, [{"kind": "delta", "text": "Final answer"}])

    def test_missing_or_failed_result_is_not_connected(self):
        for events in [[{"type": "text", "text": "Partial"}],
                       [{"type": "result", "exit_code": 1, "text": "", "error": "private vendor text"}]]:
            with self.subTest(events=events):
                adapter = self.adapter(events)
                with self.assertRaises(transport.TransportError) as caught:
                    self.invoke(adapter)
                self.assertNotIn("private vendor text", str(caught.exception))
                self.assertEqual(adapter.state, "UNAVAILABLE")

    def test_tools_fail_closed_and_no_tool_output_is_forwarded(self):
        adapter = self.adapter([{"type": "tool_use", "name": "terminal", "input": {"cmd": "secret"}}])
        with self.assertRaisesRegex(transport.TransportError, "tools disabled"):
            self.invoke(adapter)

    def test_invalid_protocol_rejected(self):
        with self.assertRaisesRegex(transport.TransportError, "protocol"):
            self.invoke(self.adapter(script="print('not json', flush=True)"))

    def test_timeout_stops_only_own_child(self):
        adapter = self.adapter(script="import time; time.sleep(30)", timeout=1)
        with self.assertRaisesRegex(transport.TransportError, "timed out"):
            self.invoke(adapter)

    def test_cancel_stops_only_own_child(self):
        adapter = self.adapter(script="import time; time.sleep(30)")
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(chat.Cancelled):
            self.invoke(adapter, cancelled)

    def test_workspace_restart_recovers_context_using_real_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.adapter([{"type": "result", "exit_code": 0, "text": "Remembered"}])
            kwargs = dict(store_root=Path(directory) / "state", user="owner",
                          role_provider=lambda: {"allowed": True, "role": "ADMIN"})
            workspace = chat.ChatWorkspace(**kwargs, adapters={"hermes": first})
            workspace.create_thread("Continuity")
            workspace.pin_decision("The integration color is cobalt", "owner supplied fact")
            self.assertEqual(workspace.run_turn("Remember cobalt")["status"], "ok")
            workspace.close()
            second = self.adapter([{"type": "result", "exit_code": 0, "text": "Cobalt"}])
            restored = chat.ChatWorkspace(**kwargs, adapters={"hermes": second})
            self.assertEqual(restored.run_turn("What color?")["status"], "ok")
            self.assertIn("The integration color is cobalt", self.calls[-1]["prompt"])
            self.assertIn("Remember cobalt", self.calls[-1]["prompt"])
            self.assertEqual(restored.state()["thread"]["provider_state"], "CONNECTED")
            self.assertEqual(len(restored.transcript()), 4)
            restored.close()


if __name__ == "__main__":
    unittest.main()
