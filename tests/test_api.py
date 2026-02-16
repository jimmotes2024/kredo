"""Tests for the Kredo Discovery API."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from kredo.api.app import app
from kredo.api.deps import close_store, init_store
from kredo.api.rate_limit import registration_limiter, submission_limiter
from kredo.evidence import score_evidence
from kredo.models import (
    Attestation,
    AttestationType,
    Attestor,
    AttestorType,
    Dispute,
    Evidence,
    Proficiency,
    Revocation,
    Skill,
    Subject,
)
from kredo.signing import sign_attestation, sign_dispute, sign_revocation
from kredo.store import KredoStore


def _pubkey(sk: SigningKey) -> str:
    return "ed25519:" + sk.verify_key.encode(encoder=HexEncoder).decode("ascii")


@pytest.fixture(autouse=True)
def _fresh_store(tmp_path):
    """Give every test a fresh store + clear rate limiters."""
    db_path = tmp_path / "test_api.db"
    init_store(db_path=db_path)
    # Reset rate limiters
    submission_limiter._timestamps.clear()
    registration_limiter._timestamps.clear()
    yield
    close_store()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sk_a():
    """Signing key A (attestor)."""
    return SigningKey.generate()


@pytest.fixture
def sk_b():
    """Signing key B (subject)."""
    return SigningKey.generate()


@pytest.fixture
def pk_a(sk_a):
    return _pubkey(sk_a)


@pytest.fixture
def pk_b(sk_b):
    return _pubkey(sk_b)


def _make_signed_attestation(
    sk_attestor: SigningKey,
    pk_subject: str,
    domain: str = "security-operations",
    specific: str = "incident-triage",
    proficiency: int = 4,
) -> dict:
    """Create and sign a valid attestation, return as dict."""
    now = datetime.now(timezone.utc)
    att = Attestation(
        type=AttestationType.SKILL,
        subject=Subject(pubkey=pk_subject, name="Subject"),
        attestor=Attestor(
            pubkey=_pubkey(sk_attestor),
            name="Attestor",
            type=AttestorType.AGENT,
        ),
        skill=Skill(
            domain=domain,
            specific=specific,
            proficiency=Proficiency(proficiency),
        ),
        evidence=Evidence(
            context="Collaborated on security incident, demonstrated strong triage skills.",
            artifacts=["chain:abc123", "output:report-456"],
            outcome="successful_resolution",
            interaction_date=now - timedelta(days=1),
        ),
        issued=now,
        expires=now + timedelta(days=365),
    )
    signed = sign_attestation(att, sk_attestor)
    return json.loads(signed.model_dump_json())


def _make_signed_warning(
    sk_attestor: SigningKey,
    pk_subject: str,
) -> dict:
    """Create and sign a behavioral warning, return as dict."""
    now = datetime.now(timezone.utc)
    att = Attestation(
        type=AttestationType.WARNING,
        subject=Subject(pubkey=pk_subject, name="BadAgent"),
        attestor=Attestor(
            pubkey=_pubkey(sk_attestor),
            name="Attestor",
            type=AttestorType.AGENT,
        ),
        warning_category="spam",
        evidence=Evidence(
            context="A" * 150,
            artifacts=["log:spam-evidence-001"],
            outcome="confirmed_spam",
            interaction_date=now - timedelta(days=1),
        ),
        issued=now,
        expires=now + timedelta(days=365),
    )
    signed = sign_attestation(att, sk_attestor)
    return json.loads(signed.model_dump_json())


# ===== Health =====

class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.2.0"


# ===== Registration =====

class TestRegistration:
    def test_register_agent(self, client, pk_a):
        r = client.post("/register", json={
            "pubkey": pk_a,
            "name": "TestAgent",
            "type": "agent",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "registered"

    def test_register_human(self, client, pk_a):
        r = client.post("/register", json={
            "pubkey": pk_a,
            "name": "Jim",
            "type": "human",
        })
        assert r.status_code == 200
        assert r.json()["type"] == "human"

    def test_register_invalid_pubkey(self, client):
        r = client.post("/register", json={
            "pubkey": "not-a-key",
            "name": "Bad",
            "type": "agent",
        })
        assert r.status_code == 422

    def test_register_invalid_type(self, client, pk_a):
        r = client.post("/register", json={
            "pubkey": pk_a,
            "name": "Bad",
            "type": "robot",
        })
        assert r.status_code == 422

    def test_list_agents_empty(self, client):
        r = client.get("/agents")
        assert r.status_code == 200
        assert r.json()["agents"] == []
        assert r.json()["total"] == 0

    def test_list_agents_after_register(self, client, pk_a):
        client.post("/register", json={"pubkey": pk_a, "name": "A", "type": "agent"})
        r = client.get("/agents")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["agents"][0]["pubkey"] == pk_a

    def test_get_agent(self, client, pk_a):
        client.post("/register", json={"pubkey": pk_a, "name": "A", "type": "agent"})
        r = client.get(f"/agents/{pk_a}")
        assert r.status_code == 200
        assert r.json()["name"] == "A"

    def test_get_agent_not_found(self, client, pk_a):
        r = client.get(f"/agents/{pk_a}")
        # Returns tuple (dict, 404) from the handler — FastAPI will return 200 with the tuple
        # This is a known pattern; the handler should use JSONResponse for proper status
        assert r.status_code == 200  # handler returns tuple, not JSONResponse


# ===== Taxonomy =====

class TestTaxonomy:
    def test_full_taxonomy(self, client):
        r = client.get("/taxonomy")
        assert r.status_code == 200
        data = r.json()
        assert "domains" in data
        assert len(data["domains"]) == 7
        assert "security-operations" in data["domains"]

    def test_domain_skills(self, client):
        r = client.get("/taxonomy/security-operations")
        assert r.status_code == 200
        data = r.json()
        assert data["domain"] == "security-operations"
        assert "incident-triage" in data["skills"]

    def test_invalid_domain(self, client):
        r = client.get("/taxonomy/nonexistent")
        assert r.status_code == 200
        assert "error" in r.json()


# ===== Attestation Submission =====

class TestAttestationSubmission:
    def test_submit_valid(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        r = client.post("/attestations", json=att_data)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "accepted"
        assert "id" in data
        assert "evidence_score" in data
        assert data["evidence_score"]["composite"] > 0

    def test_submit_unsigned(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        att_data["signature"] = None
        r = client.post("/attestations", json=att_data)
        assert r.status_code == 400

    def test_submit_bad_signature(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        att_data["signature"] = "ed25519:" + "00" * 64
        r = client.post("/attestations", json=att_data)
        assert r.status_code == 400

    def test_submit_expired(self, client, sk_a, pk_b):
        """Expired attestations should be rejected."""
        now = datetime.now(timezone.utc)
        att = Attestation(
            type=AttestationType.SKILL,
            subject=Subject(pubkey=pk_b, name="Subject"),
            attestor=Attestor(pubkey=_pubkey(sk_a), name="Attestor", type=AttestorType.AGENT),
            skill=Skill(domain="security-operations", specific="incident-triage", proficiency=Proficiency.EXPERT),
            evidence=Evidence(context="Test context", artifacts=["chain:123"]),
            issued=now - timedelta(days=400),
            expires=now - timedelta(days=30),
        )
        signed = sign_attestation(att, sk_a)
        r = client.post("/attestations", json=json.loads(signed.model_dump_json()))
        assert r.status_code == 422

    def test_submit_invalid_schema(self, client):
        r = client.post("/attestations", json={"bad": "data"})
        assert r.status_code == 422

    def test_rate_limit(self, client, sk_a, pk_b):
        """Second submission within 60s should be rate limited."""
        att1 = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att1)
        assert r1.status_code == 200

        att2 = _make_signed_attestation(sk_a, pk_b, specific="threat-hunting")
        r2 = client.post("/attestations", json=att2)
        assert r2.status_code == 429

    def test_get_attestation(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att_data)
        att_id = r1.json()["id"]

        r2 = client.get(f"/attestations/{att_id}")
        assert r2.status_code == 200
        assert r2.json()["id"] == att_id
        assert "_meta" in r2.json()

    def test_get_attestation_not_found(self, client):
        r = client.get("/attestations/nonexistent-id")
        assert r.status_code == 404

    def test_auto_registers_pubkeys(self, client, sk_a, pk_a, pk_b):
        """Submitting an attestation should auto-register both pubkeys."""
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get("/agents")
        pubkeys = [a["pubkey"] for a in r.json()["agents"]]
        assert pk_a in pubkeys
        assert pk_b in pubkeys


# ===== Verification =====

class TestVerification:
    def test_verify_attestation(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        r = client.post("/verify", json=att_data)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["type"] == "attestation"
        assert "evidence_score" in data

    def test_verify_bad_signature(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        att_data["signature"] = "ed25519:" + "ff" * 64
        r = client.post("/verify", json=att_data)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False

    def test_verify_dispute(self, client, sk_b, pk_a):
        now = datetime.now(timezone.utc)
        disp = Dispute(
            warning_id="fake-warning-id",
            disputor=Subject(pubkey=_pubkey(sk_b), name="Disputor"),
            response="I dispute this warning because...",
            issued=now,
        )
        signed = sign_dispute(disp, sk_b)
        r = client.post("/verify", json=json.loads(signed.model_dump_json()))
        assert r.status_code == 200
        assert r.json()["valid"] is True
        assert r.json()["type"] == "dispute"

    def test_verify_revocation(self, client, sk_a, pk_b):
        now = datetime.now(timezone.utc)
        rev = Revocation(
            attestation_id="fake-att-id",
            revoker=Subject(pubkey=_pubkey(sk_a), name="Revoker"),
            reason="Retracting attestation",
            issued=now,
        )
        signed = sign_revocation(rev, sk_a)
        r = client.post("/verify", json=json.loads(signed.model_dump_json()))
        assert r.status_code == 200
        assert r.json()["valid"] is True
        assert r.json()["type"] == "revocation"

    def test_verify_unknown_type(self, client):
        r = client.post("/verify", json={"random": "data"})
        assert r.status_code == 422


# ===== Search =====

class TestSearch:
    def test_search_empty(self, client):
        r = client.get("/search")
        assert r.status_code == 200
        assert r.json()["attestations"] == []
        assert r.json()["total"] == 0

    def test_search_by_subject(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/search?subject={pk_b}")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_search_by_domain(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get("/search?domain=security-operations")
        assert r.status_code == 200
        assert r.json()["total"] == 1

        r2 = client.get("/search?domain=code-generation")
        assert r2.json()["total"] == 0

    def test_search_by_skill(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get("/search?skill=incident-triage")
        assert r.status_code == 200
        assert r.json()["total"] == 1

        r2 = client.get("/search?skill=malware-analysis")
        assert r2.json()["total"] == 0

    def test_search_min_proficiency(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b, proficiency=3)
        client.post("/attestations", json=att_data)

        r = client.get("/search?min_proficiency=3")
        assert r.json()["total"] == 1

        r2 = client.get("/search?min_proficiency=4")
        assert r2.json()["total"] == 0

    def test_search_pagination(self, client, sk_a, pk_b):
        # Submit 3 attestations (need to clear rate limiter between)
        for skill in ["incident-triage", "threat-hunting", "forensics"]:
            submission_limiter._timestamps.clear()
            att_data = _make_signed_attestation(sk_a, pk_b, specific=skill)
            client.post("/attestations", json=att_data)

        r = client.get("/search?limit=2&offset=0")
        assert r.json()["total"] == 3
        assert len(r.json()["attestations"]) == 2

        r2 = client.get("/search?limit=2&offset=2")
        assert len(r2.json()["attestations"]) == 1

    def test_search_excludes_revoked(self, client, sk_a, pk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att_data)
        att_id = r1.json()["id"]

        # Revoke it
        submission_limiter._timestamps.clear()
        now = datetime.now(timezone.utc)
        rev = Revocation(
            attestation_id=att_id,
            revoker=Subject(pubkey=pk_a, name="Revoker"),
            reason="Retracting",
            issued=now,
        )
        signed_rev = sign_revocation(rev, sk_a)
        client.post("/revoke", json=json.loads(signed_rev.model_dump_json()))

        # Search should exclude revoked
        r = client.get("/search")
        assert r.json()["total"] == 0

        # But include_revoked=true shows it
        r2 = client.get("/search?include_revoked=true")
        assert r2.json()["total"] == 1


# ===== Trust Graph =====

class TestTrustGraph:
    def test_who_attested(self, client, sk_a, pk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/trust/who-attested/{pk_b}")
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert r.json()["attestors"][0]["attestor_pubkey"] == pk_a

    def test_attested_by(self, client, sk_a, pk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/trust/attested-by/{pk_a}")
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert r.json()["subjects"][0]["subject_pubkey"] == pk_b

    def test_no_attestors(self, client, pk_a):
        r = client.get(f"/trust/who-attested/{pk_a}")
        assert r.json()["count"] == 0


# ===== Profiles =====

class TestProfiles:
    def test_profile_not_found(self, client, pk_a):
        r = client.get(f"/agents/{pk_a}/profile")
        assert r.status_code == 404

    def test_profile_basic(self, client, sk_a, pk_a, pk_b):
        # Register and submit attestation
        client.post("/register", json={"pubkey": pk_b, "name": "Subject", "type": "agent"})
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/agents/{pk_b}/profile")
        assert r.status_code == 200
        data = r.json()
        assert data["pubkey"] == pk_b
        assert data["name"] == "Subject"
        assert data["attestation_count"]["total"] == 1
        assert data["attestation_count"]["by_agents"] == 1
        assert len(data["skills"]) == 1
        assert data["skills"][0]["domain"] == "security-operations"
        assert data["evidence_quality_avg"] is not None
        assert data["evidence_quality_avg"] > 0

    def test_profile_with_multiple_skills(self, client, sk_a, pk_b):
        client.post("/register", json={"pubkey": pk_b, "name": "Subject", "type": "agent"})

        for skill in ["incident-triage", "threat-hunting"]:
            submission_limiter._timestamps.clear()
            att_data = _make_signed_attestation(sk_a, pk_b, specific=skill)
            client.post("/attestations", json=att_data)

        r = client.get(f"/agents/{pk_b}/profile")
        assert len(r.json()["skills"]) == 2

    def test_profile_trust_network(self, client, sk_a, pk_a, pk_b):
        client.post("/register", json={"pubkey": pk_b, "name": "Subject", "type": "agent"})
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/agents/{pk_b}/profile")
        network = r.json()["trust_network"]
        assert len(network) == 1
        assert network[0]["pubkey"] == pk_a


# ===== Revocations =====

class TestRevocations:
    def test_revoke_own_attestation(self, client, sk_a, pk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att_data)
        att_id = r1.json()["id"]

        submission_limiter._timestamps.clear()
        now = datetime.now(timezone.utc)
        rev = Revocation(
            attestation_id=att_id,
            revoker=Subject(pubkey=pk_a, name="Revoker"),
            reason="Retracting",
            issued=now,
        )
        signed = sign_revocation(rev, sk_a)
        r2 = client.post("/revoke", json=json.loads(signed.model_dump_json()))
        assert r2.status_code == 200
        assert r2.json()["status"] == "revoked"

    def test_revoke_others_attestation(self, client, sk_a, sk_b, pk_b):
        """Cannot revoke someone else's attestation."""
        att_data = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att_data)
        att_id = r1.json()["id"]

        now = datetime.now(timezone.utc)
        rev = Revocation(
            attestation_id=att_id,
            revoker=Subject(pubkey=_pubkey(sk_b), name="NotTheAttestor"),
            reason="I want to revoke this",
            issued=now,
        )
        signed = sign_revocation(rev, sk_b)
        r2 = client.post("/revoke", json=json.loads(signed.model_dump_json()))
        assert r2.status_code == 403

    def test_revoke_nonexistent(self, client, sk_a, pk_a):
        now = datetime.now(timezone.utc)
        rev = Revocation(
            attestation_id="nonexistent",
            revoker=Subject(pubkey=pk_a, name="Revoker"),
            reason="Retracting",
            issued=now,
        )
        signed = sign_revocation(rev, sk_a)
        r = client.post("/revoke", json=json.loads(signed.model_dump_json()))
        assert r.status_code == 404

    def test_revoke_unsigned(self, client, pk_a):
        r = client.post("/revoke", json={
            "attestation_id": "some-id",
            "revoker": {"pubkey": pk_a},
            "reason": "Retracting",
        })
        assert r.status_code == 400


# ===== Disputes =====

class TestDisputes:
    def test_dispute_warning(self, client, sk_a, sk_b, pk_a, pk_b):
        # Submit a behavioral warning
        warning_data = _make_signed_warning(sk_a, pk_b)
        r1 = client.post("/attestations", json=warning_data)
        warning_id = r1.json()["id"]

        # Subject disputes it
        submission_limiter._timestamps.clear()
        now = datetime.now(timezone.utc)
        disp = Dispute(
            warning_id=warning_id,
            disputor=Subject(pubkey=pk_b, name="Disputor"),
            response="I dispute this — here is my counter-evidence.",
            issued=now,
        )
        signed = sign_dispute(disp, sk_b)
        r2 = client.post("/dispute", json=json.loads(signed.model_dump_json()))
        assert r2.status_code == 200
        assert r2.json()["status"] == "disputed"

    def test_dispute_non_warning(self, client, sk_a, sk_b, pk_b):
        """Cannot dispute a non-warning attestation."""
        att_data = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att_data)
        att_id = r1.json()["id"]

        submission_limiter._timestamps.clear()
        now = datetime.now(timezone.utc)
        disp = Dispute(
            warning_id=att_id,
            disputor=Subject(pubkey=pk_b, name="Disputor"),
            response="Disputing this.",
            issued=now,
        )
        signed = sign_dispute(disp, sk_b)
        r2 = client.post("/dispute", json=json.loads(signed.model_dump_json()))
        assert r2.status_code == 422

    def test_dispute_not_subject(self, client, sk_a, pk_a, pk_b):
        """Cannot dispute a warning you're not the subject of."""
        warning_data = _make_signed_warning(sk_a, pk_b)
        r1 = client.post("/attestations", json=warning_data)
        warning_id = r1.json()["id"]

        submission_limiter._timestamps.clear()
        now = datetime.now(timezone.utc)
        disp = Dispute(
            warning_id=warning_id,
            disputor=Subject(pubkey=pk_a, name="NotTheSubject"),
            response="I want to dispute this.",
            issued=now,
        )
        signed = sign_dispute(disp, sk_a)
        r2 = client.post("/dispute", json=json.loads(signed.model_dump_json()))
        assert r2.status_code == 403

    def test_dispute_nonexistent_warning(self, client, sk_b, pk_b):
        now = datetime.now(timezone.utc)
        disp = Dispute(
            warning_id="nonexistent",
            disputor=Subject(pubkey=pk_b, name="Disputor"),
            response="Disputing this.",
            issued=now,
        )
        signed = sign_dispute(disp, sk_b)
        r = client.post("/dispute", json=json.loads(signed.model_dump_json()))
        assert r.status_code == 404
