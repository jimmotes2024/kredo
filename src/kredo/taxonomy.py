"""Skill taxonomy — load, validate, and query the bundled taxonomy."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from kredo.exceptions import TaxonomyError

_TAXONOMY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "taxonomy_v1.json"


@lru_cache(maxsize=1)
def _load_taxonomy() -> dict:
    """Load taxonomy from bundled JSON file."""
    if not _TAXONOMY_FILE.exists():
        raise TaxonomyError(f"Taxonomy file not found: {_TAXONOMY_FILE}")
    with open(_TAXONOMY_FILE) as f:
        return json.load(f)


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


def taxonomy_version() -> str:
    """Return the taxonomy version string."""
    return _load_taxonomy()["version"]
