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

from kredo._canonical import canonical_json
from kredo.api.app import _get_cors_settings, app
from kredo.api.deps import close_store, init_store
from kredo.api.rate_limit import registration_limiter, submission_limiter
from kredo.api.trust_cache import invalidate_trust_cache
from kredo.taxonomy import invalidate_cache as _invalidate_taxonomy_cache, set_store as _set_taxonomy_store
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


def _sign_payload(payload: dict, sk: SigningKey) -> str:
    signed = sk.sign(canonical_json(payload), encoder=HexEncoder)
    return "ed25519:" + signed.signature.decode("ascii")


@pytest.fixture(autouse=True)
def _fresh_store(tmp_path):
    """Give every test a fresh store + clear rate limiters."""
    db_path = tmp_path / "test_api.db"
    store = init_store(db_path=db_path)
    _set_taxonomy_store(store)
    invalidate_trust_cache()
    # Reset rate limiters
    submission_limiter._timestamps.clear()
    registration_limiter._timestamps.clear()
    yield
    invalidate_trust_cache()
    _invalidate_taxonomy_cache()
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


def _sample_file_hashes(version: int = 1) -> list[dict]:
    if version == 1:
        return [
            {"path": "agent.py", "sha256": "1" * 64},
            {"path": "policy.yaml", "sha256": "2" * 64},
        ]
    if version == 2:
        return [
            {"path": "agent.py", "sha256": "3" * 64},  # changed
            {"path": "policy.yaml", "sha256": "2" * 64},
        ]
    raise ValueError("Unsupported version for sample file hashes")


# ===== Health =====

class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["version"]  # Just verify it's present


class TestCorsSettings:
    def test_cors_settings_defaults(self):
        settings = _get_cors_settings({})
        assert settings["allow_origins"] == [
            "https://aikredo.com",
            "https://app.aikredo.com",
            "http://localhost:5173",
            "http://localhost:3000",
        ]
        assert settings["allow_methods"] == ["GET", "POST", "DELETE", "OPTIONS"]
        assert settings["allow_headers"] == ["Content-Type", "Authorization"]
        assert settings["allow_credentials"] is False

    def test_cors_settings_env_override(self):
        settings = _get_cors_settings({
            "KREDO_CORS_ALLOW_ORIGINS": "https://foo.test, http://localhost:9000",
            "KREDO_CORS_ALLOW_METHODS": "GET,POST,PATCH",
            "KREDO_CORS_ALLOW_HEADERS": "Content-Type,X-Api-Key",
            "KREDO_CORS_ALLOW_CREDENTIALS": "true",
        })
        assert settings["allow_origins"] == ["https://foo.test", "http://localhost:9000"]
        assert settings["allow_methods"] == ["GET", "POST", "PATCH"]
        assert settings["allow_headers"] == ["Content-Type", "X-Api-Key"]
        assert settings["allow_credentials"] is True

    def test_cors_settings_wildcard_origin(self):
        settings = _get_cors_settings({"KREDO_CORS_ALLOW_ORIGINS": "*"})
        assert settings["allow_origins"] == ["*"]


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

    def test_register_existing_key_does_not_overwrite_without_signature(self, client, pk_a):
        first = client.post("/register", json={
            "pubkey": pk_a,
            "name": "Trusted",
            "type": "agent",
        })
        assert first.status_code == 200

        registration_limiter._timestamps.clear()
        second = client.post("/register", json={
            "pubkey": pk_a,
            "name": "OverwriteAttempt",
            "type": "human",
        })
        assert second.status_code == 200

        lookup = client.get(f"/agents/{pk_a}")
        assert lookup.status_code == 200
        assert lookup.json()["name"] == "Trusted"
        assert lookup.json()["type"] == "agent"

    def test_register_update_signed(self, client, sk_a, pk_a):
        seeded = client.post("/register", json={
            "pubkey": pk_a,
            "name": "Initial",
            "type": "agent",
        })
        assert seeded.status_code == 200

        payload = {
            "action": "update_registration",
            "pubkey": pk_a,
            "name": "Renamed",
            "type": "human",
        }
        signature = _sign_payload(payload, sk_a)
        updated = client.post("/register/update", json={**payload, "signature": signature})
        assert updated.status_code == 200
        assert updated.json()["status"] == "updated"
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["type"] == "human"

        lookup = client.get(f"/agents/{pk_a}")
        assert lookup.status_code == 200
        assert lookup.json()["name"] == "Renamed"
        assert lookup.json()["type"] == "human"

    def test_register_update_rejects_bad_signature(self, client, sk_a, sk_b, pk_a):
        seeded = client.post("/register", json={
            "pubkey": pk_a,
            "name": "Initial",
            "type": "agent",
        })
        assert seeded.status_code == 200

        payload = {
            "action": "update_registration",
            "pubkey": pk_a,
            "name": "Renamed",
            "type": "agent",
        }
        signature = _sign_payload(payload, sk_b)
        denied = client.post("/register/update", json={**payload, "signature": signature})
        assert denied.status_code == 400
        assert "Signature verification failed" in denied.json()["error"]

    def test_register_update_unknown_agent(self, client, sk_a, pk_a):
        payload = {
            "action": "update_registration",
            "pubkey": pk_a,
            "name": "Renamed",
            "type": "agent",
        }
        signature = _sign_payload(payload, sk_a)
        missing = client.post("/register/update", json={**payload, "signature": signature})
        assert missing.status_code == 404

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
        assert r.status_code == 404


class TestOwnership:
    def test_ownership_claim_confirm_and_lookup(self, client, sk_a, sk_b):
        pk_agent = _pubkey(sk_a)
        pk_human = _pubkey(sk_b)

        r1 = client.post("/register", json={"pubkey": pk_agent, "name": "AgentOne", "type": "agent"})
        assert r1.status_code == 200
        registration_limiter._timestamps.clear()
        r2 = client.post("/register", json={"pubkey": pk_human, "name": "OwnerOne", "type": "human"})
        assert r2.status_code == 200

        claim_payload = {
            "action": "ownership_claim",
            "claim_id": "ownclaim01",
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_human,
        }
        claim_sig = _sign_payload(claim_payload, sk_a)
        claim = client.post(
            "/ownership/claim",
            json={
                "claim_id": "ownclaim01",
                "agent_pubkey": pk_agent,
                "human_pubkey": pk_human,
                "signature": claim_sig,
            },
        )
        assert claim.status_code == 200
        assert claim.json()["status"] == "pending"

        confirm_payload = {
            "action": "ownership_confirm",
            "claim_id": "ownclaim01",
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_human,
        }
        confirm_sig = _sign_payload(confirm_payload, sk_b)
        confirm = client.post(
            "/ownership/confirm",
            json={
                "claim_id": "ownclaim01",
                "human_pubkey": pk_human,
                "signature": confirm_sig,
                "contact_email": "owner.one@example.com",
            },
        )
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "active"
        assert confirm.json()["contact_email_saved"] is True

        lookup = client.get(f"/ownership/agent/{pk_agent}")
        assert lookup.status_code == 200
        assert lookup.json()["active_owner"]["human_pubkey"] == pk_human
        assert len(lookup.json()["claims"]) == 1

    def test_ownership_claim_requires_human_registration(self, client, sk_a, sk_b):
        pk_agent = _pubkey(sk_a)
        pk_human = _pubkey(sk_b)
        client.post("/register", json={"pubkey": pk_agent, "name": "AgentOne", "type": "agent"})

        payload = {
            "action": "ownership_claim",
            "claim_id": "ownclaim02",
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_human,
        }
        signature = _sign_payload(payload, sk_a)
        r = client.post(
            "/ownership/claim",
            json={
                "claim_id": "ownclaim02",
                "agent_pubkey": pk_agent,
                "human_pubkey": pk_human,
                "signature": signature,
            },
        )
        assert r.status_code == 404


class TestIntegrity:
    def _link_agent_to_owner(self, client, sk_agent: SigningKey, sk_owner: SigningKey, claim_id: str):
        pk_agent = _pubkey(sk_agent)
        pk_owner = _pubkey(sk_owner)

        registration_limiter._timestamps.clear()
        r1 = client.post("/register", json={"pubkey": pk_agent, "name": "AgentOne", "type": "agent"})
        assert r1.status_code == 200
        registration_limiter._timestamps.clear()
        r2 = client.post("/register", json={"pubkey": pk_owner, "name": "OwnerOne", "type": "human"})
        assert r2.status_code == 200

        claim_payload = {
            "action": "ownership_claim",
            "claim_id": claim_id,
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_owner,
        }
        claim_sig = _sign_payload(claim_payload, sk_agent)
        claim = client.post(
            "/ownership/claim",
            json={
                "claim_id": claim_id,
                "agent_pubkey": pk_agent,
                "human_pubkey": pk_owner,
                "signature": claim_sig,
            },
        )
        assert claim.status_code == 200

        confirm_payload = {
            "action": "ownership_confirm",
            "claim_id": claim_id,
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_owner,
        }
        confirm_sig = _sign_payload(confirm_payload, sk_owner)
        confirm = client.post(
            "/ownership/confirm",
            json={
                "claim_id": claim_id,
                "human_pubkey": pk_owner,
                "signature": confirm_sig,
            },
        )
        assert confirm.status_code == 200
        return pk_agent, pk_owner

    def _set_baseline(self, client, sk_owner: SigningKey, pk_agent: str, pk_owner: str, baseline_id: str):
        file_hashes = _sample_file_hashes(1)
        payload = {
            "action": "integrity_set_baseline",
            "baseline_id": baseline_id,
            "agent_pubkey": pk_agent,
            "owner_pubkey": pk_owner,
            "file_hashes": file_hashes,
        }
        signature = _sign_payload(payload, sk_owner)
        return client.post(
            "/integrity/baseline/set",
            json={
                "baseline_id": baseline_id,
                "agent_pubkey": pk_agent,
                "owner_pubkey": pk_owner,
                "file_hashes": file_hashes,
                "signature": signature,
            },
        )

    def _integrity_check(self, client, sk_agent: SigningKey, pk_agent: str, file_hashes: list[dict]):
        payload = {
            "action": "integrity_check",
            "agent_pubkey": pk_agent,
            "file_hashes": file_hashes,
        }
        signature = _sign_payload(payload, sk_agent)
        return client.post(
            "/integrity/check",
            json={
                "agent_pubkey": pk_agent,
                "file_hashes": file_hashes,
                "signature": signature,
            },
        )

    def test_set_baseline_requires_active_owner(self, client):
        sk_agent = SigningKey.generate()
        sk_owner = SigningKey.generate()
        pk_agent = _pubkey(sk_agent)
        pk_owner = _pubkey(sk_owner)

        registration_limiter._timestamps.clear()
        client.post("/register", json={"pubkey": pk_agent, "name": "AgentOne", "type": "agent"})
        registration_limiter._timestamps.clear()
        client.post("/register", json={"pubkey": pk_owner, "name": "OwnerOne", "type": "human"})

        denied = self._set_baseline(
            client=client,
            sk_owner=sk_owner,
            pk_agent=pk_agent,
            pk_owner=pk_owner,
            baseline_id="baseline1001",
        )
        assert denied.status_code == 403
        assert "active owner" in denied.json()["error"].lower()

    def test_integrity_green_path_and_status(self, client):
        sk_agent = SigningKey.generate()
        sk_owner = SigningKey.generate()
        pk_agent, pk_owner = self._link_agent_to_owner(
            client=client,
            sk_agent=sk_agent,
            sk_owner=sk_owner,
            claim_id="ownclaim10",
        )

        baseline = self._set_baseline(
            client=client,
            sk_owner=sk_owner,
            pk_agent=pk_agent,
            pk_owner=pk_owner,
            baseline_id="baseline1002",
        )
        assert baseline.status_code == 200
        baseline_data = baseline.json()
        assert baseline_data["status"] == "baseline_set"
        assert baseline_data["traffic_light"] == "yellow"
        assert baseline_data["status_label"] == "baseline_set_not_checked"
        assert baseline_data["requires_owner_reapproval"] is True

        status_after_baseline = client.get(f"/integrity/status/{pk_agent}")
        assert status_after_baseline.status_code == 200
        assert status_after_baseline.json()["traffic_light"] == "yellow"

        check = self._integrity_check(
            client=client,
            sk_agent=sk_agent,
            pk_agent=pk_agent,
            file_hashes=_sample_file_hashes(1),
        )
        assert check.status_code == 200
        check_data = check.json()
        assert check_data["status"] == "green"
        assert check_data["traffic_light"] == "green"
        assert check_data["recommended_action"] == "safe_to_run"
        assert check_data["requires_owner_reapproval"] is False
        assert check_data["diff"]["added_paths"] == []
        assert check_data["diff"]["removed_paths"] == []
        assert check_data["diff"]["changed_paths"] == []

        status_after_check = client.get(f"/integrity/status/{pk_agent}")
        assert status_after_check.status_code == 200
        status_data = status_after_check.json()
        assert status_data["traffic_light"] == "green"
        assert status_data["status_label"] == "verified"
        assert status_data["recommended_action"] == "safe_to_run"

    def test_integrity_detects_change_and_requires_reapproval(self, client):
        sk_agent = SigningKey.generate()
        sk_owner = SigningKey.generate()
        pk_agent, pk_owner = self._link_agent_to_owner(
            client=client,
            sk_agent=sk_agent,
            sk_owner=sk_owner,
            claim_id="ownclaim11",
        )

        baseline = self._set_baseline(
            client=client,
            sk_owner=sk_owner,
            pk_agent=pk_agent,
            pk_owner=pk_owner,
            baseline_id="baseline1003",
        )
        assert baseline.status_code == 200

        changed = self._integrity_check(
            client=client,
            sk_agent=sk_agent,
            pk_agent=pk_agent,
            file_hashes=_sample_file_hashes(2),
        )
        assert changed.status_code == 200
        changed_data = changed.json()
        assert changed_data["status"] == "yellow"
        assert changed_data["traffic_light"] == "yellow"
        assert changed_data["status_label"] == "changed_since_baseline"
        assert changed_data["requires_owner_reapproval"] is True
        assert changed_data["diff"]["changed_paths"] == ["agent.py"]

        status = client.get(f"/integrity/status/{pk_agent}")
        assert status.status_code == 200
        status_data = status.json()
        assert status_data["traffic_light"] == "yellow"
        assert status_data["status_label"] == "changed_since_baseline"


# ===== Taxonomy =====

class TestTaxonomy:
    def test_full_taxonomy(self, client):
        r = client.get("/taxonomy")
        assert r.status_code == 200
        data = r.json()
        assert "domains" in data
        assert len(data["domains"]) == 8
        assert "security-operations" in data["domains"]

    def test_domain_skills(self, client):
        r = client.get("/taxonomy/security-operations")
        assert r.status_code == 200
        data = r.json()
        assert data["domain"] == "security-operations"
        assert "incident-triage" in data["skills"]

    def test_invalid_domain(self, client):
        r = client.get("/taxonomy/nonexistent")
        assert r.status_code == 404
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

    def test_submit_duplicate_id_conflict(self, client, sk_a, pk_b):
        att_data = _make_signed_attestation(sk_a, pk_b)
        r1 = client.post("/attestations", json=att_data)
        assert r1.status_code == 200

        # Clear limiter so duplicate-ID behavior is exercised (not 429 throttling).
        submission_limiter._timestamps.clear()
        r2 = client.post("/attestations", json=att_data)
        assert r2.status_code == 409
        assert "already exists" in r2.json()["error"]

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

    def test_search_skill_filter_uses_sql_pagination(self, client, sk_a, pk_b):
        for skill in ["incident-triage", "incident-triage", "incident-triage", "threat-hunting"]:
            submission_limiter._timestamps.clear()
            att_data = _make_signed_attestation(sk_a, pk_b, specific=skill)
            client.post("/attestations", json=att_data)

        first = client.get("/search?skill=incident-triage&limit=2&offset=0")
        assert first.status_code == 200
        assert first.json()["total"] == 3
        assert len(first.json()["attestations"]) == 2

        second = client.get("/search?skill=incident-triage&limit=2&offset=2")
        assert second.status_code == 200
        assert second.json()["total"] == 3
        assert len(second.json()["attestations"]) == 1

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


# ===== Trust Analysis =====

class TestTrustAnalysis:
    def test_analysis_unknown_agent(self, client, pk_a):
        """Analysis of unknown agent returns zero reputation."""
        r = client.get(f"/trust/analysis/{pk_a}")
        assert r.status_code == 200
        data = r.json()
        assert data["pubkey"] == pk_a
        assert data["reputation_score"] == 0.0
        assert data["attestation_weights"] == []
        assert data["rings_involved"] == []

    def test_analysis_with_attestation(self, client, sk_a, pk_a, pk_b):
        """Agent with attestation has nonzero analysis."""
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/trust/analysis/{pk_b}")
        assert r.status_code == 200
        data = r.json()
        assert data["reputation_score"] > 0
        assert len(data["attestation_weights"]) == 1
        assert data["attestation_weights"][0]["attestor_reputation"] == 0.0
        assert "unattested_attestor" in data["attestation_weights"][0]["flags"]
        assert len(data["weighted_skills"]) == 1
        assert data["analysis_timestamp"] is not None

    def test_rings_empty(self, client):
        """Rings endpoint on empty DB."""
        r = client.get("/trust/rings")
        assert r.status_code == 200
        assert r.json()["ring_count"] == 0
        assert r.json()["rings"] == []

    def test_rings_with_mutual_pair(self, client, sk_a, sk_b, pk_a, pk_b):
        """Mutual attestation pair shows up in rings."""
        # A attests B
        att1 = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att1)

        # B attests A
        submission_limiter._timestamps.clear()
        att2 = _make_signed_attestation(sk_b, pk_a)
        client.post("/attestations", json=att2)

        r = client.get("/trust/rings")
        assert r.status_code == 200
        data = r.json()
        assert data["ring_count"] == 1
        assert data["rings"][0]["ring_type"] == "mutual_pair"
        assert data["rings"][0]["size"] == 2

    def test_network_health_empty(self, client):
        """Network health on empty DB."""
        r = client.get("/trust/network-health")
        assert r.status_code == 200
        data = r.json()
        assert data["total_agents_in_graph"] == 0
        assert data["total_directed_edges"] == 0

    def test_network_health_with_data(self, client, sk_a, pk_b):
        """Network health with attestation data."""
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get("/trust/network-health")
        assert r.status_code == 200
        data = r.json()
        assert data["total_agents_in_graph"] == 2
        assert data["total_directed_edges"] == 1

    def test_profile_includes_trust_analysis(self, client, sk_a, pk_b):
        """Profile endpoint now includes trust_analysis and weighted_avg_proficiency."""
        client.post("/register", json={"pubkey": pk_b, "name": "Subject", "type": "agent"})
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        r = client.get(f"/agents/{pk_b}/profile")
        assert r.status_code == 200
        data = r.json()

        # Trust analysis section present
        assert "trust_analysis" in data
        assert "reputation_score" in data["trust_analysis"]
        assert data["trust_analysis"]["reputation_score"] > 0
        assert "ring_flags" in data["trust_analysis"]

        # Skills include weighted_avg_proficiency
        assert len(data["skills"]) == 1
        assert "weighted_avg_proficiency" in data["skills"][0]

    def test_trust_cache_invalidated_on_attestation_write(self, client, sk_a, pk_b):
        """Repeated read should hit cache, then write should invalidate it."""
        att_data = _make_signed_attestation(sk_a, pk_b)
        client.post("/attestations", json=att_data)

        first = client.get(f"/trust/analysis/{pk_b}")
        assert first.status_code == 200
        first_data = first.json()
        first_ts = first_data["analysis_timestamp"]
        assert len(first_data["attestation_weights"]) == 1

        second = client.get(f"/trust/analysis/{pk_b}")
        assert second.status_code == 200
        second_data = second.json()
        assert second_data["analysis_timestamp"] == first_ts
        assert len(second_data["attestation_weights"]) == 1

        submission_limiter._timestamps.clear()
        sk_c = SigningKey.generate()
        second_att = _make_signed_attestation(sk_c, pk_b, specific="threat-hunting")
        client.post("/attestations", json=second_att)

        third = client.get(f"/trust/analysis/{pk_b}")
        assert third.status_code == 200
        third_data = third.json()
        assert third_data["analysis_timestamp"] != first_ts
        assert len(third_data["attestation_weights"]) == 2

    def test_accountability_tier_unlinked_and_human_linked(self, client, sk_a, sk_b):
        """Unlinked agents get accountability multiplier; linked agents move to human-linked."""
        pk_attestor = _pubkey(sk_a)
        pk_agent = _pubkey(sk_b)
        sk_c = SigningKey.generate()
        pk_human = _pubkey(sk_c)

        att_data = _make_signed_attestation(sk_a, pk_agent)
        client.post("/attestations", json=att_data)

        baseline = client.get(f"/trust/analysis/{pk_agent}")
        assert baseline.status_code == 200
        baseline_data = baseline.json()
        assert baseline_data["reputation_score"] > 0
        assert baseline_data["accountability"]["tier"] == "unlinked"
        assert baseline_data["deployability_score"] < baseline_data["reputation_score"]

        registration_limiter._timestamps.clear()
        client.post("/register", json={"pubkey": pk_human, "name": "Owner", "type": "human"})
        registration_limiter._timestamps.clear()
        client.post("/register", json={"pubkey": pk_agent, "name": "Agent", "type": "agent"})

        claim_payload = {
            "action": "ownership_claim",
            "claim_id": "ownclaim03",
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_human,
        }
        claim_sig = _sign_payload(claim_payload, sk_b)
        client.post(
            "/ownership/claim",
            json={
                "claim_id": "ownclaim03",
                "agent_pubkey": pk_agent,
                "human_pubkey": pk_human,
                "signature": claim_sig,
            },
        )
        confirm_payload = {
            "action": "ownership_confirm",
            "claim_id": "ownclaim03",
            "agent_pubkey": pk_agent,
            "human_pubkey": pk_human,
        }
        confirm_sig = _sign_payload(confirm_payload, sk_c)
        client.post(
            "/ownership/confirm",
            json={
                "claim_id": "ownclaim03",
                "human_pubkey": pk_human,
                "signature": confirm_sig,
            },
        )

        linked = client.get(f"/trust/analysis/{pk_agent}")
        assert linked.status_code == 200
        linked_data = linked.json()
        assert linked_data["accountability"]["tier"] == "human-linked"
        assert linked_data["integrity"]["traffic_light"] == "red"
        assert linked_data["deployability_score"] < linked_data["reputation_score"]

        baseline_payload = {
            "action": "integrity_set_baseline",
            "baseline_id": "baseline1004",
            "agent_pubkey": pk_agent,
            "owner_pubkey": pk_human,
            "file_hashes": _sample_file_hashes(1),
        }
        baseline_sig = _sign_payload(baseline_payload, sk_c)
        set_baseline = client.post(
            "/integrity/baseline/set",
            json={
                "baseline_id": "baseline1004",
                "agent_pubkey": pk_agent,
                "owner_pubkey": pk_human,
                "file_hashes": _sample_file_hashes(1),
                "signature": baseline_sig,
            },
        )
        assert set_baseline.status_code == 200

        check_payload = {
            "action": "integrity_check",
            "agent_pubkey": pk_agent,
            "file_hashes": _sample_file_hashes(1),
        }
        check_sig = _sign_payload(check_payload, sk_b)
        check = client.post(
            "/integrity/check",
            json={
                "agent_pubkey": pk_agent,
                "file_hashes": _sample_file_hashes(1),
                "signature": check_sig,
            },
        )
        assert check.status_code == 200

        verified = client.get(f"/trust/analysis/{pk_agent}")
        assert verified.status_code == 200
        verified_data = verified.json()
        assert verified_data["integrity"]["traffic_light"] == "green"
        assert verified_data["deployability_score"] == verified_data["reputation_score"]


class TestRiskSignals:
    def test_source_anomalies_endpoint(self, client):
        for idx in range(4):
            registration_limiter._timestamps.clear()
            sk = SigningKey.generate()
            pk = _pubkey(sk)
            client.post(
                "/register",
                json={"pubkey": pk, "name": f"actor-{idx}", "type": "agent"},
            )

        r = client.get("/risk/source-anomalies?hours=24&min_events=3&min_unique_actors=3")
        assert r.status_code == 200
        data = r.json()
        assert data["cluster_count"] >= 1
        assert data["clusters"][0]["event_count"] >= 3
        assert data["clusters"][0]["unique_actor_count"] >= 3
