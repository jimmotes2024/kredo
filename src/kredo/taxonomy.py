"""Skill taxonomy — load, validate, and query the bundled taxonomy."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Optional

from kredo.exceptions import TaxonomyError


@lru_cache(maxsize=1)
def _load_taxonomy() -> dict:
    """Load taxonomy from bundled package data."""
    try:
        ref = resources.files("kredo.data").joinpath("taxonomy_v1.json")
        return json.loads(ref.read_text(encoding="utf-8"))
    except Exception as e:
        raise TaxonomyError(f"Failed to load taxonomy: {e}") from e


def get_domains() -> list[str]:
    """Return list of all valid domain identifiers."""
    return list(_load_taxonomy()["domains"].keys())


def get_domain_label(domain: str) -> str:
    """Return human-readable label for a domain."""
    taxonomy = _load_taxonomy()
    if domain not in taxonomy["domains"]:
        raise TaxonomyError(f"Unknown domain: {domain!r}. Valid: {get_domains()}")
    return taxonomy["domains"][domain]["label"]


def get_skills(domain: str) -> list[str]:
    """Return list of specific skills for a given domain."""
    taxonomy = _load_taxonomy()
    if domain not in taxonomy["domains"]:
        raise TaxonomyError(f"Unknown domain: {domain!r}. Valid: {get_domains()}")
    return taxonomy["domains"][domain]["skills"]


def is_valid_skill(domain: str, specific: str) -> bool:
    """Check if a domain/skill combination is valid."""
    taxonomy = _load_taxonomy()
    if domain not in taxonomy["domains"]:
        return False
    return specific in taxonomy["domains"][domain]["skills"]


def validate_skill(domain: str, specific: str) -> None:
    """Validate domain/skill combination, raising TaxonomyError if invalid."""
    taxonomy = _load_taxonomy()
    if domain not in taxonomy["domains"]:
        raise TaxonomyError(f"Unknown domain: {domain!r}. Valid: {get_domains()}")
    skills = taxonomy["domains"][domain]["skills"]
    if specific not in skills:
        raise TaxonomyError(
            f"Unknown skill {specific!r} in domain {domain!r}. Valid: {skills}"
        )


def suggest_domain(query: str) -> Optional[str]:
    """Suggest a domain that partially matches the query, or None."""
    query_lower = query.lower()
    domains = get_domains()
    # Exact prefix match
    for d in domains:
        if d.startswith(query_lower):
            return d
    # Substring match
    for d in domains:
        if query_lower in d:
            return d
    return None


def taxonomy_version() -> str:
    """Return the taxonomy version string."""
    return _load_taxonomy()["version"]
