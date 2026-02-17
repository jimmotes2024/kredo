"""Trust graph analysis — ring detection, reputation weighting, decay.

All computation is derived from stored attestations. Nothing here
modifies signed documents. Results are ephemeral metadata served
alongside attestation data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from kredo.evidence import score_evidence
from kredo.models import AttestationType, Evidence
from kredo.store import KredoStore


# --- Constants ---

DECAY_HALF_LIFE_DAYS = 180
BASE_REPUTATION_WEIGHT = 0.1
MUTUAL_PAIR_DISCOUNT = 0.5
CLIQUE_DISCOUNT = 0.3
MAX_REPUTATION_DEPTH = 3
MAX_EDGES_FOR_CLIQUE_DETECTION = 10_000


# --- Dataclasses ---

@dataclass
class RingInfo:
    """A detected attestation ring."""
    members: list[str]
    size: int
    ring_type: str  # "mutual_pair" or "clique"
    attestation_ids: list[str]


@dataclass
class AttestationWeight:
    """Computed weight for a single attestation."""
    attestation_id: str
    raw_proficiency: int
    evidence_quality: float
    decay_factor: float
    attestor_reputation: float
    ring_discount: float
    effective_weight: float
    flags: list[str]


@dataclass
class AgentTrustAnalysis:
    """Full trust analysis for one agent."""
    pubkey: str
    reputation_score: float
    attestation_weights: list[AttestationWeight]
    rings_involved: list[RingInfo]
    weighted_skills: list[dict]
    analysis_timestamp: str


# --- Decay ---

def compute_decay(
    issued_date: datetime,
    reference_date: Optional[datetime] = None,
    half_life_days: float = DECAY_HALF_LIFE_DAYS,
) -> float:
    """Exponential decay: 2^(-days/half_life). Matches evidence.py pattern."""
    ref = reference_date or datetime.now(timezone.utc)
    if issued_date.tzinfo is None:
        issued_date = issued_date.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta_days = (ref - issued_date).total_seconds() / 86400
    if delta_days < 0:
        return 1.0
    return math.pow(2, -delta_days / half_life_days)


# --- Ring Detection ---

def detect_mutual_pairs(store: KredoStore) -> list[RingInfo]:
    """Find all A<->B mutual attestation pairs."""
    edges = store.get_all_attestation_edges()
    edge_set = set(edges)

    seen = set()
    pairs = []
    for a, b in edges:
        pair_key = tuple(sorted([a, b]))
        if pair_key in seen:
            continue
        if (b, a) in edge_set:
            seen.add(pair_key)
            # Find the attestation IDs forming this pair
            att_ids = _find_attestation_ids_between(store, a, b)
            att_ids += _find_attestation_ids_between(store, b, a)
            pairs.append(RingInfo(
                members=sorted([a, b]),
                size=2,
                ring_type="mutual_pair",
                attestation_ids=att_ids,
            ))
    return pairs


def detect_cliques(store: KredoStore, min_size: int = 3) -> list[RingInfo]:
    """Find cliques of size >= min_size where all members mutually attest.

    Uses Bron-Kerbosch algorithm on the mutual-attestation graph.
    Safety valve: skips if edge count exceeds MAX_EDGES_FOR_CLIQUE_DETECTION.
    """
    edges = store.get_all_attestation_edges()
    if len(edges) > MAX_EDGES_FOR_CLIQUE_DETECTION:
        return []

    # Build undirected graph of mutual attestations only
    edge_set = set(edges)
    mutual_graph: dict[str, set[str]] = {}
    for a, b in edges:
        if (b, a) in edge_set:
            mutual_graph.setdefault(a, set()).add(b)
            mutual_graph.setdefault(b, set()).add(a)

    if not mutual_graph:
        return []

    # Bron-Kerbosch without pivoting (graph is small)
    cliques: list[set[str]] = []
    _bron_kerbosch(set(), set(mutual_graph.keys()), set(), mutual_graph, cliques)

    results = []
    for clique in cliques:
        if len(clique) >= min_size:
            members = sorted(clique)
            att_ids = []
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    att_ids += _find_attestation_ids_between(store, a, b)
                    att_ids += _find_attestation_ids_between(store, b, a)
            results.append(RingInfo(
                members=members,
                size=len(members),
                ring_type="clique",
                attestation_ids=att_ids,
            ))
    return results


def _bron_kerbosch(
    r: set[str],
    p: set[str],
    x: set[str],
    graph: dict[str, set[str]],
    cliques: list[set[str]],
) -> None:
    """Bron-Kerbosch algorithm for maximal clique enumeration."""
    if not p and not x:
        if len(r) >= 2:  # Only record cliques of size 2+
            cliques.append(set(r))
        return
    for v in list(p):
        neighbors = graph.get(v, set())
        _bron_kerbosch(
            r | {v},
            p & neighbors,
            x & neighbors,
            graph,
            cliques,
        )
        p.remove(v)
        x.add(v)


def detect_all_rings(store: KredoStore) -> list[RingInfo]:
    """Combined ring detection: mutual pairs + cliques."""
    pairs = detect_mutual_pairs(store)
    cliques = detect_cliques(store, min_size=3)
    return pairs + cliques


def get_ring_discount(
    subject_pubkey: str,
    attestor_pubkey: str,
    rings: list[RingInfo],
) -> float:
    """Return discount factor for an attestation given ring membership.

    Returns CLIQUE_DISCOUNT if both are in a clique of 3+,
    MUTUAL_PAIR_DISCOUNT if they form a mutual pair,
    1.0 if no ring involvement.
    """
    both = {subject_pubkey, attestor_pubkey}
    # Check cliques first (stricter discount)
    for ring in rings:
        if ring.ring_type == "clique" and both.issubset(set(ring.members)):
            return CLIQUE_DISCOUNT
    # Check mutual pairs
    for ring in rings:
        if ring.ring_type == "mutual_pair" and both == set(ring.members):
            return MUTUAL_PAIR_DISCOUNT
    return 1.0


def _find_attestation_ids_between(
    store: KredoStore, attestor: str, subject: str,
) -> list[str]:
    """Find attestation IDs where attestor attested subject."""
    atts = store.search_attestations(
        attestor_pubkey=attestor, subject_pubkey=subject,
    )
    return [a["id"] for a in atts]


# --- Reputation ---

def compute_attestor_reputation(
    store: KredoStore,
    pubkey: str,
    depth: int = 0,
    visited: Optional[set[str]] = None,
    rings: Optional[list[RingInfo]] = None,
    reference_date: Optional[datetime] = None,
) -> float:
    """Recursive reputation: weighted sum of incoming attestations.

    Stops at MAX_REPUTATION_DEPTH. Uses visited set for cycle detection.
    Returns 0.0-1.0 via 1 - exp(-total) normalization.
    """
    if visited is None:
        visited = set()
    if rings is None:
        rings = detect_all_rings(store)

    if depth >= MAX_REPUTATION_DEPTH or pubkey in visited:
        return 0.0

    visited.add(pubkey)

    attestations = store.search_attestations(subject_pubkey=pubkey)
    if not attestations:
        return 0.0

    total = 0.0
    for att in attestations:
        attestor_pk = att.get("attestor", {}).get("pubkey", "")
        # Recursive reputation of the attestor
        attestor_rep = compute_attestor_reputation(
            store, attestor_pk, depth + 1, set(visited), rings, reference_date,
        )
        attestor_weight = BASE_REPUTATION_WEIGHT + (1 - BASE_REPUTATION_WEIGHT) * attestor_rep

        # Decay from issued date
        issued_str = att.get("issued", "")
        try:
            issued_dt = datetime.fromisoformat(issued_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            issued_dt = datetime.now(timezone.utc)
        decay = compute_decay(issued_dt, reference_date)

        # Ring discount
        subject_pk = att.get("subject", {}).get("pubkey", "")
        ring_disc = get_ring_discount(subject_pk, attestor_pk, rings)

        # Evidence quality
        try:
            ev = Evidence(**att["evidence"])
            att_type_val = att.get("type", "skill_attestation")
            try:
                att_type_enum = AttestationType(att_type_val)
            except ValueError:
                att_type_enum = AttestationType.SKILL
            ev_score = score_evidence(ev, att_type_enum, reference_date).composite
        except Exception:
            ev_score = 0.5

        total += attestor_weight * decay * ring_disc * ev_score

    return 1.0 - math.exp(-total)


# --- Attestation Weighting ---

def compute_attestation_weight(
    store: KredoStore,
    attestation: dict,
    rings: list[RingInfo],
    reference_date: Optional[datetime] = None,
) -> AttestationWeight:
    """Compute the effective weight of a single attestation."""
    att_id = attestation.get("id", "")
    attestor_pk = attestation.get("attestor", {}).get("pubkey", "")
    subject_pk = attestation.get("subject", {}).get("pubkey", "")

    # Raw proficiency
    raw_prof = attestation.get("skill", {}).get("proficiency", 1) if attestation.get("skill") else 1

    # Evidence quality
    try:
        ev = Evidence(**attestation["evidence"])
        att_type_val = attestation.get("type", "skill_attestation")
        try:
            att_type_enum = AttestationType(att_type_val)
        except ValueError:
            att_type_enum = AttestationType.SKILL
        ev_quality = score_evidence(ev, att_type_enum, reference_date).composite
    except Exception:
        ev_quality = 0.5

    # Decay
    issued_str = attestation.get("issued", "")
    try:
        issued_dt = datetime.fromisoformat(issued_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        issued_dt = datetime.now(timezone.utc)
    decay = compute_decay(issued_dt, reference_date)

    # Attestor reputation
    attestor_rep = compute_attestor_reputation(
        store, attestor_pk, rings=rings, reference_date=reference_date,
    )
    attestor_weight = BASE_REPUTATION_WEIGHT + (1 - BASE_REPUTATION_WEIGHT) * attestor_rep

    # Ring discount
    ring_disc = get_ring_discount(subject_pk, attestor_pk, rings)

    # Effective weight
    effective = raw_prof * ev_quality * decay * attestor_weight * ring_disc

    # Flags
    flags = []
    if ring_disc < 1.0:
        flags.append("ring_member")
    if decay < 0.25:
        flags.append("decayed")
    if attestor_rep < 0.01:
        flags.append("unattested_attestor")

    return AttestationWeight(
        attestation_id=att_id,
        raw_proficiency=raw_prof,
        evidence_quality=round(ev_quality, 4),
        decay_factor=round(decay, 4),
        attestor_reputation=round(attestor_rep, 4),
        ring_discount=round(ring_disc, 2),
        effective_weight=round(effective, 4),
        flags=flags,
    )


# --- Profile Analysis ---

def analyze_agent(
    store: KredoStore,
    pubkey: str,
    reference_date: Optional[datetime] = None,
) -> AgentTrustAnalysis:
    """Full trust analysis for an agent."""
    rings = detect_all_rings(store)

    # Reputation score
    rep_score = compute_attestor_reputation(
        store, pubkey, rings=rings, reference_date=reference_date,
    )

    # All attestations where this agent is the subject
    attestations = store.search_attestations(subject_pubkey=pubkey)

    # Compute weights for each attestation
    weights = [
        compute_attestation_weight(store, att, rings, reference_date)
        for att in attestations
    ]

    # Rings involving this agent
    agent_rings = [
        r for r in rings if pubkey in r.members
    ]

    # Weighted skill aggregation
    weighted_skills = _aggregate_weighted_skills(attestations, weights)

    return AgentTrustAnalysis(
        pubkey=pubkey,
        reputation_score=round(rep_score, 4),
        attestation_weights=weights,
        rings_involved=agent_rings,
        weighted_skills=weighted_skills,
        analysis_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _aggregate_weighted_skills(
    attestations: list[dict],
    weights: list[AttestationWeight],
) -> list[dict]:
    """Aggregate skills using effective weights."""
    weight_map = {w.attestation_id: w for w in weights}

    skill_data: dict[str, dict] = {}
    for att in attestations:
        skill = att.get("skill")
        if not skill:
            continue
        key = f"{skill['domain']}:{skill['specific']}"
        w = weight_map.get(att["id"])
        eff_weight = w.effective_weight if w else 0.0
        prof = skill.get("proficiency", 1)

        if key not in skill_data:
            skill_data[key] = {
                "domain": skill["domain"],
                "specific": skill["specific"],
                "proficiency_values": [],
                "weights": [],
                "attestation_count": 0,
            }
        skill_data[key]["proficiency_values"].append(prof)
        skill_data[key]["weights"].append(eff_weight)
        skill_data[key]["attestation_count"] += 1

    results = []
    for data in skill_data.values():
        profs = data["proficiency_values"]
        wts = data["weights"]
        total_wt = sum(wts)
        if total_wt > 0:
            weighted_avg = sum(p * w for p, w in zip(profs, wts)) / total_wt
        else:
            weighted_avg = sum(profs) / len(profs) if profs else 0

        results.append({
            "domain": data["domain"],
            "specific": data["specific"],
            "max_proficiency": max(profs),
            "avg_proficiency": round(sum(profs) / len(profs), 2),
            "weighted_avg_proficiency": round(weighted_avg, 2),
            "attestation_count": data["attestation_count"],
        })

    results.sort(key=lambda x: (-x["max_proficiency"], -x["attestation_count"]))
    return results


def compute_network_health(store: KredoStore) -> dict:
    """Network-wide statistics."""
    rings = detect_all_rings(store)
    edges = store.get_all_attestation_edges()
    unique_agents = set()
    for a, b in edges:
        unique_agents.add(a)
        unique_agents.add(b)

    mutual_pairs = [r for r in rings if r.ring_type == "mutual_pair"]
    cliques = [r for r in rings if r.ring_type == "clique"]

    # Agents involved in any ring
    ring_agents = set()
    for r in rings:
        ring_agents.update(r.members)

    return {
        "total_agents_in_graph": len(unique_agents),
        "total_directed_edges": len(edges),
        "mutual_pair_count": len(mutual_pairs),
        "clique_count": len(cliques),
        "agents_in_rings": len(ring_agents),
        "ring_participation_rate": round(
            len(ring_agents) / len(unique_agents), 4
        ) if unique_agents else 0.0,
    }
