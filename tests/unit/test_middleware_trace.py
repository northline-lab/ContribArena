from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from contribarena.engine.middleware.trace import _safe_result, traced_tool
from contribarena.trace import TraceWriter


class _PydanticPayload(BaseModel):
    name: str
    count: int = 0


@dataclass
class _DataclassPayload:
    label: str


class SafeResultTest(unittest.TestCase):
    def test_returns_model_dump_for_pydantic_model(self) -> None:
        payload = _PydanticPayload(name="ok", count=2)

        result = _safe_result(payload)

        self.assertEqual({"name": "ok", "count": 2}, result)

    def test_returns_str_for_plain_object(self) -> None:
        self.assertEqual("42", _safe_result(42))
        self.assertEqual("hello", _safe_result("hello"))
        self.assertEqual("None", _safe_result(None))
        self.assertEqual(
            str(_DataclassPayload(label="a")),
            _safe_result(_DataclassPayload(label="a")),
        )

    def test_model_dump_is_used_when_attribute_present_on_non_pydantic(self) -> None:
        class _Shim:
            def model_dump(self, mode: str = "python") -> dict[str, object]:
                return {"mode": mode, "value": 1}

        result = _safe_result(_Shim())

        self.assertEqual({"mode": "json", "value": 1}, result)


class TracedToolTest(unittest.TestCase):
    def _writer(self, path: Path) -> TraceWriter:
        return TraceWriter(path, run_id="run-trace")

    def _events(self, path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def test_writes_started_and_finished_events_with_kwargs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)

            def add(a: int, b: int) -> int:
                return a + b

            wrapped = traced_tool(trace, "tool", "add", add)

            result = wrapped(1, b=2)

            self.assertEqual(3, result)
            events = self._events(path)
            self.assertEqual(2, len(events))
            started, finished = events
            self.assertEqual("tool", started["state"])
            self.assertEqual("add.started", started["event"])
            self.assertEqual(["1"], started["payload"]["args"])
            self.assertEqual({"b": 2}, started["payload"]["kwargs"])
            self.assertEqual("add.finished", finished["event"])
            self.assertEqual("3", finished["payload"]["result"])

    def test_finished_payload_uses_model_dump_for_pydantic_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)

            def build() -> _PydanticPayload:
                return _PydanticPayload(name="abc", count=7)

            wrapped = traced_tool(trace, "tool", "build", build)

            wrapped()

            _, finished = self._events(path)
            self.assertEqual(
                {"name": "abc", "count": 7},
                finished["payload"]["result"],
            )

    def test_started_args_are_stringified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)

            def echo(*args: object, **kwargs: object) -> str:
                return "ok"

            wrapped = traced_tool(trace, "phase", "echo", echo)

            wrapped(_DataclassPayload(label="x"), None, 0, flag=True)

            started, _ = self._events(path)
            self.assertEqual(
                [str(_DataclassPayload(label="x")), "None", "0"],
                started["payload"]["args"],
            )
            self.assertEqual({"flag": True}, started["payload"]["kwargs"])

    def test_no_kwargs_recorded_as_empty_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)

            def noop() -> None:
                return None

            wrapped = traced_tool(trace, "phase", "noop", noop)

            wrapped()

            started, finished = self._events(path)
            self.assertEqual([], started["payload"]["args"])
            self.assertEqual({}, started["payload"]["kwargs"])
            self.assertEqual("None", finished["payload"]["result"])

    def test_exception_propagates_and_finished_event_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)

            def boom() -> None:
                raise ValueError("explode")

            wrapped = traced_tool(trace, "phase", "boom", boom)

            with self.assertRaises(ValueError):
                wrapped()

            events = self._events(path)
            self.assertEqual(1, len(events))
            self.assertEqual("boom.started", events[0]["event"])

    def test_runs_inner_function_each_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)
            calls: list[int] = []

            def counter(value: int) -> int:
                calls.append(value)
                return value

            wrapped = traced_tool(trace, "tool", "counter", counter)

            wrapped(1)
            wrapped(2)

            self.assertEqual([1, 2], calls)
            events = self._events(path)
            self.assertEqual(4, len(events))
            self.assertEqual(
                ["counter.started", "counter.finished", "counter.started", "counter.finished"],
                [event["event"] for event in events],
            )

    def test_state_and_event_are_passed_through_to_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace = self._writer(path)

            def identity(value: int) -> int:
                return value

            wrapped = traced_tool(trace, "phase_abc", "identity_op", identity)

            wrapped(5)

            started, finished = self._events(path)
            self.assertEqual("phase_abc", started["state"])
            self.assertEqual("phase_abc", finished["state"])
            self.assertEqual("identity_op.started", started["event"])
            self.assertEqual("identity_op.finished", finished["event"])


if __name__ == "__main__":
    unittest.main()
