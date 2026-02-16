"""Taxonomy browsing endpoints.

GET /taxonomy — full taxonomy with all domains and skills
GET /taxonomy/{domain} — skills for one domain
"""

from __future__ import annotations

from fastapi import APIRouter

from kredo.taxonomy import get_domain_label, get_domains, get_skills, taxonomy_version

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("")
async def full_taxonomy():
    """Return the complete skill taxonomy."""
    domains = get_domains()
    result = {}
    for domain in domains:
        result[domain] = {
            "label": get_domain_label(domain),
            "skills": get_skills(domain),
        }
    return {
        "version": taxonomy_version(),
        "domains": result,
    }


@router.get("/{domain}")
async def domain_skills(domain: str):
    """Return skills for a specific domain."""
    domains = get_domains()
    if domain not in domains:
        return {"error": f"Unknown domain: {domain!r}", "valid_domains": domains}
    return {
        "domain": domain,
        "label": get_domain_label(domain),
        "skills": get_skills(domain),
    }
