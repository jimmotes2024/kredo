"""Pydantic models for the Kredo attestation protocol.

Schema follows SCOPE.md — top-level "kredo": "1.0", pubkey on attestor/subject,
skill.specific (not skill.specific_skill), flat issued/expires/signature.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from kredo.taxonomy import is_valid_skill


# --- Enums ---

class AttestorType(str, Enum):
    AGENT = "agent"
    HUMAN = "human"


class AttestationType(str, Enum):
    SKILL = "skill_attestation"
    INTELLECTUAL = "intellectual_contribution"
    COMMUNITY = "community_contribution"
    WARNING = "behavioral_warning"


class WarningCategory(str, Enum):
    SPAM = "spam"
    MALWARE = "malware"
    DECEPTION = "deception"
    DATA_EXFILTRATION = "data_exfiltration"
    IMPERSONATION = "impersonation"


class Proficiency(int, Enum):
    NOVICE = 1
    COMPETENT = 2
    PROFICIENT = 3
    EXPERT = 4
    AUTHORITY = 5


# --- Data Models ---

class Subject(BaseModel):
    pubkey: str
    name: str = ""

    @field_validator("pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not v.startswith("ed25519:"):
            raise ValueError("pubkey must start with 'ed25519:'")
        hex_part = v[len("ed25519:"):]
        if len(hex_part) != 64:
            raise ValueError("pubkey hex portion must be 64 characters (32 bytes)")
        try:
            bytes.fromhex(hex_part)
        except ValueError:
            raise ValueError("pubkey hex portion must be valid hexadecimal")
        return v


class Attestor(BaseModel):
    pubkey: str
    name: str = ""
    type: AttestorType

    @field_validator("pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not v.startswith("ed25519:"):
            raise ValueError("pubkey must start with 'ed25519:'")
        hex_part = v[len("ed25519:"):]
        if len(hex_part) != 64:
            raise ValueError("pubkey hex portion must be 64 characters (32 bytes)")
        try:
            bytes.fromhex(hex_part)
        except ValueError:
            raise ValueError("pubkey hex portion must be valid hexadecimal")
        return v


class Skill(BaseModel):
    domain: str
    specific: str
    proficiency: Proficiency

    @model_validator(mode="after")
    def validate_taxonomy(self) -> Skill:
        if not is_valid_skill(self.domain, self.specific):
            from kredo.taxonomy import get_domains, get_skills
            if self.domain not in get_domains():
                raise ValueError(f"Unknown domain: {self.domain!r}")
            raise ValueError(
                f"Unknown skill {self.specific!r} in domain {self.domain!r}. "
                f"Valid: {get_skills(self.domain)}"
            )
        return self


class Evidence(BaseModel):
    context: str
    artifacts: list[str] = Field(default_factory=list)
    outcome: str = ""
    interaction_date: Optional[datetime] = None

    @field_validator("interaction_date", mode="before")
    @classmethod
    def parse_date(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


class Attestation(BaseModel):
    kredo: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: AttestationType
    subject: Subject
    attestor: Attestor
    skill: Optional[Skill] = None
    warning_category: Optional[WarningCategory] = None
    evidence: Evidence
    issued: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires: datetime
    signature: Optional[str] = None

    @field_validator("issued", "expires", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def validate_attestation(self) -> Attestation:
        # expires must be after issued
        if self.expires <= self.issued:
            raise ValueError("expires must be after issued")

        # behavioral warnings require warning_category
        if self.type == AttestationType.WARNING:
            if self.warning_category is None:
                raise ValueError("behavioral_warning requires warning_category")
            if len(self.evidence.artifacts) < 1:
                raise ValueError(
                    "behavioral_warning requires at least 1 evidence artifact"
                )
            if len(self.evidence.context) < 100:
                raise ValueError(
                    "behavioral_warning requires evidence context >= 100 characters"
                )

        # non-warnings should have a skill
        if self.type != AttestationType.WARNING and self.skill is None:
            raise ValueError(f"{self.type.value} requires a skill field")

        return self


class Dispute(BaseModel):
    kredo: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    warning_id: str
    disputor: Subject
    response: str
    evidence: Optional[Evidence] = None
    issued: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: Optional[str] = None

    @field_validator("issued", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


class Revocation(BaseModel):
    kredo: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    attestation_id: str
    revoker: Subject
    reason: str
    issued: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: Optional[str] = None

    @field_validator("issued", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


class Identity(BaseModel):
    pubkey: str
    name: str
    type: AttestorType
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not v.startswith("ed25519:"):
            raise ValueError("pubkey must start with 'ed25519:'")
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v
