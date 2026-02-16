"""Agent profile endpoint.

GET /agents/{pubkey}/profile — comprehensive agent profile
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kredo.api.deps import get_known_key, get_store
from kredo.evidence import score_evidence
from kredo.models import AttestationType, Evidence
from kredo.store import KredoStore

router = APIRouter(tags=["profiles"])


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
