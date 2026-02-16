"""Evidence quality scoring — informational, does NOT block signing.

Four dimensions (0.0-1.0):
- Specificity: context length + artifact count
- Verifiability: artifact count + URI patterns
- Relevance: 1.0 in v1 (needs NLP for real assessment)
- Recency: exponential decay from interaction_date
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from kredo.models import AttestationType, Evidence


@dataclass
class EvidenceScore:
    """Evidence quality score across four dimensions."""
    specificity: float
    verifiability: float
    relevance: float
    recency: float
    composite: float

    def __repr__(self) -> str:
        return (
            f"EvidenceScore(specificity={self.specificity:.2f}, "
            f"verifiability={self.verifiability:.2f}, "
            f"relevance={self.relevance:.2f}, "
            f"recency={self.recency:.2f}, "
            f"composite={self.composite:.2f})"
        )


# URI-like patterns that suggest verifiable evidence
_URI_PATTERNS = [
    re.compile(r"^https?://"),
    re.compile(r"^chain:[a-zA-Z0-9]+"),
    re.compile(r"^output:[a-zA-Z0-9-]+"),
    re.compile(r"^post:[a-zA-Z0-9/.-]+"),
    re.compile(r"^commit:[a-f0-9]+"),
    re.compile(r"^pr:[a-zA-Z0-9/.-]+"),
    re.compile(r"^issue:[a-zA-Z0-9/.-]+"),
]

# Weights for composite score
_WEIGHTS = {
    "specificity": 0.30,
    "verifiability": 0.30,
    "relevance": 0.20,
    "recency": 0.20,
}

# Recency half-life in days — after this many days, recency score halves
_RECENCY_HALF_LIFE_DAYS = 180


def _score_specificity(evidence: Evidence) -> float:
    """Score based on context length and artifact count."""
    # Context length score: 0-500 chars = linear 0-0.5, 500+ = 0.5-1.0 (diminishing)
    ctx_len = len(evidence.context)
    if ctx_len <= 0:
        ctx_score = 0.0
    elif ctx_len <= 500:
        ctx_score = (ctx_len / 500) * 0.5
    else:
        ctx_score = 0.5 + min(0.5, (ctx_len - 500) / 2000)

    # Artifact count score: 0=0, 1=0.5, 2=0.75, 3+=1.0
    n_artifacts = len(evidence.artifacts)
    if n_artifacts == 0:
        art_score = 0.0
    elif n_artifacts == 1:
        art_score = 0.5
    elif n_artifacts == 2:
        art_score = 0.75
    else:
        art_score = 1.0

    # Outcome bonus: non-empty outcome adds 0.1
    outcome_bonus = 0.1 if evidence.outcome else 0.0

    return min(1.0, (ctx_score * 0.5 + art_score * 0.5) + outcome_bonus)


def _score_verifiability(evidence: Evidence) -> float:
    """Score based on artifact structure and URI patterns."""
    if not evidence.artifacts:
        return 0.0

    uri_count = 0
    for artifact in evidence.artifacts:
        for pattern in _URI_PATTERNS:
            if pattern.match(artifact):
                uri_count += 1
                break

    # Ratio of URI-matching artifacts
    uri_ratio = uri_count / len(evidence.artifacts)
    # Base from having artifacts at all
    base = min(0.5, len(evidence.artifacts) * 0.2)
    return min(1.0, base + uri_ratio * 0.5)


def _score_relevance(evidence: Evidence, attestation_type: AttestationType) -> float:
    """Score relevance of evidence to the attestation type.

    In v1, this returns 1.0 — real relevance scoring needs NLP.
    """
    return 1.0


def _score_recency(evidence: Evidence, reference_date: Optional[datetime] = None) -> float:
    """Score based on how recent the interaction was.

    Uses exponential decay with a 180-day half-life.
    """
    if evidence.interaction_date is None:
        return 0.5  # Unknown recency gets middle score

    ref = reference_date or datetime.now(timezone.utc)
    if evidence.interaction_date.tzinfo is None:
        interaction = evidence.interaction_date.replace(tzinfo=timezone.utc)
    else:
        interaction = evidence.interaction_date

    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    delta_days = (ref - interaction).total_seconds() / 86400
    if delta_days < 0:
        return 1.0  # Future date? Full score.

    # Exponential decay: score = 2^(-days/half_life)
    import math
    return math.pow(2, -delta_days / _RECENCY_HALF_LIFE_DAYS)


def score_evidence(
    evidence: Evidence,
    attestation_type: AttestationType,
    reference_date: Optional[datetime] = None,
) -> EvidenceScore:
    """Score evidence quality across four dimensions.

    Returns an EvidenceScore with per-dimension and composite scores.
    This is informational only — it does NOT block signing.
    """
    specificity = _score_specificity(evidence)
    verifiability = _score_verifiability(evidence)
    relevance = _score_relevance(evidence, attestation_type)
    recency = _score_recency(evidence, reference_date)

    composite = (
        _WEIGHTS["specificity"] * specificity
        + _WEIGHTS["verifiability"] * verifiability
        + _WEIGHTS["relevance"] * relevance
        + _WEIGHTS["recency"] * recency
    )

    return EvidenceScore(
        specificity=round(specificity, 4),
        verifiability=round(verifiability, 4),
        relevance=round(relevance, 4),
        recency=round(recency, 4),
        composite=round(composite, 4),
    )
