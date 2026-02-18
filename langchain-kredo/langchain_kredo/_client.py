"""Signing-aware Kredo client for LangChain integration.

Wraps kredo.client.KredoClient with Ed25519 signing for write operations.
Read operations delegate directly. Write operations build Pydantic models,
sign with Ed25519, and submit to the Discovery API.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from kredo.client import KredoClient
from kredo.models import (
    Attestation,
    AttestationType,
    Attestor,
    AttestorType,
    Evidence,
    Proficiency,
    Skill,
    Subject,
    WarningCategory,
)
from kredo.signing import sign_attestation


def _pubkey_from_signing_key(signing_key: SigningKey) -> str:
    """Convert a PyNaCl SigningKey to ed25519:<hex> pubkey string."""
    return "ed25519:" + signing_key.verify_key.encode(
        encoder=HexEncoder
    ).decode("ascii")


class KredoSigningClient:
    """Kredo client with Ed25519 signing for write operations.

    Signing key resolution order:
    1. signing_key parameter (SigningKey, bytes, or hex string)
    2. KREDO_PRIVATE_KEY environment variable (hex seed)
    3. None (read-only mode — write operations will raise)
    """

    def __init__(
        self,
        signing_key: Optional[Union[SigningKey, bytes, str]] = None,
        name: str = "",
        agent_type: str = "agent",
        api_url: Optional[str] = None,
    ):
        self._client = KredoClient(base_url=api_url)
        self._name = name
        self._agent_type = agent_type
        self._signing_key = self._resolve_key(signing_key)
        self._pubkey = (
            _pubkey_from_signing_key(self._signing_key)
            if self._signing_key
            else None
        )

    @staticmethod
    def _resolve_key(
        key: Optional[Union[SigningKey, bytes, str]],
    ) -> Optional[SigningKey]:
        """Resolve signing key from multiple input formats."""
        if key is None:
            env_hex = os.environ.get("KREDO_PRIVATE_KEY")
            if env_hex:
                return SigningKey(bytes.fromhex(env_hex))
            return None
        if isinstance(key, SigningKey):
            return key
        if isinstance(key, bytes):
            return SigningKey(key)
        if isinstance(key, str):
            return SigningKey(bytes.fromhex(key))
        raise TypeError(f"Unsupported signing key type: {type(key)}")

    @property
    def pubkey(self) -> Optional[str]:
        """Public key in ed25519:<hex> format, or None if no signing key."""
        return self._pubkey

    def _require_key(self) -> SigningKey:
        """Return signing key or raise if not configured."""
        if self._signing_key is None:
            raise ValueError(
                "Signing key required for write operations. "
                "Pass signing_key= or set KREDO_PRIVATE_KEY env var."
            )
        return self._signing_key

    # --- Read operations (delegate to KredoClient) ---

    def health(self) -> dict:
        """Check API health."""
        return self._client.health()

    def get_profile(self, pubkey: str) -> dict:
        """Get agent profile with reputation data."""
        return self._client.get_profile(pubkey)

    def get_trust_analysis(self, pubkey: str) -> dict:
        """Get full trust analysis for an agent."""
        # Profile already includes trust_analysis section
        profile = self._client.get_profile(pubkey)
        return profile.get("trust_analysis", {})

    def list_agents(self, limit: int = 200) -> list[dict]:
        """List all registered agents on the network.

        Returns list of dicts with pubkey, name, type fields.
        """
        import json
        import urllib.request

        url = f"{self._client.base_url}/agents?limit={limit}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("agents", [])

    def search(self, **kwargs) -> dict:
        """Search attestations."""
        return self._client.search(**kwargs)

    def get_taxonomy(self) -> dict:
        """Get the skill taxonomy."""
        return self._client.get_taxonomy()

    def my_profile(self) -> dict:
        """Get this agent's own attestation portfolio for presentation.

        Returns the full profile including skills, attestation counts,
        trust analysis, and trust network — everything a new service
        needs to evaluate whether to onboard this agent.

        Requires a signing key (need pubkey to look up own profile).
        """
        if not self._pubkey:
            raise ValueError(
                "Signing key required to retrieve own profile. "
                "Pass signing_key= or set KREDO_PRIVATE_KEY env var."
            )
        return self._client.get_profile(self._pubkey)

    # --- Write operations (build model, sign, submit) ---

    def register(self) -> dict:
        """Register this agent's identity on the Discovery API."""
        self._require_key()
        return self._client.register(
            pubkey=self._pubkey,
            name=self._name,
            agent_type=self._agent_type,
        )

    def attest_skill(
        self,
        subject_pubkey: str,
        domain: str,
        skill: str,
        proficiency: int,
        context: str,
        artifacts: Optional[list[str]] = None,
        outcome: str = "",
        subject_name: str = "",
        expires_days: int = 365,
    ) -> dict:
        """Build, sign, and submit a skill attestation."""
        key = self._require_key()

        attestation = Attestation(
            type=AttestationType.SKILL,
            subject=Subject(pubkey=subject_pubkey, name=subject_name),
            attestor=Attestor(
                pubkey=self._pubkey,
                name=self._name,
                type=AttestorType(self._agent_type),
            ),
            skill=Skill(
                domain=domain,
                specific=skill,
                proficiency=Proficiency(proficiency),
            ),
            evidence=Evidence(
                context=context,
                artifacts=artifacts or [],
                outcome=outcome,
            ),
            expires=datetime.now(timezone.utc) + timedelta(days=expires_days),
        )

        signed = sign_attestation(attestation, key)
        return self._client.submit_attestation(signed.model_dump(mode="json"))

    def attest_warning(
        self,
        subject_pubkey: str,
        warning_category: str,
        context: str,
        artifacts: list[str],
        outcome: str = "",
        subject_name: str = "",
        expires_days: int = 365,
    ) -> dict:
        """Build, sign, and submit a behavioral warning."""
        key = self._require_key()

        attestation = Attestation(
            type=AttestationType.WARNING,
            subject=Subject(pubkey=subject_pubkey, name=subject_name),
            attestor=Attestor(
                pubkey=self._pubkey,
                name=self._name,
                type=AttestorType(self._agent_type),
            ),
            warning_category=WarningCategory(warning_category),
            evidence=Evidence(
                context=context,
                artifacts=artifacts,
                outcome=outcome,
            ),
            expires=datetime.now(timezone.utc) + timedelta(days=expires_days),
        )

        signed = sign_attestation(attestation, key)
        return self._client.submit_attestation(signed.model_dump(mode="json"))
