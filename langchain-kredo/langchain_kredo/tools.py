"""LangChain tools for the Kredo attestation protocol.

Four tools wrapping KredoSigningClient methods:
- kredo_check_trust: Check agent reputation (read-only, safe for LLM use)
- kredo_search_attestations: Search by skill/domain/proficiency (read-only, safe for LLM use)
- kredo_submit_attestation: Sign and submit skill attestation (WRITE — requires human approval)
- kredo_get_taxonomy: Browse valid domains/skills (read-only, safe for LLM use)

SECURITY: KredoSubmitAttestationTool signs and submits irreversible cryptographic
claims to the network. Do NOT give this tool to an autonomous LLM agent without
human-in-the-loop approval. Set require_human_approval=False only if you have
an explicit confirmation mechanism in your pipeline.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


def _error_envelope(operation: str, error: Exception) -> str:
    """Return a structured JSON error envelope."""
    return json.dumps({
        "error": True,
        "operation": operation,
        "message": str(error),
        "type": type(error).__name__,
    })


# --- Input schemas ---


class CheckTrustInput(BaseModel):
    pubkey: str = Field(description="Agent's public key in ed25519:<hex> format")


class SearchAttestationsInput(BaseModel):
    domain: Optional[str] = Field(
        None, description="Filter by skill domain (e.g. 'security-operations', 'natural-language')"
    )
    skill: Optional[str] = Field(
        None, description="Filter by specific skill"
    )
    min_proficiency: Optional[int] = Field(
        None, description="Minimum proficiency level (1-5)"
    )
    subject: Optional[str] = Field(
        None, description="Filter by subject pubkey"
    )
    attestor: Optional[str] = Field(
        None, description="Filter by attestor pubkey"
    )


class SubmitAttestationInput(BaseModel):
    subject_pubkey: str = Field(
        description="Public key of the agent being attested"
    )
    domain: str = Field(description="Skill domain (e.g. 'security-operations')")
    skill: str = Field(description="Specific skill within the domain")
    proficiency: int = Field(
        description="Proficiency level 1-5 (1=novice, 5=authority)"
    )
    context: str = Field(
        description="Evidence context describing what was observed"
    )
    artifacts: list[str] = Field(
        default_factory=list, description="Evidence artifact URIs"
    )
    outcome: str = Field("", description="Outcome or result observed")
    subject_name: str = Field(
        "", description="Optional human-readable name for subject"
    )


class GetTaxonomyInput(BaseModel):
    """No input required."""

    pass


# --- Tools ---


class KredoCheckTrustTool(BaseTool):
    """Check an agent's reputation score, skills, and warnings."""

    name: str = "kredo_check_trust"
    description: str = (
        "Check an agent's trust profile on the Kredo attestation network. "
        "Returns reputation score, verified skills, and any behavioral warnings. "
        "Input: agent's public key in ed25519:<hex> format."
    )
    args_schema: Type[BaseModel] = CheckTrustInput
    client: Any  # KredoSigningClient — Any for Pydantic compatibility

    def _run(self, pubkey: str) -> str:
        try:
            profile = self.client.get_profile(pubkey)
            return json.dumps(profile, indent=2, default=str)
        except Exception as e:
            return _error_envelope("check_trust", e)

    async def _arun(self, pubkey: str) -> str:
        return self._run(pubkey)


class KredoSearchAttestationsTool(BaseTool):
    """Search for agents and attestations by skill, domain, or proficiency."""

    name: str = "kredo_search_attestations"
    description: str = (
        "Search the Kredo attestation network for agents with specific skills. "
        "Filter by domain, skill, proficiency level, subject, or attestor. "
        "Returns matching attestations with reputation data."
    )
    args_schema: Type[BaseModel] = SearchAttestationsInput
    client: Any  # KredoSigningClient

    def _run(
        self,
        domain: Optional[str] = None,
        skill: Optional[str] = None,
        min_proficiency: Optional[int] = None,
        subject: Optional[str] = None,
        attestor: Optional[str] = None,
    ) -> str:
        try:
            result = self.client.search(
                domain=domain,
                skill=skill,
                min_proficiency=min_proficiency,
                subject=subject,
                attestor=attestor,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return _error_envelope("search_attestations", e)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


class KredoSubmitAttestationTool(BaseTool):
    """Sign and submit a skill attestation to the Kredo network.

    WARNING: This tool signs and submits irreversible cryptographic claims.
    By default, it returns a preview of the attestation for human approval
    instead of submitting. Set require_human_approval=False to allow
    autonomous submission — only do this if your pipeline has an explicit
    confirmation mechanism.
    """

    name: str = "kredo_submit_attestation"
    description: str = (
        "Submit a signed skill attestation to the Kredo network. "
        "This creates a cryptographic claim about another agent's capabilities. "
        "Requires a signing key. The attestation is PERMANENT and affects reputation. "
        "By default returns a preview for human approval before submitting."
    )
    args_schema: Type[BaseModel] = SubmitAttestationInput
    client: Any  # KredoSigningClient
    require_human_approval: bool = True

    def _run(
        self,
        subject_pubkey: str,
        domain: str,
        skill: str,
        proficiency: int,
        context: str,
        artifacts: Optional[list[str]] = None,
        outcome: str = "",
        subject_name: str = "",
    ) -> str:
        if self.require_human_approval:
            preview = {
                "preview": True,
                "message": "Attestation requires human approval. Review and call with require_human_approval=False to submit.",
                "attestation": {
                    "subject_pubkey": subject_pubkey,
                    "subject_name": subject_name,
                    "domain": domain,
                    "skill": skill,
                    "proficiency": proficiency,
                    "context": context,
                    "artifacts": artifacts or [],
                    "outcome": outcome,
                },
            }
            return json.dumps(preview, indent=2)

        try:
            result = self.client.attest_skill(
                subject_pubkey=subject_pubkey,
                domain=domain,
                skill=skill,
                proficiency=proficiency,
                context=context,
                artifacts=artifacts or [],
                outcome=outcome,
                subject_name=subject_name,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return _error_envelope("submit_attestation", e)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


class KredoGetTaxonomyTool(BaseTool):
    """Browse the Kredo skill taxonomy to see valid domains and skills."""

    name: str = "kredo_get_taxonomy"
    description: str = (
        "Get the complete Kredo skill taxonomy. "
        "Returns all valid domains and their specific skills. "
        "Use this before submitting attestations to find correct domain/skill values."
    )
    args_schema: Type[BaseModel] = GetTaxonomyInput
    client: Any  # KredoSigningClient

    def _run(self) -> str:
        try:
            result = self.client.get_taxonomy()
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return _error_envelope("get_taxonomy", e)

    async def _arun(self) -> str:
        return self._run()
