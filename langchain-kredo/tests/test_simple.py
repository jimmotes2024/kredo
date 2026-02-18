"""Tests for the dead-simple attest() interface."""

from unittest.mock import MagicMock, patch

import pytest

from langchain_kredo.simple import _resolve_skill, _resolve_subject, attest


class TestResolveSkill:
    def test_exact_domain_skill(self):
        domain, specific = _resolve_skill("security-operations/incident-triage")
        assert domain == "security-operations"
        assert specific == "incident-triage"

    def test_reverse_lookup(self):
        domain, specific = _resolve_skill("incident-triage")
        assert domain == "security-operations"
        assert specific == "incident-triage"

    def test_code_review_reverse_lookup(self):
        domain, specific = _resolve_skill("code-review")
        assert domain == "code-generation"
        assert specific == "code-review"

    def test_unknown_skill_raises(self):
        with pytest.raises(ValueError, match="Unknown skill"):
            _resolve_skill("quantum-teleportation")

    def test_domain_name_gives_helpful_error(self):
        with pytest.raises(ValueError, match="is a domain, not a skill"):
            _resolve_skill("security-operations")


class TestResolveSubject:
    def test_pubkey_passthrough(self, keypair):
        sk, pubkey = keypair
        client = MagicMock()
        client.get_profile.return_value = {"name": "test-agent"}

        resolved_pk, resolved_name = _resolve_subject(client, pubkey)
        assert resolved_pk == pubkey
        assert resolved_name == "test-agent"

    def test_pubkey_with_api_error(self, keypair):
        sk, pubkey = keypair
        client = MagicMock()
        client.get_profile.side_effect = Exception("not found")

        resolved_pk, resolved_name = _resolve_subject(client, pubkey)
        assert resolved_pk == pubkey
        assert resolved_name == ""

    def test_name_lookup(self):
        client = MagicMock()
        client.list_agents.return_value = [
            {"pubkey": "ed25519:" + "aa" * 32, "name": "Alice"},
            {"pubkey": "ed25519:" + "bb" * 32, "name": "Jim"},
        ]

        pubkey, name = _resolve_subject(client, "jim")
        assert pubkey == "ed25519:" + "bb" * 32
        assert name == "Jim"

    def test_name_lookup_case_insensitive(self):
        client = MagicMock()
        client.list_agents.return_value = [
            {"pubkey": "ed25519:" + "cc" * 32, "name": "Vanguard"},
        ]

        pubkey, name = _resolve_subject(client, "vanguard")
        assert pubkey == "ed25519:" + "cc" * 32

    def test_name_not_found_raises(self):
        client = MagicMock()
        client.list_agents.return_value = []

        with pytest.raises(ValueError, match="not found on the network"):
            _resolve_subject(client, "nobody")

    def test_duplicate_name_raises(self):
        """Ambiguous names should raise, not silently pick the first."""
        client = MagicMock()
        client.list_agents.return_value = [
            {"pubkey": "ed25519:" + "aa" * 32, "name": "Jim"},
            {"pubkey": "ed25519:" + "bb" * 32, "name": "jim"},
        ]

        with pytest.raises(ValueError, match="Ambiguous"):
            _resolve_subject(client, "jim")


class TestAttest:
    @patch("langchain_kredo.simple.KredoSigningClient")
    def test_full_flow(self, MockClient):
        client = MockClient.return_value
        client.list_agents.return_value = [
            {"pubkey": "ed25519:" + "ab" * 32, "name": "Jim"},
        ]
        client.attest_skill.return_value = {"id": "att-123"}

        result = attest(
            "jim", "incident-triage", "Triaged 3 incidents correctly",
            signer="aa" * 32,
        )

        assert result == "att-123"
        client.attest_skill.assert_called_once()
        call_kwargs = client.attest_skill.call_args[1]
        assert call_kwargs["domain"] == "security-operations"
        assert call_kwargs["skill"] == "incident-triage"
        assert call_kwargs["proficiency"] == 3
        assert call_kwargs["subject_name"] == "Jim"

    @patch("langchain_kredo.simple.KredoSigningClient")
    def test_url_becomes_artifact(self, MockClient):
        client = MockClient.return_value
        client.get_profile.return_value = {"name": ""}
        client.attest_skill.return_value = {"id": "att-456"}

        attest(
            "ed25519:" + "ab" * 32,
            "code-generation/code-review",
            "https://github.com/org/repo/pull/47",
            signer="aa" * 32,
        )

        call_kwargs = client.attest_skill.call_args[1]
        assert call_kwargs["artifacts"] == ["https://github.com/org/repo/pull/47"]
        assert call_kwargs["context"] == "https://github.com/org/repo/pull/47"

    @patch("langchain_kredo.simple.KredoSigningClient")
    def test_custom_proficiency(self, MockClient):
        client = MockClient.return_value
        client.get_profile.return_value = {"name": ""}
        client.attest_skill.return_value = {"id": "att-789"}

        attest(
            "ed25519:" + "ab" * 32,
            "code-review",
            "Excellent reviewer",
            proficiency=5,
            signer="aa" * 32,
        )

        call_kwargs = client.attest_skill.call_args[1]
        assert call_kwargs["proficiency"] == 5

    @patch("langchain_kredo.simple.KredoSigningClient")
    def test_uses_env_var_when_no_signer(self, MockClient):
        """When signer is None, KredoSigningClient picks up KREDO_PRIVATE_KEY."""
        client = MockClient.return_value
        client.get_profile.return_value = {"name": ""}
        client.attest_skill.return_value = {"id": "att-env"}

        attest("ed25519:" + "ab" * 32, "code-review", "Good work")

        MockClient.assert_called_once_with(signing_key=None)
