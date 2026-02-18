"""Tests for KredoCallbackHandler."""

import time
from uuid import uuid4

import pytest

from langchain_kredo.callback import (
    ChainRecord,
    KredoCallbackHandler,
    ToolRecord,
    _truncate,
)


@pytest.fixture
def handler():
    return KredoCallbackHandler()


def _uuid():
    return uuid4()


class TestChainLifecycle:
    def test_basic_chain_start_end(self, handler):
        run_id = _uuid()
        handler.on_chain_start({}, {}, run_id=run_id)
        handler.on_chain_end({}, run_id=run_id)

        records = handler.get_records()
        assert len(records) == 1
        assert records[0].run_id == str(run_id)
        assert records[0].duration_ms >= 0
        assert records[0].error is None

    def test_chain_error(self, handler):
        run_id = _uuid()
        handler.on_chain_start({}, {}, run_id=run_id)
        handler.on_chain_error(ValueError("test error"), run_id=run_id)

        records = handler.get_records()
        assert len(records) == 1
        assert records[0].error == "test error"

    def test_nested_chains_only_track_top_level(self, handler):
        outer_id = _uuid()
        inner_id = _uuid()

        handler.on_chain_start({}, {}, run_id=outer_id)
        handler.on_chain_start({}, {}, run_id=inner_id, parent_run_id=outer_id)
        handler.on_chain_end({}, run_id=inner_id, parent_run_id=outer_id)
        handler.on_chain_end({}, run_id=outer_id)

        records = handler.get_records()
        # Only the top-level chain is recorded
        assert len(records) == 1
        assert records[0].run_id == str(outer_id)

    def test_multiple_chains(self, handler):
        id1 = _uuid()
        id2 = _uuid()

        handler.on_chain_start({}, {}, run_id=id1)
        handler.on_chain_end({}, run_id=id1)
        handler.on_chain_start({}, {}, run_id=id2)
        handler.on_chain_end({}, run_id=id2)

        records = handler.get_records()
        assert len(records) == 2


class TestToolTracking:
    def test_tool_tracked_within_chain(self, handler):
        chain_id = _uuid()
        tool_id = _uuid()

        handler.on_chain_start({}, {}, run_id=chain_id)
        handler.on_tool_start(
            {"name": "kredo_check_trust"}, "test input",
            run_id=tool_id, parent_run_id=chain_id,
        )
        handler.on_tool_end("test output", run_id=tool_id, parent_run_id=chain_id)
        handler.on_chain_end({}, run_id=chain_id)

        records = handler.get_records()
        assert len(records) == 1
        assert records[0].tool_count == 1
        assert records[0].tools[0].name == "kredo_check_trust"
        assert records[0].tools[0].input_text == "test input"
        assert records[0].tools[0].output_text == "test output"

    def test_tool_error_tracked(self, handler):
        chain_id = _uuid()
        tool_id = _uuid()

        handler.on_chain_start({}, {}, run_id=chain_id)
        handler.on_tool_start(
            {"name": "kredo_submit_attestation"}, "input",
            run_id=tool_id, parent_run_id=chain_id,
        )
        handler.on_tool_error(
            RuntimeError("signing failed"),
            run_id=tool_id, parent_run_id=chain_id,
        )
        handler.on_chain_end({}, run_id=chain_id)

        records = handler.get_records()
        assert records[0].tools[0].error == "signing failed"
        assert records[0].error_count == 1

    def test_multiple_tools_in_chain(self, handler):
        chain_id = _uuid()

        handler.on_chain_start({}, {}, run_id=chain_id)
        for i in range(3):
            tid = _uuid()
            handler.on_tool_start(
                {"name": f"tool_{i}"}, f"input_{i}",
                run_id=tid, parent_run_id=chain_id,
            )
            handler.on_tool_end(f"output_{i}", run_id=tid, parent_run_id=chain_id)
        handler.on_chain_end({}, run_id=chain_id)

        records = handler.get_records()
        assert records[0].tool_count == 3


class TestEvidenceBuilding:
    def test_evidence_context_format(self):
        record = ChainRecord(
            run_id="test-run-123",
            start_ms=1000,
            end_ms=2500,
            tools=[
                ToolRecord(
                    name="kredo_check_trust",
                    input_text="check",
                    output_text="ok",
                    start_ms=1100,
                    end_ms=1200,
                ),
            ],
        )

        ctx = record.build_evidence_context()
        assert "Chain test-run-123" in ctx
        assert "Duration: 1500ms" in ctx
        assert "Tools used: 1" in ctx
        assert "Success rate: 100%" in ctx
        assert "kredo_check_trust" in ctx

    def test_evidence_context_with_error(self):
        record = ChainRecord(
            run_id="err-run",
            start_ms=0,
            end_ms=100,
            error="something broke",
        )

        ctx = record.build_evidence_context()
        assert "Chain error: something broke" in ctx

    def test_artifacts(self):
        record = ChainRecord(run_id="art-run")
        artifacts = record.build_artifacts()
        assert artifacts == ["chain:art-run"]

    def test_success_rate_all_ok(self):
        record = ChainRecord(
            run_id="ok-run",
            tools=[
                ToolRecord(name="t1", input_text=""),
                ToolRecord(name="t2", input_text=""),
            ],
        )
        assert record.success_rate == 1.0

    def test_success_rate_with_errors(self):
        record = ChainRecord(
            run_id="err-run",
            error="chain failed",
            tools=[
                ToolRecord(name="t1", input_text=""),
                ToolRecord(name="t2", input_text="", error="tool failed"),
            ],
        )
        # 3 total (chain + 2 tools), 2 errors → 1/3
        assert abs(record.success_rate - 1 / 3) < 0.01


class TestGetAndPeek:
    def test_get_clears_records(self, handler):
        run_id = _uuid()
        handler.on_chain_start({}, {}, run_id=run_id)
        handler.on_chain_end({}, run_id=run_id)

        records = handler.get_records()
        assert len(records) == 1
        assert len(handler.get_records()) == 0

    def test_peek_preserves_records(self, handler):
        run_id = _uuid()
        handler.on_chain_start({}, {}, run_id=run_id)
        handler.on_chain_end({}, run_id=run_id)

        assert len(handler.peek_records()) == 1
        assert len(handler.peek_records()) == 1  # Still there


class TestTruncation:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self):
        long = "x" * 600
        result = _truncate(long, max_len=500)
        assert len(result) == 503  # 500 + "..."
        assert result.endswith("...")
