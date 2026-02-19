"""Accountability tiering and multipliers for agent deployment confidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kredo.store import KredoStore

UNLINKED_MULTIPLIER = 0.85
HUMAN_LINKED_MULTIPLIER = 1.0
ORG_BACKED_MULTIPLIER = 1.05


@dataclass
class AccountabilityContext:
    tier: str
    multiplier: float
    owner_pubkey: Optional[str]
    ownership_claim_id: Optional[str]


def resolve_accountability_context(store: KredoStore, agent_pubkey: str) -> AccountabilityContext:
    """Resolve accountability tier for an agent key.

    Current rules:
    - Active ownership link => human-linked
    - Otherwise => unlinked

    Future:
    - org-backed tier for enterprise assertions.
    """
    active_owner = store.get_active_owner(agent_pubkey)
    if active_owner is None:
        return AccountabilityContext(
            tier="unlinked",
            multiplier=UNLINKED_MULTIPLIER,
            owner_pubkey=None,
            ownership_claim_id=None,
        )

    return AccountabilityContext(
        tier="human-linked",
        multiplier=HUMAN_LINKED_MULTIPLIER,
        owner_pubkey=active_owner.get("human_pubkey"),
        ownership_claim_id=active_owner.get("id"),
    )

