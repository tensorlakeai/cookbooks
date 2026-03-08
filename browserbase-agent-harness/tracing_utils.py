"""Small, readable helpers for run trace collection."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from agents.tracing.spans import Span
    from agents.tracing.traces import Trace

try:
    from agents.tracing.processor_interface import TracingProcessor
except Exception:  # pragma: no cover - safe fallback for environments without agents SDK
    class TracingProcessor:  # type: ignore[override]
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_for_trace(value: Any, depth: int = 0) -> Any:
    """Sanitize nested values for trace output and avoid oversized payloads."""
    if depth > 4:
        return "<max-depth>"

    if isinstance(value, dict):
        masked: dict[Any, Any] = {}
        for key, val in value.items():
            key_l = str(key).lower()
            if any(token in key_l for token in ["api_key", "token", "secret", "password"]):
                masked[key] = "***redacted***"
            else:
                masked[key] = sanitize_for_trace(val, depth + 1)
        return masked

    if isinstance(value, list):
        if len(value) > 30:
            return [sanitize_for_trace(v, depth + 1) for v in value[:30]] + ["<truncated>"]
        return [sanitize_for_trace(v, depth + 1) for v in value]

    if isinstance(value, str):
        if len(value) > 800:
            return value[:800] + "...<truncated>"
        return value

    return value


def append_trace(
    *,
    traces: list[dict[str, Any]],
    enabled: bool,
    max_events: int,
    event: str,
    details: dict[str, Any],
) -> None:
    """Append one trace entry, respecting enabled and max-event guards."""
    if not enabled:
        return
    if len(traces) >= max_events:
        return

    entry = {
        "timestamp": _now_iso(),
        "event": event,
        "details": sanitize_for_trace(details),
    }
    traces.append(entry)
    print(f"[TRACE] {json.dumps(entry, ensure_ascii=True)}")


def _decode_json_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except Exception:
        return value


def _normalize_tool_args(raw_input: Any) -> dict[str, Any]:
    payload = _decode_json_maybe(raw_input)
    if isinstance(payload, dict):
        return payload
    if payload in (None, ""):
        return {}
    return {"value": payload}


def _summarize_tool_result(raw_output: Any) -> dict[str, Any]:
    payload = _decode_json_maybe(raw_output)
    if isinstance(payload, dict):
        summary: dict[str, Any] = {}
        for key in [
            "success",
            "error",
            "url",
            "title",
            "count",
            "indexed_pages",
            "errors",
            "message",
            "search_url",
            "filename",
        ]:
            if key in payload:
                summary[key] = payload[key]
        if "links" in payload:
            summary["links_count"] = len(payload.get("links", []))
        if "snippets" in payload:
            summary["snippets_count"] = len(payload.get("snippets", []))
        if "results" in payload:
            summary["results_count"] = len(payload.get("results", []))
        if "files" in payload:
            summary["files_count"] = len(payload.get("files", []))
        if "text" in payload:
            summary["text_chars"] = len(payload.get("text", "") or "")
        if "markdown" in payload:
            summary["markdown_chars"] = len(payload.get("markdown", "") or "")
        if not summary:
            summary["payload_type"] = "dict"
        return summary

    if payload in (None, ""):
        return {}
    return {"output_preview": payload}


@dataclass
class _TraceSink:
    traces: list[dict[str, Any]]
    max_events: int
    run_id: str
    workflow_name: str
    group_id: str
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, event: str, details: dict[str, Any]) -> None:
        with self.lock:
            append_trace(
                traces=self.traces,
                enabled=True,
                max_events=self.max_events,
                event=event,
                details=details,
            )


_TRACE_SINKS: dict[str, _TraceSink] = {}
_TRACE_SINKS_LOCK = threading.Lock()
_PROCESSOR_REGISTERED = False
_PROCESSOR_REGISTERED_LOCK = threading.Lock()


def _get_trace_sink(trace_id: str | None) -> _TraceSink | None:
    if not trace_id:
        return None
    with _TRACE_SINKS_LOCK:
        return _TRACE_SINKS.get(trace_id)


class _OpenAIAgentsTraceBridgeProcessor(TracingProcessor):
    """Bridge OpenAI Agents trace callbacks into harness trace events."""

    def on_trace_start(self, trace: "Trace") -> None:
        sink = _get_trace_sink(trace.trace_id)
        if sink is None:
            return
        sink.append(
            "agent_trace_started",
            {
                "trace_id": trace.trace_id,
                "workflow_name": trace.name,
                "group_id": getattr(trace, "group_id", None),
                "run_id": sink.run_id,
            },
        )

    def on_trace_end(self, trace: "Trace") -> None:
        sink = _get_trace_sink(trace.trace_id)
        if sink is None:
            return
        sink.append(
            "agent_trace_completed",
            {
                "trace_id": trace.trace_id,
                "workflow_name": trace.name,
                "group_id": getattr(trace, "group_id", None),
                "run_id": sink.run_id,
            },
        )

    def on_span_start(self, span: "Span[Any]") -> None:
        sink = _get_trace_sink(span.trace_id)
        if sink is None:
            return
        exported = span.export() or {}
        span_data = exported.get("span_data", {})
        if not isinstance(span_data, dict) or span_data.get("type") != "function":
            return
        sink.append(
            "agent_tool_called",
            {
                "tool_name": str(span_data.get("name", "")),
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_id": span.parent_id,
            },
        )

    def on_span_end(self, span: "Span[Any]") -> None:
        sink = _get_trace_sink(span.trace_id)
        if sink is None:
            return

        exported = span.export() or {}
        span_data = exported.get("span_data", {})
        if not isinstance(span_data, dict) or span_data.get("type") != "function":
            return

        details: dict[str, Any] = {
            "tool_name": str(span_data.get("name", "")),
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_id": span.parent_id,
        }

        args = _normalize_tool_args(span_data.get("input"))
        if args:
            details["args"] = args

        result_summary = _summarize_tool_result(span_data.get("output"))
        if result_summary:
            details["result_summary"] = result_summary

        if span.error:
            details["span_error"] = span.error

        sink.append("agent_tool_result", details)

    def shutdown(self) -> None:
        return

    def force_flush(self) -> None:
        return


def ensure_agents_trace_bridge_processor() -> None:
    global _PROCESSOR_REGISTERED
    if _PROCESSOR_REGISTERED:
        return

    with _PROCESSOR_REGISTERED_LOCK:
        if _PROCESSOR_REGISTERED:
            return
        from agents.tracing import add_trace_processor

        add_trace_processor(_OpenAIAgentsTraceBridgeProcessor())
        _PROCESSOR_REGISTERED = True


@contextmanager
def register_agents_trace_sink(
    *,
    enabled: bool,
    trace_id: str | None,
    run_id: str,
    workflow_name: str,
    group_id: str,
    traces: list[dict[str, Any]],
    max_events: int,
) -> Iterator[None]:
    """Register one run sink so processor callbacks map to this run's trace list."""
    if not enabled or not trace_id:
        yield
        return

    ensure_agents_trace_bridge_processor()
    sink = _TraceSink(
        traces=traces,
        max_events=max_events,
        run_id=run_id,
        workflow_name=workflow_name,
        group_id=group_id,
    )
    with _TRACE_SINKS_LOCK:
        _TRACE_SINKS[trace_id] = sink

    try:
        yield
    finally:
        with _TRACE_SINKS_LOCK:
            _TRACE_SINKS.pop(trace_id, None)
