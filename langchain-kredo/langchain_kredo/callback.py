"""LangChain callback handler for tracking chain execution and building evidence.

Collects evidence about agent behavior without automatically submitting
attestations. The developer decides when and what to attest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


@dataclass
class ToolRecord:
    """Record of a single tool invocation within a chain."""

    name: str
    input_text: str
    output_text: str = ""
    error: Optional[str] = None
    start_ms: float = 0
    end_ms: float = 0

    @property
    def duration_ms(self) -> int:
        return int(self.end_ms - self.start_ms) if self.end_ms else 0


@dataclass
class ChainRecord:
    """Record of a complete chain execution."""

    run_id: str
    start_ms: float = 0
    end_ms: float = 0
    error: Optional[str] = None
    tools: list[ToolRecord] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return int(self.end_ms - self.start_ms) if self.end_ms else 0

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    @property
    def error_count(self) -> int:
        count = 1 if self.error else 0
        count += sum(1 for t in self.tools if t.error)
        return count

    @property
    def success_rate(self) -> float:
        total = 1 + len(self.tools)  # chain itself + each tool
        errors = self.error_count
        return (total - errors) / total

    def build_evidence_context(self) -> str:
        """Build a human-readable evidence string for attestation context."""
        parts = [f"Chain {self.run_id}"]
        parts.append(f"Duration: {self.duration_ms}ms")
        parts.append(f"Tools used: {self.tool_count}")
        parts.append(f"Success rate: {self.success_rate:.0%}")

        if self.tools:
            parts.append("Tool sequence:")
            for t in self.tools:
                status = "OK" if not t.error else f"ERROR: {t.error}"
                parts.append(f"  - {t.name} ({t.duration_ms}ms): {status}")

        if self.error:
            parts.append(f"Chain error: {self.error}")

        return "\n".join(parts)

    def build_artifacts(self) -> list[str]:
        """Build artifact URIs for attestation evidence."""
        return [f"chain:{self.run_id}"]


def _truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len characters."""
    s = str(text)
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


class KredoCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler that tracks chain execution for Kredo attestations.

    Tracks top-level chain start/end/error and tool invocations within chains.
    Produces ChainRecord objects with evidence-building methods.

    Usage:
        handler = KredoCallbackHandler()
        chain.invoke(input, config={"callbacks": [handler]})
        records = handler.get_records()  # Returns and clears completed records
    """

    def __init__(self) -> None:
        self._active_chains: dict[str, ChainRecord] = {}
        self._active_tools: dict[str, ToolRecord] = {}
        self._completed: list[ChainRecord] = []
        self._chain_depth: int = 0

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Track top-level chain starts only."""
        self._chain_depth += 1
        if self._chain_depth == 1:
            self._active_chains[str(run_id)] = ChainRecord(
                run_id=str(run_id),
                start_ms=time.monotonic() * 1000,
            )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Complete top-level chain tracking."""
        self._chain_depth = max(0, self._chain_depth - 1)
        rid = str(run_id)
        if rid in self._active_chains:
            record = self._active_chains.pop(rid)
            record.end_ms = time.monotonic() * 1000
            self._completed.append(record)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Record chain error."""
        self._chain_depth = max(0, self._chain_depth - 1)
        rid = str(run_id)
        if rid in self._active_chains:
            record = self._active_chains.pop(rid)
            record.end_ms = time.monotonic() * 1000
            record.error = _truncate(str(error))
            self._completed.append(record)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Track tool invocation within a chain."""
        tool_name = serialized.get("name", "unknown")
        self._active_tools[str(run_id)] = ToolRecord(
            name=tool_name,
            input_text=_truncate(str(input_str)),
            start_ms=time.monotonic() * 1000,
        )

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Complete tool tracking."""
        rid = str(run_id)
        if rid in self._active_tools:
            tool_rec = self._active_tools.pop(rid)
            tool_rec.output_text = _truncate(str(output))
            tool_rec.end_ms = time.monotonic() * 1000
            self._attach_tool(parent_run_id, tool_rec)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Record tool error."""
        rid = str(run_id)
        if rid in self._active_tools:
            tool_rec = self._active_tools.pop(rid)
            tool_rec.error = _truncate(str(error))
            tool_rec.end_ms = time.monotonic() * 1000
            self._attach_tool(parent_run_id, tool_rec)

    def _attach_tool(
        self, parent_run_id: Optional[UUID], tool_rec: ToolRecord,
    ) -> None:
        """Attach a tool record to its parent chain.

        Drops the record if the parent chain is not found, rather than
        attaching to an arbitrary chain (which would be incorrect under
        concurrency).
        """
        if parent_run_id:
            parent_id = str(parent_run_id)
            if parent_id in self._active_chains:
                self._active_chains[parent_id].tools.append(tool_rec)
                return
        # No matching parent — drop rather than misattach
        # This can happen if the tool runs outside a tracked chain

    def get_records(self) -> list[ChainRecord]:
        """Return and clear all completed chain records."""
        records = list(self._completed)
        self._completed.clear()
        return records

    def peek_records(self) -> list[ChainRecord]:
        """Return completed records without clearing them."""
        return list(self._completed)
