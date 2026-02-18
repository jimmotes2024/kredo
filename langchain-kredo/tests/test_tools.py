"""Tests for LangChain tools."""

import json
from unittest.mock import MagicMock

import pytest

from langchain_kredo.tools import (
    KredoCheckTrustTool,
    KredoGetTaxonomyTool,
    KredoSearchAttestationsTool,
    KredoSubmitAttestationTool,
)


@pytest.fixture
def mock_client():
    return MagicMock()


class TestKredoCheckTrustTool:
    def test_returns_profile_json(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        tool = KredoCheckTrustTool(client=mock_client)
        result = tool._run(pubkey="ed25519:" + "ab" * 32)

        parsed = json.loads(result)
        assert parsed["name"] == "test-agent"
        assert parsed["trust_analysis"]["reputation_score"] == 0.45

    def test_error_returns_structured_envelope(self, mock_client):
        mock_client.get_profile.side_effect = Exception("Connection refused")
        tool = KredoCheckTrustTool(client=mock_client)
        result = tool._run(pubkey="ed25519:" + "ab" * 32)

        parsed = json.loads(result)
        assert parsed["error"] is True
        assert parsed["operation"] == "check_trust"
        assert "Connection refused" in parsed["message"]

    def test_tool_name(self, mock_client):
        tool = KredoCheckTrustTool(client=mock_client)
        assert tool.name == "kredo_check_trust"


class TestKredoSearchAttestationsTool:
    def test_returns_search_results(self, mock_client):
        mock_client.search.return_value = {
            "attestations": [{"id": "att-1", "type": "skill_attestation"}],
            "total": 1,
        }
        tool = KredoSearchAttestationsTool(client=mock_client)
        result = tool._run(domain="security", min_proficiency=3)

        parsed = json.loads(result)
        assert parsed["total"] == 1
        mock_client.search.assert_called_once_with(
            domain="security",
            skill=None,
            min_proficiency=3,
            subject=None,
            attestor=None,
        )

    def test_error_returns_structured_envelope(self, mock_client):
        mock_client.search.side_effect = Exception("timeout")
        tool = KredoSearchAttestationsTool(client=mock_client)
        result = tool._run(domain="security")

        parsed = json.loads(result)
        assert parsed["error"] is True
        assert parsed["operation"] == "search_attestations"

    def test_tool_name(self, mock_client):
        tool = KredoSearchAttestationsTool(client=mock_client)
        assert tool.name == "kredo_search_attestations"


class TestKredoSubmitAttestationTool:
    def test_default_returns_preview(self, mock_client):
        """By default, submit tool returns a preview for human approval."""
        tool = KredoSubmitAttestationTool(client=mock_client)
        result = tool._run(
            subject_pubkey="ed25519:" + "ab" * 32,
            domain="security-operations",
            skill="incident-triage",
            proficiency=4,
            context="Excellent triage demonstrated",
        )

        parsed = json.loads(result)
        assert parsed["preview"] is True
        assert "human approval" in parsed["message"].lower()
        assert parsed["attestation"]["domain"] == "security-operations"
        mock_client.attest_skill.assert_not_called()

    def test_submits_when_approval_disabled(self, mock_client):
        mock_client.attest_skill.return_value = {"id": "new-att-id", "status": "created"}
        tool = KredoSubmitAttestationTool(client=mock_client, require_human_approval=False)
        result = tool._run(
            subject_pubkey="ed25519:" + "ab" * 32,
            domain="security-operations",
            skill="incident-triage",
            proficiency=4,
            context="Excellent triage demonstrated",
        )

        parsed = json.loads(result)
        assert parsed["id"] == "new-att-id"
        mock_client.attest_skill.assert_called_once()

    def test_error_returns_structured_envelope(self, mock_client):
        mock_client.attest_skill.side_effect = ValueError("Signing key required")
        tool = KredoSubmitAttestationTool(client=mock_client, require_human_approval=False)
        result = tool._run(
            subject_pubkey="ed25519:" + "ab" * 32,
            domain="security-operations",
            skill="incident-triage",
            proficiency=3,
            context="test",
        )

        parsed = json.loads(result)
        assert parsed["error"] is True
        assert parsed["type"] == "ValueError"

    def test_tool_name(self, mock_client):
        tool = KredoSubmitAttestationTool(client=mock_client)
        assert tool.name == "kredo_submit_attestation"


class TestKredoGetTaxonomyTool:
    def test_returns_taxonomy(self, mock_client):
        taxonomy = {
            "version": "1.0",
            "domains": [{"id": "security", "label": "Security"}],
        }
        mock_client.get_taxonomy.return_value = taxonomy
        tool = KredoGetTaxonomyTool(client=mock_client)
        result = tool._run()

        parsed = json.loads(result)
        assert parsed["version"] == "1.0"
        assert len(parsed["domains"]) == 1

    def test_tool_name(self, mock_client):
        tool = KredoGetTaxonomyTool(client=mock_client)
        assert tool.name == "kredo_get_taxonomy"

    def test_tool_descriptions_are_nonempty(self, mock_client):
        """All tools should have meaningful descriptions."""
        tools = [
            KredoCheckTrustTool(client=mock_client),
            KredoSearchAttestationsTool(client=mock_client),
            KredoSubmitAttestationTool(client=mock_client),
            KredoGetTaxonomyTool(client=mock_client),
        ]
        for tool in tools:
            assert len(tool.description) > 20
            assert tool.name.startswith("kredo_")
