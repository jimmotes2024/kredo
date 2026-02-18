"""Shared fixtures for Kredo tests."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from kredo.models import (
    AttestationType,
    AttestorType,
    Attestation,
    Attestor,
    Evidence,
    Proficiency,
    Skill,
    Subject,
)
from kredo.store import KredoStore
from kredo.taxonomy import invalidate_cache as _invalidate_taxonomy_cache


@pytest.fixture
def tmp_db(tmp_path):
    """Temporary SQLite database path."""
    return tmp_path / "test_kredo.db"


@pytest.fixture
def store(tmp_db):
    """Fresh KredoStore for each test."""
    s = KredoStore(db_path=tmp_db)
    yield s
    s.close()
    _invalidate_taxonomy_cache()


@pytest.fixture
def signing_key():
    """A fresh Ed25519 signing key."""
    return SigningKey.generate()


@pytest.fixture
def signing_key_b():
    """A second Ed25519 signing key."""
    return SigningKey.generate()


def _pubkey(sk: SigningKey) -> str:
    return "ed25519:" + sk.verify_key.encode(encoder=HexEncoder).decode("ascii")


@pytest.fixture
def pubkey(signing_key):
    return _pubkey(signing_key)


@pytest.fixture
def pubkey_b(signing_key_b):
    return _pubkey(signing_key_b)


@pytest.fixture
def sample_attestation(pubkey, pubkey_b):
    """A valid unsigned skill attestation."""
    now = datetime.now(timezone.utc)
    return Attestation(
        type=AttestationType.SKILL,
        subject=Subject(pubkey=pubkey_b, name="TestSubject"),
        attestor=Attestor(pubkey=pubkey, name="TestAttestor", type=AttestorType.AGENT),
        skill=Skill(
            domain="security-operations",
            specific="incident-triage",
            proficiency=Proficiency.EXPERT,
        ),
        evidence=Evidence(
            context="Collaborated on phishing incident chain, agent performed IOC extraction",
            artifacts=["chain:abc123", "output:report-456"],
            outcome="successful_resolution",
            interaction_date=now - timedelta(days=1),
        ),
        issued=now,
        expires=now + timedelta(days=365),
    )


@pytest.fixture
def sample_warning(pubkey, pubkey_b):
    """A valid unsigned behavioral warning."""
    now = datetime.now(timezone.utc)
    return Attestation(
        type=AttestationType.WARNING,
        subject=Subject(pubkey=pubkey_b, name="BadAgent"),
        attestor=Attestor(pubkey=pubkey, name="TestAttestor", type=AttestorType.AGENT),
        warning_category="spam",
        evidence=Evidence(
            context="A" * 150,  # >100 chars required
            artifacts=["log:spam-evidence-001"],
            outcome="confirmed_spam",
            interaction_date=now - timedelta(days=1),
        ),
        issued=now,
        expires=now + timedelta(days=365),
    )
