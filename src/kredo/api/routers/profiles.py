"""Agent profile endpoint.

GET /agents/{pubkey}/profile — comprehensive agent profile
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kredo.accountability import resolve_accountability_context
from kredo.api.deps import get_known_key, get_store
from kredo.api.trust_cache import get_cached_agent_analysis
from kredo.evidence import score_evidence
from kredo.models import AttestationType, Evidence
from kredo.store import KredoStore

router = APIRouter(tags=["profiles"])


def _integrity_context(store: KredoStore, agent_pubkey: str) -> dict:
    baseline = store.get_active_integrity_baseline(agent_pubkey)
    latest_check = store.get_latest_integrity_check(agent_pubkey)

    if baseline is None:
        return {
            "traffic_light": "red",
            "status_label": "unknown_unsigned",
            "multiplier": 0.6,
            "recommended_action": "block_run",
            "active_baseline_id": None,
            "latest_check_id": latest_check["id"] if latest_check else None,
        }
    if latest_check is None:
        return {
            "traffic_light": "yellow",
            "status_label": "baseline_set_not_checked",
            "multiplier": 0.85,
            "recommended_action": "owner_review_required",
            "active_baseline_id": baseline["id"],
            "latest_check_id": None,
        }
    if latest_check.get("baseline_id") != baseline.get("id"):
        return {
            "traffic_light": "yellow",
            "status_label": "baseline_changed_recheck_required",
            "multiplier": 0.85,
            "recommended_action": "owner_review_required",
            "active_baseline_id": baseline["id"],
            "latest_check_id": latest_check["id"],
        }
    status = latest_check.get("status", "red")
    if status == "green":
        return {
            "traffic_light": "green",
            "status_label": "verified",
            "multiplier": 1.0,
            "recommended_action": "safe_to_run",
            "active_baseline_id": baseline["id"],
            "latest_check_id": latest_check["id"],
        }
    if status == "yellow":
        return {
            "traffic_light": "yellow",
            "status_label": "changed_since_baseline",
            "multiplier": 0.85,
            "recommended_action": "owner_review_required",
            "active_baseline_id": baseline["id"],
            "latest_check_id": latest_check["id"],
        }
    return {
        "traffic_light": "red",
        "status_label": "integrity_unknown",
        "multiplier": 0.6,
        "recommended_action": "block_run",
        "active_baseline_id": baseline["id"],
        "latest_check_id": latest_check["id"],
    }


@router.get("/agents/{pubkey}/profile")
async def agent_profile(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Comprehensive agent profile: identity, skills, attestation stats, trust depth."""
    agent = get_known_key(store, pubkey)
    if agent is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Agent not found: {pubkey}"},
        )

    # All attestations where this pubkey is the subject
    attestations = store.search_attestations(subject_pubkey=pubkey)

    # Skills aggregation
    skills = _aggregate_skills(attestations)

    # Attestor breakdown (agent vs human counts)
    agent_attestors = 0
    human_attestors = 0
    for att in attestations:
        att_type = att.get("attestor", {}).get("type", "agent")
        if att_type == "human":
            human_attestors += 1
        else:
            agent_attestors += 1

    # Behavioral warnings (including revoked ones for completeness)
    warnings_raw = store.search_attestations(
        subject_pubkey=pubkey,
        att_type="behavioral_warning",
        include_revoked=True,
    )
    warnings = []
    for w in warnings_raw:
        disputes = store.get_disputes_for(w["id"])
        row = store.get_attestation_row(w["id"])
        warnings.append({
            "id": w["id"],
            "category": w.get("warning_category"),
            "attestor": w.get("attestor", {}).get("pubkey"),
            "issued": w.get("issued"),
            "is_revoked": bool(row["is_revoked"]) if row else False,
            "dispute_count": len(disputes),
        })

    # Evidence quality average
    ev_scores = []
    for att in attestations:
        try:
            ev = Evidence(**att["evidence"])
            att_type_val = att.get("type", "skill_attestation")
            try:
                att_type_enum = AttestationType(att_type_val)
            except ValueError:
                att_type_enum = AttestationType.SKILL
            score = score_evidence(ev, att_type_enum)
            ev_scores.append(score.composite)
        except Exception:
            pass

    avg_evidence_quality = (
        round(sum(ev_scores) / len(ev_scores), 4) if ev_scores else None
    )

    # Trust depth: who attested for this agent
    attestors = store.get_attestors_for(pubkey)
    trust_network = []
    for a in attestors:
        # How many attestations does the attestor themselves have?
        attestor_attestations = store.search_attestations(
            subject_pubkey=a["attestor_pubkey"]
        )
        # Look up the attestor's actual type from known_keys
        attestor_info = get_known_key(store, a["attestor_pubkey"])
        attestor_type = attestor_info["type"] if attestor_info else "agent"
        trust_network.append({
            "pubkey": a["attestor_pubkey"],
            "type": attestor_type,
            "attestation_count_for_subject": a["attestation_count"],
            "attestor_own_attestation_count": len(attestor_attestations),
        })

    # Trust analysis: reputation score, ring flags, weighted skills
    analysis_payload = get_cached_agent_analysis(store, pubkey)
    accountability = resolve_accountability_context(store, pubkey)
    integrity = _integrity_context(store, pubkey)
    ring_flags = [
        {
            "ring_type": ring.get("ring_type"),
            "members": ring.get("members", []),
            "size": ring.get("size"),
        }
        for ring in analysis_payload.get("rings_involved", [])
    ]

    # Enrich skills with weighted_avg_proficiency from trust analysis
    weighted_skill_map = {
        f"{ws['domain']}:{ws['specific']}": ws.get("weighted_avg_proficiency", 0)
        for ws in analysis_payload.get("weighted_skills", [])
    }
    for skill in skills:
        key = f"{skill['domain']}:{skill['specific']}"
        skill["weighted_avg_proficiency"] = weighted_skill_map.get(key, skill["avg_proficiency"])

    owner_details = None
    if accountability.owner_pubkey:
        owner_info = get_known_key(store, accountability.owner_pubkey)
        owner_analysis = get_cached_agent_analysis(store, accountability.owner_pubkey)
        owner_details = {
            "pubkey": accountability.owner_pubkey,
            "name": owner_info["name"] if owner_info else "",
            "type": owner_info["type"] if owner_info else "human",
            "reputation_score": owner_analysis.get("reputation_score", 0.0),
        }

    combined_multiplier = accountability.multiplier * integrity["multiplier"]

    return {
        "pubkey": pubkey,
        "name": agent["name"],
        "type": agent["type"],
        "registered": agent["first_seen"],
        "last_seen": agent["last_seen"],
        "skills": skills,
        "attestation_count": {
            "total": len(attestations),
            "by_agents": agent_attestors,
            "by_humans": human_attestors,
        },
        "warnings": warnings,
        "evidence_quality_avg": avg_evidence_quality,
        "trust_network": trust_network,
        "trust_analysis": {
            "reputation_score": analysis_payload.get("reputation_score", 0.0),
            "deployability_score": round(
                analysis_payload.get("reputation_score", 0.0) * combined_multiplier,
                4,
            ),
            "deployability_multiplier": round(combined_multiplier, 4),
            "ring_flags": ring_flags,
        },
        "accountability": {
            "tier": accountability.tier,
            "multiplier": accountability.multiplier,
            "ownership_claim_id": accountability.ownership_claim_id,
            "owner": owner_details,
        },
        "integrity": integrity,
    }


def _aggregate_skills(attestations: list[dict]) -> list[dict]:
    """Aggregate skills across attestations into a summary."""
    skill_data: dict[str, dict] = {}

    for att in attestations:
        skill = att.get("skill")
        if not skill:
            continue
        domain = skill.get("domain", "")
        specific = skill.get("specific", "")
        key = f"{domain}:{specific}"

        if key not in skill_data:
            skill_data[key] = {
                "domain": domain,
                "specific": specific,
                "proficiency_values": [],
                "attestation_count": 0,
            }
        skill_data[key]["proficiency_values"].append(skill.get("proficiency", 0))
        skill_data[key]["attestation_count"] += 1

    results = []
    for key, data in skill_data.items():
        profs = data["proficiency_values"]
        results.append({
            "domain": data["domain"],
            "specific": data["specific"],
            "max_proficiency": max(profs),
            "avg_proficiency": round(sum(profs) / len(profs), 2),
            "attestation_count": data["attestation_count"],
        })

    # Sort by max proficiency desc, then attestation count desc
    results.sort(key=lambda x: (-x["max_proficiency"], -x["attestation_count"]))
    return results
