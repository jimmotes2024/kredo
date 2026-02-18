"""Trust score enforcement for LangChain agent pipelines.

Policy layer that checks agent reputation before allowing operations.
Non-throwing check(), throwing enforce(), decorator require(),
multi-candidate select_best(), and build-vs-buy should_delegate().
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Callable, Optional

from langchain_kredo._client import KredoSigningClient


@dataclass
class TrustCheckResult:
    """Result of a trust check."""

    pubkey: str
    score: float
    passed: bool
    required: float
    has_warnings: bool
    warning_count: int
    skills: list[dict] = field(default_factory=list)
    attestor_count: int = 0


class InsufficientTrustError(Exception):
    """Raised when an agent does not meet trust requirements."""

    def __init__(self, result: TrustCheckResult):
        self.result = result
        super().__init__(
            f"Agent {result.pubkey} has trust score {result.score:.4f}, "
            f"required {result.required:.4f}"
        )


def _skill_proficiency(skills: list[dict], domain: str, skill: Optional[str]) -> float:
    """Extract best matching proficiency from a skills list. Returns 0.0-5.0."""
    best = 0.0
    for s in skills:
        domain_ok = s.get("domain") == domain
        skill_ok = (not skill) or s.get("specific") == skill
        if domain_ok and skill_ok:
            prof = s.get("weighted_avg_proficiency", s.get("max_proficiency", 0))
            best = max(best, float(prof))
    return best


class KredoTrustGate:
    """Policy enforcement layer for agent trust scores.

    Usage:
        gate = KredoTrustGate(client, min_score=0.3, block_warned=True)

        # Non-throwing check
        result = gate.check("ed25519:abc...")

        # Throwing enforcement
        result = gate.enforce("ed25519:abc...")  # raises InsufficientTrustError

        # Decorator
        @gate.require(min_score=0.7)
        def sensitive_operation(pubkey: str):
            ...

        # Select best candidate (ranks by reputation + diversity + domain proficiency)
        best = gate.select_best(["ed25519:a...", "ed25519:b..."], domain="security-operations")

        # Build-vs-buy: should I delegate or self-compute?
        delegate = gate.should_delegate(
            candidates=["ed25519:a...", "ed25519:b..."],
            domain="security-operations",
            skill="incident-triage",
            self_proficiency=2,  # I'm Competent level
        )
    """

    def __init__(
        self,
        client: KredoSigningClient,
        min_score: float = 0.0,
        block_warned: bool = False,
    ):
        self._client = client
        self._min_score = min_score
        self._block_warned = block_warned

    def check(
        self, pubkey: str, min_score: Optional[float] = None,
    ) -> TrustCheckResult:
        """Check an agent's trust score. Non-throwing.

        Args:
            pubkey: Agent's public key (ed25519:<hex>).
            min_score: Override the default minimum score for this check.

        Returns:
            TrustCheckResult with pass/fail, profile data, and attestor diversity.
        """
        threshold = min_score if min_score is not None else self._min_score

        try:
            profile = self._client.get_profile(pubkey)
        except Exception:
            return TrustCheckResult(
                pubkey=pubkey,
                score=0.0,
                passed=False,
                required=threshold,
                has_warnings=False,
                warning_count=0,
            )

        raw_score = profile.get("trust_analysis", {}).get("reputation_score", 0.0)
        try:
            score = float(raw_score) if raw_score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        warnings = profile.get("warnings", [])
        warning_count = len(warnings)
        has_warnings = warning_count > 0
        skills = profile.get("skills", [])
        trust_network = profile.get("trust_network", [])
        attestor_count = len({
            e["pubkey"] for e in trust_network if "pubkey" in e
        })

        passed = score >= threshold
        if self._block_warned and has_warnings:
            passed = False

        return TrustCheckResult(
            pubkey=pubkey,
            score=score,
            passed=passed,
            required=threshold,
            has_warnings=has_warnings,
            warning_count=warning_count,
            skills=skills,
            attestor_count=attestor_count,
        )

    def enforce(
        self, pubkey: str, min_score: Optional[float] = None,
    ) -> TrustCheckResult:
        """Check trust and raise if insufficient.

        Args:
            pubkey: Agent's public key (ed25519:<hex>).
            min_score: Override the default minimum score.

        Returns:
            TrustCheckResult if the agent passes.

        Raises:
            InsufficientTrustError: If the agent fails the trust check.
        """
        result = self.check(pubkey, min_score=min_score)
        if not result.passed:
            raise InsufficientTrustError(result)
        return result

    def require(
        self, min_score: Optional[float] = None,
    ) -> Callable:
        """Decorator that enforces trust before function execution.

        The decorated function must accept pubkey as its first argument.

        Usage:
            @gate.require(min_score=0.7)
            def sensitive_operation(pubkey: str, ...):
                ...
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(pubkey: str, *args, **kwargs):
                self.enforce(pubkey, min_score=min_score)
                return func(pubkey, *args, **kwargs)

            return wrapper

        return decorator

    @staticmethod
    def _ranking_score(
        result: TrustCheckResult,
        domain: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> float:
        """Composite ranking score: reputation + diversity + domain proficiency.

        When domain/skill specified:
            40% reputation + 35% domain proficiency + 25% attestor diversity
        When no domain filter:
            60% reputation + 40% attestor diversity
        """
        rep = result.score  # 0-1
        diversity = min(result.attestor_count / 10.0, 1.0)  # Cap at 10 attestors

        if domain:
            prof = _skill_proficiency(result.skills, domain, skill)
            domain_score = prof / 5.0  # Normalize 0-5 to 0-1
            return 0.4 * rep + 0.35 * domain_score + 0.25 * diversity
        else:
            return 0.6 * rep + 0.4 * diversity

    def select_best(
        self,
        candidates: list[str],
        domain: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> Optional[TrustCheckResult]:
        """Select the best candidate using composite ranking.

        Ranks by reputation score, attestation diversity (unique attestors),
        and domain-specific proficiency when a domain filter is provided.

        Args:
            candidates: List of agent public keys.
            domain: Optional domain filter for skill matching.
            skill: Optional skill filter for specific skill matching.

        Returns:
            TrustCheckResult for the best candidate, or None if none pass.
        """
        results = []
        for pubkey in candidates:
            result = self.check(pubkey)
            if not result.passed:
                continue

            if domain or skill:
                has_match = False
                for s in result.skills:
                    domain_ok = (not domain) or s.get("domain") == domain
                    skill_ok = (not skill) or s.get("specific") == skill
                    if domain_ok and skill_ok:
                        has_match = True
                        break
                if not has_match:
                    continue

            results.append(result)

        if not results:
            return None

        results.sort(
            key=lambda r: self._ranking_score(r, domain, skill),
            reverse=True,
        )
        return results[0]

    def should_delegate(
        self,
        candidates: list[str],
        domain: str,
        skill: Optional[str] = None,
        self_proficiency: int = 0,
    ) -> Optional[TrustCheckResult]:
        """Decide whether to delegate to an external agent or self-compute.

        Compares available candidates' attested proficiency in a domain
        against your own self-assessed proficiency level.

        Args:
            candidates: External agent pubkeys to evaluate.
            domain: Skill domain to compare on (required).
            skill: Optional specific skill to compare on.
            self_proficiency: Your own proficiency (0-5). 0 = no capability.

        Returns:
            TrustCheckResult for the best candidate if delegating is
            recommended. None if you should handle it yourself.
        """
        best = self.select_best(candidates, domain=domain, skill=skill)
        if best is None:
            return None

        candidate_prof = _skill_proficiency(best.skills, domain, skill)

        if candidate_prof > self_proficiency:
            return best
        return None
