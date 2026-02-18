"""Tests for KredoTrustGate."""

from unittest.mock import MagicMock

import pytest

from langchain_kredo.trust_gate import (
    InsufficientTrustError,
    KredoTrustGate,
    TrustCheckResult,
)


@pytest.fixture
def mock_client():
    return MagicMock()


class TestCheck:
    def test_passes_when_above_threshold(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.3)
        result = gate.check(mock_profile["pubkey"])

        assert result.passed is True
        assert result.score == 0.45
        assert result.required == 0.3

    def test_fails_when_below_threshold(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.8)
        result = gate.check(mock_profile["pubkey"])

        assert result.passed is False
        assert result.score == 0.45
        assert result.required == 0.8

    def test_override_min_score(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.8)
        # Override with lower threshold
        result = gate.check(mock_profile["pubkey"], min_score=0.1)
        assert result.passed is True
        assert result.required == 0.1

    def test_api_error_returns_failed(self, mock_client):
        mock_client.get_profile.side_effect = Exception("connection error")
        gate = KredoTrustGate(mock_client, min_score=0.0)
        result = gate.check("ed25519:" + "ab" * 32)

        assert result.passed is False
        assert result.score == 0.0

    def test_zero_threshold_passes_anyone(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.0)
        result = gate.check(mock_profile["pubkey"])
        assert result.passed is True


class TestBlockWarned:
    def test_block_warned_fails_warned_agent(self, mock_client, warned_profile):
        mock_client.get_profile.return_value = warned_profile
        gate = KredoTrustGate(mock_client, min_score=0.0, block_warned=True)
        result = gate.check(warned_profile["pubkey"])

        assert result.passed is False
        assert result.has_warnings is True
        assert result.warning_count == 1

    def test_no_block_warned_passes_warned_agent(self, mock_client, warned_profile):
        mock_client.get_profile.return_value = warned_profile
        gate = KredoTrustGate(mock_client, min_score=0.0, block_warned=False)
        result = gate.check(warned_profile["pubkey"])

        assert result.passed is True
        assert result.has_warnings is True


class TestEnforce:
    def test_raises_on_failure(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.9)

        with pytest.raises(InsufficientTrustError) as exc_info:
            gate.enforce(mock_profile["pubkey"])

        assert exc_info.value.result.score == 0.45
        assert exc_info.value.result.required == 0.9

    def test_returns_result_on_success(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.1)
        result = gate.enforce(mock_profile["pubkey"])
        assert result.passed is True


class TestRequireDecorator:
    def test_decorator_allows_trusted(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.1)

        @gate.require(min_score=0.1)
        def sensitive_op(pubkey: str):
            return "success"

        assert sensitive_op(mock_profile["pubkey"]) == "success"

    def test_decorator_blocks_untrusted(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.1)

        @gate.require(min_score=0.9)
        def sensitive_op(pubkey: str):
            return "success"

        with pytest.raises(InsufficientTrustError):
            sensitive_op(mock_profile["pubkey"])

    def test_decorator_preserves_function_name(self, mock_client):
        gate = KredoTrustGate(mock_client)

        @gate.require(min_score=0.5)
        def my_function(pubkey: str):
            """My docstring."""
            pass

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."


class TestSelectBest:
    def test_selects_highest_score(self, mock_client):
        profiles = {
            "ed25519:" + "aa" * 32: _profile(score=0.3),
            "ed25519:" + "bb" * 32: _profile(score=0.7),
            "ed25519:" + "cc" * 32: _profile(score=0.5),
        }
        mock_client.get_profile.side_effect = lambda pk: profiles[pk]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.select_best(list(profiles.keys()))
        assert result is not None
        assert result.score == 0.7

    def test_filters_by_domain(self, mock_client):
        profiles = {
            "ed25519:" + "aa" * 32: _profile(
                score=0.8, skills=[{"domain": "nlp", "specific": "ner"}]
            ),
            "ed25519:" + "bb" * 32: _profile(
                score=0.5, skills=[{"domain": "security", "specific": "pentest"}]
            ),
        }
        mock_client.get_profile.side_effect = lambda pk: profiles[pk]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.select_best(list(profiles.keys()), domain="security")
        assert result is not None
        assert result.score == 0.5  # The NLP agent is filtered out

    def test_filters_by_skill(self, mock_client):
        profiles = {
            "ed25519:" + "aa" * 32: _profile(
                score=0.8, skills=[{"domain": "security", "specific": "pentest"}]
            ),
            "ed25519:" + "bb" * 32: _profile(
                score=0.5, skills=[{"domain": "security", "specific": "vuln_assessment"}]
            ),
        }
        mock_client.get_profile.side_effect = lambda pk: profiles[pk]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.select_best(
            list(profiles.keys()), domain="security", skill="vuln_assessment"
        )
        assert result is not None
        assert result.score == 0.5

    def test_returns_none_if_none_pass(self, mock_client):
        profiles = {
            "ed25519:" + "aa" * 32: _profile(score=0.1),
            "ed25519:" + "bb" * 32: _profile(score=0.2),
        }
        mock_client.get_profile.side_effect = lambda pk: profiles[pk]
        gate = KredoTrustGate(mock_client, min_score=0.9)

        result = gate.select_best(list(profiles.keys()))
        assert result is None

    def test_empty_candidates(self, mock_client):
        gate = KredoTrustGate(mock_client, min_score=0.0)
        result = gate.select_best([])
        assert result is None

    def test_prefers_domain_proficiency_over_raw_score(self, mock_client):
        """Agent with lower overall score but higher domain proficiency should win."""
        pk_a = "ed25519:" + "aa" * 32
        pk_b = "ed25519:" + "bb" * 32
        profiles = {
            pk_a: _profile(
                score=0.8, attestor_count=1,
                skills=[{"domain": "security-operations", "specific": "incident-triage",
                         "weighted_avg_proficiency": 2.0, "max_proficiency": 2}],
            ),
            pk_b: _profile(
                score=0.4, attestor_count=5,
                skills=[{"domain": "security-operations", "specific": "incident-triage",
                         "weighted_avg_proficiency": 4.5, "max_proficiency": 5}],
            ),
        }
        mock_client.get_profile.side_effect = lambda pk: profiles[pk]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.select_best(
            [pk_a, pk_b], domain="security-operations", skill="incident-triage"
        )
        assert result is not None
        # pk_b wins: lower rep but much higher domain proficiency + diversity
        assert result.pubkey == pk_b

    def test_diversity_breaks_tie(self, mock_client):
        """Agent with more attestors ranks higher when scores are similar."""
        pk_a = "ed25519:" + "aa" * 32
        pk_b = "ed25519:" + "bb" * 32
        profiles = {
            pk_a: _profile(score=0.5, attestor_count=1),
            pk_b: _profile(score=0.5, attestor_count=8),
        }
        mock_client.get_profile.side_effect = lambda pk: profiles[pk]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.select_best([pk_a, pk_b])
        assert result is not None
        assert result.pubkey == pk_b

    def test_attestor_count_populated(self, mock_client, mock_profile):
        mock_client.get_profile.return_value = mock_profile
        gate = KredoTrustGate(mock_client, min_score=0.0)
        result = gate.check(mock_profile["pubkey"])
        assert result.attestor_count == 2  # Two unique pubkeys in trust_network

    def test_duplicate_attestors_counted_once(self, mock_client):
        """Same pubkey appearing twice should only count once."""
        dup_pk = "ed25519:" + "cd" * 32
        profile = {
            "pubkey": "ed25519:" + "ab" * 32,
            "name": "test",
            "type": "agent",
            "skills": [],
            "attestation_count": {"total": 2, "by_agents": 2, "by_humans": 0},
            "warnings": [],
            "evidence_quality_avg": 0.5,
            "trust_network": [
                {"pubkey": dup_pk, "type": "agent",
                 "attestation_count_for_subject": 1, "attestor_own_attestation_count": 5},
                {"pubkey": dup_pk, "type": "agent",
                 "attestation_count_for_subject": 1, "attestor_own_attestation_count": 5},
            ],
            "trust_analysis": {"reputation_score": 0.5, "ring_flags": []},
        }
        mock_client.get_profile.return_value = profile
        gate = KredoTrustGate(mock_client, min_score=0.0)
        result = gate.check(profile["pubkey"])
        assert result.attestor_count == 1  # Deduplicated

    def test_none_score_treated_as_zero(self, mock_client):
        """None/missing reputation_score should not crash."""
        profile = {
            "pubkey": "ed25519:" + "ab" * 32,
            "name": "test",
            "type": "agent",
            "skills": [],
            "attestation_count": {"total": 0, "by_agents": 0, "by_humans": 0},
            "warnings": [],
            "evidence_quality_avg": 0.0,
            "trust_network": [],
            "trust_analysis": {"reputation_score": None, "ring_flags": []},
        }
        mock_client.get_profile.return_value = profile
        gate = KredoTrustGate(mock_client, min_score=0.0)
        result = gate.check(profile["pubkey"])
        assert result.score == 0.0
        assert result.passed is True


class TestShouldDelegate:
    def test_delegates_when_candidate_better(self, mock_client):
        pk = "ed25519:" + "aa" * 32
        profiles = {
            pk: _profile(
                score=0.5, attestor_count=3,
                skills=[{"domain": "security-operations", "specific": "incident-triage",
                         "weighted_avg_proficiency": 4.0, "max_proficiency": 4}],
            ),
        }
        mock_client.get_profile.side_effect = lambda p: profiles[p]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.should_delegate(
            [pk], domain="security-operations", skill="incident-triage",
            self_proficiency=2,
        )
        assert result is not None
        assert result.pubkey == pk

    def test_self_compute_when_self_proficiency_higher(self, mock_client):
        pk = "ed25519:" + "aa" * 32
        profiles = {
            pk: _profile(
                score=0.5, attestor_count=3,
                skills=[{"domain": "security-operations", "specific": "incident-triage",
                         "weighted_avg_proficiency": 2.0, "max_proficiency": 2}],
            ),
        }
        mock_client.get_profile.side_effect = lambda p: profiles[p]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.should_delegate(
            [pk], domain="security-operations", skill="incident-triage",
            self_proficiency=4,
        )
        assert result is None

    def test_self_compute_when_no_candidates_pass(self, mock_client):
        pk = "ed25519:" + "aa" * 32
        profiles = {pk: _profile(score=0.1)}
        mock_client.get_profile.side_effect = lambda p: profiles[p]
        gate = KredoTrustGate(mock_client, min_score=0.9)

        result = gate.should_delegate(
            [pk], domain="security-operations", self_proficiency=0,
        )
        assert result is None

    def test_delegates_when_self_proficiency_zero(self, mock_client):
        pk = "ed25519:" + "aa" * 32
        profiles = {
            pk: _profile(
                score=0.5, attestor_count=2,
                skills=[{"domain": "nlp", "specific": "ner",
                         "weighted_avg_proficiency": 1.0, "max_proficiency": 1}],
            ),
        }
        mock_client.get_profile.side_effect = lambda p: profiles[p]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.should_delegate(
            [pk], domain="nlp", skill="ner", self_proficiency=0,
        )
        assert result is not None  # Even proficiency 1 > 0

    def test_equal_proficiency_no_delegation(self, mock_client):
        pk = "ed25519:" + "aa" * 32
        profiles = {
            pk: _profile(
                score=0.5, attestor_count=3,
                skills=[{"domain": "security-operations", "specific": "log-analysis",
                         "weighted_avg_proficiency": 3.0, "max_proficiency": 3}],
            ),
        }
        mock_client.get_profile.side_effect = lambda p: profiles[p]
        gate = KredoTrustGate(mock_client, min_score=0.1)

        result = gate.should_delegate(
            [pk], domain="security-operations", skill="log-analysis",
            self_proficiency=3,
        )
        assert result is None  # Equal — not strictly better


def _profile(
    score: float = 0.5,
    skills: list = None,
    warnings: list = None,
    attestor_count: int = 0,
) -> dict:
    """Build a mock profile response."""
    trust_network = [
        {"pubkey": f"ed25519:{'%02x' % i * 32}", "type": "agent",
         "attestation_count_for_subject": 1, "attestor_own_attestation_count": 1}
        for i in range(attestor_count)
    ]
    return {
        "pubkey": "ed25519:" + "ab" * 32,
        "name": "test",
        "type": "agent",
        "skills": skills or [],
        "attestation_count": {"total": 1, "by_agents": 1, "by_humans": 0},
        "warnings": warnings or [],
        "evidence_quality_avg": 0.5,
        "trust_network": trust_network,
        "trust_analysis": {
            "reputation_score": score,
            "ring_flags": [],
        },
    }
