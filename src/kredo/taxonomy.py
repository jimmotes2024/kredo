"""Skill taxonomy — load, validate, and query bundled + custom taxonomy."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Optional

from kredo.exceptions import TaxonomyError

if TYPE_CHECKING:
    from kredo.store import KredoStore

# Module-level store reference, set by set_store() during app/CLI startup
_store: Optional[KredoStore] = None


def set_store(store: KredoStore) -> None:
    """Wire a KredoStore so taxonomy queries include custom entries."""
    global _store
    _store = store
    invalidate_cache()


def invalidate_cache() -> None:
    """Clear the merged taxonomy cache. Call after custom entries change."""
    _load_merged_taxonomy.cache_clear()


@lru_cache(maxsize=1)
def _load_bundled_taxonomy() -> dict:
    """Load taxonomy from bundled package data (never changes at runtime)."""
    try:
        ref = resources.files("kredo.data").joinpath("taxonomy_v1.json")
        return json.loads(ref.read_text(encoding="utf-8"))
    except Exception as e:
        raise TaxonomyError(f"Failed to load taxonomy: {e}") from e


@lru_cache(maxsize=1)
def _load_merged_taxonomy() -> dict:
    """Load bundled taxonomy and merge in custom domains/skills from the store."""
    bundled = _load_bundled_taxonomy()
    # Deep copy the domains dict so we don't mutate the bundled cache
    merged = {}
    for domain_id, domain_data in bundled["domains"].items():
        merged[domain_id] = {
            "label": domain_data["label"],
            "skills": list(domain_data["skills"]),
            "custom": False,
        }

    if _store is not None:
        try:
            # Add custom domains
            for cd in _store.list_custom_domains():
                if cd["id"] not in merged:
                    merged[cd["id"]] = {
                        "label": cd["label"],
                        "skills": [],
                        "custom": True,
                    }
            # Add custom skills to their domains
            for domain_id in list(merged.keys()):
                custom_skills = _store.list_custom_skills(domain_id)
                for cs in custom_skills:
                    if cs["id"] not in merged[domain_id]["skills"]:
                        merged[domain_id]["skills"].append(cs["id"])
        except Exception:
            # Store closed or unavailable — return bundled only
            pass

    return {
        "version": bundled["version"],
        "domains": merged,
    }


def get_domains(bundled_only: bool = False) -> list[str]:
    """Return list of all valid domain identifiers."""
    if bundled_only:
        return list(_load_bundled_taxonomy()["domains"].keys())
    return list(_load_merged_taxonomy()["domains"].keys())


def get_domain_label(domain: str) -> str:
    """Return human-readable label for a domain."""
    taxonomy = _load_merged_taxonomy()
    if domain not in taxonomy["domains"]:
        raise TaxonomyError(f"Unknown domain: {domain!r}. Valid: {get_domains()}")
    return taxonomy["domains"][domain]["label"]


def get_skills(domain: str) -> list[str]:
    """Return list of specific skills for a given domain."""
    taxonomy = _load_merged_taxonomy()
    if domain not in taxonomy["domains"]:
        raise TaxonomyError(f"Unknown domain: {domain!r}. Valid: {get_domains()}")
    return taxonomy["domains"][domain]["skills"]


def is_valid_skill(domain: str, specific: str) -> bool:
    """Check if a domain/skill combination is valid."""
    taxonomy = _load_merged_taxonomy()
    if domain not in taxonomy["domains"]:
        return False
    return specific in taxonomy["domains"][domain]["skills"]


def validate_skill(domain: str, specific: str) -> None:
    """Validate domain/skill combination, raising TaxonomyError if invalid."""
    taxonomy = _load_merged_taxonomy()
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


def is_custom_domain(domain: str) -> bool:
    """Check if a domain is a custom (non-bundled) domain."""
    taxonomy = _load_merged_taxonomy()
    if domain not in taxonomy["domains"]:
        return False
    return taxonomy["domains"][domain].get("custom", False)


def taxonomy_version() -> str:
    """Return the taxonomy version string."""
    return _load_merged_taxonomy()["version"]
