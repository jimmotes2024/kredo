"""Tests for trust_analysis — ring detection, reputation weighting, decay."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from kredo.models import (
    Attestation,
    AttestationType,
    Attestor,
    AttestorType,
    Evidence,
    Proficiency,
    Skill,
    Subject,
)
from kredo.signing import sign_attestation
from kredo.store import KredoStore
from kredo.trust_analysis import (
    CLIQUE_DISCOUNT,
    DECAY_HALF_LIFE_DAYS,
    MUTUAL_PAIR_DISCOUNT,
    AgentTrustAnalysis,
    AttestationWeight,
    RingInfo,
    analyze_agent,
    compute_attestation_weight,
    compute_attestor_reputation,
    compute_decay,
    compute_network_health,
    detect_all_rings,
    detect_cliques,
    detect_mutual_pairs,
    get_ring_discount,
)


# --- Helpers ---

def _make_key() -> tuple[SigningKey, str]:
    sk = SigningKey.generate()
    pk = "ed25519:" + sk.verify_key.encode(encoder=HexEncoder).decode("ascii")
    return sk, pk


def _make_signed_attestation(
    sk_attestor: SigningKey,
    pk_attestor: str,
    pk_subject: str,
    domain: str = "security-operations",
    specific: str = "incident-triage",
    proficiency: int = 4,
    days_ago: int = 1,
    context: str = "Collaborated on security incident, demonstrated competence",
) -> dict:
    """Create a signed attestation dict ready for store.save_attestation()."""
    now = datetime.now(timezone.utc)
    att = Attestation(
        type=AttestationType.SKILL,
        subject=Subject(pubkey=pk_subject, name="Subject"),
        attestor=Attestor(pubkey=pk_attestor, name="Attestor", type=AttestorType.AGENT),
        skill=Skill(domain=domain, specific=specific, proficiency=Proficiency(proficiency)),
        evidence=Evidence(
            context=context,
            artifacts=["chain:test123", "output:report-456"],
            outcome="successful_resolution",
            interaction_date=now - timedelta(days=days_ago),
        ),
        issued=now - timedelta(days=days_ago),
        expires=now + timedelta(days=365),
    )
    signed = sign_attestation(att, sk_attestor)
    return json.loads(signed.model_dump_json())


def _store_attestation(store: KredoStore, att_dict: dict) -> str:
    """Save an attestation to the store and return its ID."""
    raw = json.dumps(att_dict)
    return store.save_attestation(raw)


# --- TestDecay ---

class TestDecay:
    def test_zero_days_full_weight(self):
        now = datetime.now(timezone.utc)
        assert compute_decay(now, now) == pytest.approx(1.0)

    def test_half_life_half_weight(self):
        now = datetime.now(timezone.utc)
        issued = now - timedelta(days=DECAY_HALF_LIFE_DAYS)
        assert compute_decay(issued, now) == pytest.approx(0.5, abs=0.01)

    def test_one_year_low_weight(self):
        now = datetime.now(timezone.utc)
        issued = now - timedelta(days=365)
        result = compute_decay(issued, now)
        assert result < 0.3
        assert result > 0.0

    def test_future_date_full_weight(self):
        now = datetime.now(timezone.utc)
        issued = now + timedelta(days=10)
        assert compute_decay(issued, now) == 1.0

    def test_custom_half_life(self):
        now = datetime.now(timezone.utc)
        issued = now - timedelta(days=30)
        result = compute_decay(issued, now, half_life_days=30)
        assert result == pytest.approx(0.5, abs=0.01)


# --- TestRingDetection ---

class TestRingDetection:
    def test_no_rings_empty_db(self, store):
        assert detect_mutual_pairs(store) == []
        assert detect_cliques(store) == []

    def test_mutual_pair_detected(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()

        # A attests B
        att1 = _make_signed_attestation(sk_a, pk_a, pk_b)
        _store_attestation(store, att1)

        # B attests A
        att2 = _make_signed_attestation(sk_b, pk_b, pk_a)
        _store_attestation(store, att2)

        pairs = detect_mutual_pairs(store)
        assert len(pairs) == 1
        assert pairs[0].ring_type == "mutual_pair"
        assert pairs[0].size == 2
        assert set(pairs[0].members) == {pk_a, pk_b}

    def test_one_way_not_a_ring(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        # A attests B only (no reverse)
        att = _make_signed_attestation(sk_a, pk_a, pk_b)
        _store_attestation(store, att)

        assert detect_mutual_pairs(store) == []

    def test_three_agent_clique(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()
        sk_c, pk_c = _make_key()

        # All pairs mutual: A↔B, B↔C, A↔C
        for sk_from, pk_from, pk_to in [
            (sk_a, pk_a, pk_b), (sk_b, pk_b, pk_a),
            (sk_b, pk_b, pk_c), (sk_c, pk_c, pk_b),
            (sk_a, pk_a, pk_c), (sk_c, pk_c, pk_a),
        ]:
            att = _make_signed_attestation(sk_from, pk_from, pk_to)
            _store_attestation(store, att)

        cliques = detect_cliques(store, min_size=3)
        assert len(cliques) == 1
        assert cliques[0].size == 3
        assert set(cliques[0].members) == {pk_a, pk_b, pk_c}

    def test_partial_clique_not_detected(self, store):
        """A↔B and B↔C but NOT A↔C — not a 3-clique."""
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()
        sk_c, pk_c = _make_key()

        for sk_from, pk_from, pk_to in [
            (sk_a, pk_a, pk_b), (sk_b, pk_b, pk_a),
            (sk_b, pk_b, pk_c), (sk_c, pk_c, pk_b),
        ]:
            att = _make_signed_attestation(sk_from, pk_from, pk_to)
            _store_attestation(store, att)

        cliques = detect_cliques(store, min_size=3)
        assert len(cliques) == 0

        # But mutual pairs should still be found
        pairs = detect_mutual_pairs(store)
        assert len(pairs) == 2

    def test_multiple_independent_rings(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()
        sk_c, pk_c = _make_key()
        sk_d, pk_d = _make_key()

        # Ring 1: A↔B
        for sk_from, pk_from, pk_to in [
            (sk_a, pk_a, pk_b), (sk_b, pk_b, pk_a),
        ]:
            _store_attestation(store, _make_signed_attestation(sk_from, pk_from, pk_to))

        # Ring 2: C↔D
        for sk_from, pk_from, pk_to in [
            (sk_c, pk_c, pk_d), (sk_d, pk_d, pk_c),
        ]:
            _store_attestation(store, _make_signed_attestation(sk_from, pk_from, pk_to))

        pairs = detect_mutual_pairs(store)
        assert len(pairs) == 2

    def test_revoked_attestation_excluded(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()

        # A attests B
        att1 = _make_signed_attestation(sk_a, pk_a, pk_b)
        att1_id = _store_attestation(store, att1)

        # B attests A
        att2 = _make_signed_attestation(sk_b, pk_b, pk_a)
        _store_attestation(store, att2)

        # Revoke att1
        from kredo.models import Revocation
        from kredo.signing import sign_revocation
        rev = Revocation(
            id="rev-001",
            attestation_id=att1_id,
            revoker=Subject(pubkey=pk_a, name="Attestor"),
            reason="Test revocation",
            issued=datetime.now(timezone.utc),
        )
        signed_rev = sign_revocation(rev, sk_a)
        store.save_revocation(signed_rev.model_dump_json())

        # Revoked edge should break the mutual pair
        pairs = detect_mutual_pairs(store)
        assert len(pairs) == 0


# --- TestReputation ---

class TestReputation:
    def test_unattested_agent_zero_reputation(self, store):
        _, pk = _make_key()
        assert compute_attestor_reputation(store, pk) == 0.0

    def test_single_attestation_nonzero(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        att = _make_signed_attestation(sk_a, pk_a, pk_b)
        _store_attestation(store, att)

        rep = compute_attestor_reputation(store, pk_b)
        assert rep > 0.0
        assert rep < 1.0

    def test_attestor_with_reputation_carries_more_weight(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()
        sk_c, pk_c = _make_key()
        _, pk_target1 = _make_key()
        _, pk_target2 = _make_key()

        # Give B reputation by having A attest B
        _store_attestation(store, _make_signed_attestation(sk_a, pk_a, pk_b))

        # B (with reputation) attests target1
        _store_attestation(store, _make_signed_attestation(sk_b, pk_b, pk_target1))

        # C (no reputation) attests target2
        _store_attestation(store, _make_signed_attestation(sk_c, pk_c, pk_target2))

        rep_target1 = compute_attestor_reputation(store, pk_target1)
        rep_target2 = compute_attestor_reputation(store, pk_target2)

        # Target1 should have higher reputation (attested by reputable B)
        assert rep_target1 > rep_target2

    def test_depth_limit_prevents_infinite_recursion(self, store):
        """Deep chain: A→B→C→D→E. Should terminate due to depth limit."""
        keys = [_make_key() for _ in range(5)]

        for i in range(4):
            sk_from, pk_from = keys[i]
            _, pk_to = keys[i + 1]
            _store_attestation(store, _make_signed_attestation(sk_from, pk_from, pk_to))

        # Should not hang or error
        _, pk_last = keys[4]
        rep = compute_attestor_reputation(store, pk_last)
        assert rep > 0.0

    def test_cycle_handling(self, store):
        """A attests B, B attests A. Should not loop infinitely."""
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()

        _store_attestation(store, _make_signed_attestation(sk_a, pk_a, pk_b))
        _store_attestation(store, _make_signed_attestation(sk_b, pk_b, pk_a))

        # Should terminate (visited set prevents loop)
        rep_a = compute_attestor_reputation(store, pk_a)
        rep_b = compute_attestor_reputation(store, pk_b)
        assert rep_a > 0.0
        assert rep_b > 0.0


# --- TestAttestationWeight ---

class TestAttestationWeight:
    def test_fresh_strong_attestation_high_weight(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        att = _make_signed_attestation(sk_a, pk_a, pk_b, proficiency=5, days_ago=1)
        _store_attestation(store, att)

        w = compute_attestation_weight(store, att, rings=[])
        assert w.effective_weight > 0
        assert w.raw_proficiency == 5
        assert w.decay_factor > 0.99
        assert w.ring_discount == 1.0
        assert "ring_member" not in w.flags

    def test_old_attestation_decayed(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        att = _make_signed_attestation(sk_a, pk_a, pk_b, days_ago=365)
        _store_attestation(store, att)

        w = compute_attestation_weight(store, att, rings=[])
        assert w.decay_factor < 0.3
        assert "decayed" in w.flags

    def test_ring_member_discounted(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()

        att_ab = _make_signed_attestation(sk_a, pk_a, pk_b)
        att_ba = _make_signed_attestation(sk_b, pk_b, pk_a)
        _store_attestation(store, att_ab)
        _store_attestation(store, att_ba)

        rings = detect_all_rings(store)
        w = compute_attestation_weight(store, att_ab, rings)
        assert w.ring_discount == MUTUAL_PAIR_DISCOUNT
        assert "ring_member" in w.flags

    def test_unattested_attestor_low_reputation_weight(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        att = _make_signed_attestation(sk_a, pk_a, pk_b)
        _store_attestation(store, att)

        w = compute_attestation_weight(store, att, rings=[])
        assert w.attestor_reputation == 0.0
        assert "unattested_attestor" in w.flags

    def test_poor_evidence_low_weight(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        att = _make_signed_attestation(
            sk_a, pk_a, pk_b,
            context="ok",  # Very short context → low specificity
        )
        _store_attestation(store, att)

        w = compute_attestation_weight(store, att, rings=[])
        assert w.evidence_quality < 0.85


# --- TestAgentAnalysis ---

class TestAgentAnalysis:
    def test_analyze_unknown_agent(self, store):
        _, pk = _make_key()
        analysis = analyze_agent(store, pk)
        assert analysis.pubkey == pk
        assert analysis.reputation_score == 0.0
        assert analysis.attestation_weights == []
        assert analysis.rings_involved == []
        assert analysis.weighted_skills == []

    def test_analyze_agent_with_attestations(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()

        att = _make_signed_attestation(sk_a, pk_a, pk_b)
        _store_attestation(store, att)

        analysis = analyze_agent(store, pk_b)
        assert analysis.reputation_score > 0.0
        assert len(analysis.attestation_weights) == 1
        assert len(analysis.weighted_skills) == 1

    def test_weighted_skills_differ_from_raw(self, store):
        """With different attestor reputations, weighted avg should differ from simple avg."""
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()
        sk_c, pk_c = _make_key()
        _, pk_target = _make_key()

        # Give A reputation
        _store_attestation(store, _make_signed_attestation(sk_c, pk_c, pk_a))

        # A (reputable) attests target with proficiency 5
        _store_attestation(store, _make_signed_attestation(
            sk_a, pk_a, pk_target, proficiency=5,
        ))

        # B (no reputation) attests target with proficiency 2
        _store_attestation(store, _make_signed_attestation(
            sk_b, pk_b, pk_target, proficiency=2,
        ))

        analysis = analyze_agent(store, pk_target)
        skill = analysis.weighted_skills[0]

        # Simple average: (5+2)/2 = 3.5
        assert skill["avg_proficiency"] == 3.5

        # Weighted average should be higher (A's attestation carries more weight)
        assert skill["weighted_avg_proficiency"] > skill["avg_proficiency"]

    def test_ring_flags_present(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()

        _store_attestation(store, _make_signed_attestation(sk_a, pk_a, pk_b))
        _store_attestation(store, _make_signed_attestation(sk_b, pk_b, pk_a))

        analysis = analyze_agent(store, pk_a)
        assert len(analysis.rings_involved) > 0

    def test_analysis_timestamp_set(self, store):
        _, pk = _make_key()
        analysis = analyze_agent(store, pk)
        assert analysis.analysis_timestamp is not None
        # Should parse as ISO datetime
        datetime.fromisoformat(analysis.analysis_timestamp)


# --- TestNetworkHealth ---

class TestNetworkHealth:
    def test_empty_network(self, store):
        health = compute_network_health(store)
        assert health["total_agents_in_graph"] == 0
        assert health["mutual_pair_count"] == 0
        assert health["clique_count"] == 0

    def test_healthy_network_stats(self, store):
        sk_a, pk_a = _make_key()
        _, pk_b = _make_key()
        _, pk_c = _make_key()

        # One-way attestations (no rings)
        _store_attestation(store, _make_signed_attestation(sk_a, pk_a, pk_b))
        _store_attestation(store, _make_signed_attestation(sk_a, pk_a, pk_c))

        health = compute_network_health(store)
        assert health["total_agents_in_graph"] == 3
        assert health["total_directed_edges"] == 2
        assert health["mutual_pair_count"] == 0
        assert health["agents_in_rings"] == 0
        assert health["ring_participation_rate"] == 0.0

    def test_network_with_rings(self, store):
        sk_a, pk_a = _make_key()
        sk_b, pk_b = _make_key()

        _store_attestation(store, _make_signed_attestation(sk_a, pk_a, pk_b))
        _store_attestation(store, _make_signed_attestation(sk_b, pk_b, pk_a))

        health = compute_network_health(store)
        assert health["mutual_pair_count"] == 1
        assert health["agents_in_rings"] == 2
        assert health["ring_participation_rate"] == 1.0
