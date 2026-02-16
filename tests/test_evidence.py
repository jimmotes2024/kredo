"""Tests for kredo.evidence — quality scoring."""

from datetime import datetime, timedelta, timezone

from kredo.evidence import EvidenceScore, score_evidence
from kredo.models import AttestationType, Evidence


def _now():
    return datetime.now(timezone.utc)


class TestSpecificity:
    def test_empty_context_low_score(self):
        ev = Evidence(context="", artifacts=[])
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.specificity == 0.0

    def test_long_context_with_artifacts(self):
        ev = Evidence(
            context="A" * 600,
            artifacts=["chain:abc", "output:def", "log:ghi"],
            outcome="success",
        )
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.specificity > 0.7


class TestVerifiability:
    def test_no_artifacts_zero(self):
        ev = Evidence(context="test")
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.verifiability == 0.0

    def test_uri_artifacts_high(self):
        ev = Evidence(
            context="test",
            artifacts=["https://example.com/report", "chain:abc123"],
        )
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.verifiability > 0.5

    def test_non_uri_artifacts_lower(self):
        ev = Evidence(
            context="test",
            artifacts=["just some text", "another thing"],
        )
        score = score_evidence(ev, AttestationType.SKILL)
        # Has artifacts but no URI patterns
        assert 0.0 < score.verifiability < 0.6


class TestRecency:
    def test_recent_high(self):
        ref = _now()
        ev = Evidence(context="test", interaction_date=ref - timedelta(days=1))
        score = score_evidence(ev, AttestationType.SKILL, reference_date=ref)
        assert score.recency > 0.95

    def test_old_low(self):
        ref = _now()
        ev = Evidence(context="test", interaction_date=ref - timedelta(days=365))
        score = score_evidence(ev, AttestationType.SKILL, reference_date=ref)
        assert score.recency < 0.3

    def test_half_life(self):
        ref = _now()
        ev = Evidence(context="test", interaction_date=ref - timedelta(days=180))
        score = score_evidence(ev, AttestationType.SKILL, reference_date=ref)
        assert 0.45 < score.recency < 0.55  # Should be ~0.5

    def test_no_date_middle(self):
        ev = Evidence(context="test")
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.recency == 0.5


class TestComposite:
    def test_weights_sum_to_one(self):
        from kredo.evidence import _WEIGHTS
        assert abs(sum(_WEIGHTS.values()) - 1.0) < 0.001

    def test_good_evidence_high_composite(self):
        ev = Evidence(
            context="Collaborated on a complex incident. Agent performed IOC extraction, severity classification, and wrote the final report. Evidence is thorough and verifiable.",
            artifacts=["chain:abc123", "output:report-456", "https://evidence.com/full"],
            outcome="successful_resolution",
            interaction_date=_now() - timedelta(days=3),
        )
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.composite > 0.6

    def test_poor_evidence_low_composite(self):
        ev = Evidence(context="ok")
        score = score_evidence(ev, AttestationType.SKILL)
        assert score.composite < 0.4
