"""Shared fixtures for langchain-kredo tests."""

import pytest
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey
from unittest.mock import MagicMock

from langchain_kredo._client import KredoSigningClient


@pytest.fixture
def keypair():
    """Generate a fresh Ed25519 keypair."""
    sk = SigningKey.generate()
    pubkey = "ed25519:" + sk.verify_key.encode(encoder=HexEncoder).decode("ascii")
    return sk, pubkey


@pytest.fixture
def second_keypair():
    """Generate a second keypair for subject/attestor separation."""
    sk = SigningKey.generate()
    pubkey = "ed25519:" + sk.verify_key.encode(encoder=HexEncoder).decode("ascii")
    return sk, pubkey


@pytest.fixture
def mock_kredo_client(keypair):
    """KredoSigningClient with mocked HTTP layer."""
    sk, pubkey = keypair
    client = KredoSigningClient(
        signing_key=sk,
        name="test-agent",
        agent_type="agent",
        api_url="http://localhost:9999",
    )
    client._client = MagicMock()
    return client


@pytest.fixture
def mock_profile():
    """Sample profile response from the API."""
    return {
        "pubkey": "ed25519:" + "ab" * 32,
        "name": "test-agent",
        "type": "agent",
        "skills": [
            {
                "domain": "security",
                "specific": "vulnerability_assessment",
                "max_proficiency": 4,
                "avg_proficiency": 3.5,
                "weighted_avg_proficiency": 3.2,
                "attestation_count": 2,
            },
        ],
        "attestation_count": {"total": 3, "by_agents": 2, "by_humans": 1},
        "warnings": [],
        "evidence_quality_avg": 0.65,
        "trust_network": [
            {
                "pubkey": "ed25519:" + "cd" * 32,
                "type": "agent",
                "attestation_count_for_subject": 2,
                "attestor_own_attestation_count": 5,
            },
            {
                "pubkey": "ed25519:" + "ef" * 32,
                "type": "human",
                "attestation_count_for_subject": 1,
                "attestor_own_attestation_count": 3,
            },
        ],
        "trust_analysis": {
            "reputation_score": 0.45,
            "ring_flags": [],
        },
    }


@pytest.fixture
def warned_profile(mock_profile):
    """Profile with behavioral warnings."""
    profile = dict(mock_profile)
    profile["warnings"] = [
        {
            "id": "warn-1",
            "category": "spam",
            "attestor": "ed25519:" + "cd" * 32,
            "issued": "2026-01-15T00:00:00Z",
            "is_revoked": False,
            "dispute_count": 0,
        },
    ]
    return profile
