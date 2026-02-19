"""Tests for kredo.store — SQLite CRUD, trust graph, import/export."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from kredo.exceptions import DuplicateAttestationError, KeyNotFoundError, StoreError
from kredo.models import (
    AttestationType,
    AttestorType,
    Attestation,
    Attestor,
    Dispute,
    Evidence,
    Proficiency,
    Revocation,
    Skill,
    Subject,
)


def _make_pubkey(n=0):
    return "ed25519:" + f"{n:064x}"


class TestIdentityStorage:
    def test_save_and_get(self, store):
        store.save_identity(_make_pubkey(1), "Alice", "agent", b"seed", False, True)
        row = store.get_identity(_make_pubkey(1))
        assert row["name"] == "Alice"
        assert row["is_default"] == 1

    def test_get_missing_raises(self, store):
        with pytest.raises(KeyNotFoundError):
            store.get_identity(_make_pubkey(99))

    def test_list_identities(self, store):
        store.save_identity(_make_pubkey(1), "A", "agent")
        store.save_identity(_make_pubkey(2), "B", "human")
        ids = store.list_identities()
        assert len(ids) == 2

    def test_default_identity(self, store):
        store.save_identity(_make_pubkey(1), "A", "agent", is_default=True)
        store.save_identity(_make_pubkey(2), "B", "agent", is_default=False)
        default = store.get_default_identity()
        assert default["pubkey"] == _make_pubkey(1)

    def test_set_default_clears_old(self, store):
        store.save_identity(_make_pubkey(1), "A", "agent", is_default=True)
        store.save_identity(_make_pubkey(2), "B", "agent", is_default=False)
        store.set_default_identity(_make_pubkey(2))
        default = store.get_default_identity()
        assert default["pubkey"] == _make_pubkey(2)

    def test_get_private_key(self, store):
        store.save_identity(_make_pubkey(1), "A", "agent", b"secret_seed", True)
        key_blob, is_enc = store.get_private_key(_make_pubkey(1))
        assert key_blob == b"secret_seed"
        assert is_enc is True


class TestAttestationStorage:
    def _make_attestation_json(self, attestor_n=1, subject_n=2):
        now = datetime.now(timezone.utc)
        att = Attestation(
            type=AttestationType.SKILL,
            subject=Subject(pubkey=_make_pubkey(subject_n), name="Subject"),
            attestor=Attestor(pubkey=_make_pubkey(attestor_n), name="Attestor", type=AttestorType.AGENT),
            skill=Skill(domain="reasoning", specific="planning", proficiency=Proficiency.PROFICIENT),
            evidence=Evidence(context="Worked together on planning task"),
            issued=now,
            expires=now + timedelta(days=365),
        )
        return att.model_dump_json(indent=2), att.id

    def test_save_and_get(self, store):
        json_str, att_id = self._make_attestation_json()
        store.save_attestation(json_str)
        result = store.get_attestation(att_id)
        assert result["id"] == att_id

    def test_get_missing_returns_none(self, store):
        assert store.get_attestation("nonexistent") is None

    def test_search_by_subject(self, store):
        json_str, _ = self._make_attestation_json(attestor_n=1, subject_n=2)
        store.save_attestation(json_str)
        results = store.search_attestations(subject_pubkey=_make_pubkey(2))
        assert len(results) == 1

    def test_search_by_domain(self, store):
        json_str, _ = self._make_attestation_json()
        store.save_attestation(json_str)
        results = store.search_attestations(domain="reasoning")
        assert len(results) == 1
        results = store.search_attestations(domain="collaboration")
        assert len(results) == 0

    def test_save_duplicate_id_raises(self, store):
        json_str, _ = self._make_attestation_json()
        store.save_attestation(json_str)
        with pytest.raises(DuplicateAttestationError):
            store.save_attestation(json_str)


class TestTrustGraph:
    def _populate(self, store):
        now = datetime.now(timezone.utc)
        for i in range(3):
            att = Attestation(
                type=AttestationType.SKILL,
                subject=Subject(pubkey=_make_pubkey(10), name="Target"),
                attestor=Attestor(pubkey=_make_pubkey(i), name=f"Attestor{i}", type=AttestorType.AGENT),
                skill=Skill(domain="reasoning", specific="planning", proficiency=Proficiency.COMPETENT),
                evidence=Evidence(context="test"),
                issued=now,
                expires=now + timedelta(days=365),
            )
            store.save_attestation(att.model_dump_json())

    def test_get_attestors_for(self, store):
        self._populate(store)
        attestors = store.get_attestors_for(_make_pubkey(10))
        assert len(attestors) == 3

    def test_get_attested_by(self, store):
        self._populate(store)
        subjects = store.get_attested_by(_make_pubkey(0))
        assert len(subjects) == 1
        assert subjects[0]["subject_pubkey"] == _make_pubkey(10)


class TestRevocationStorage:
    def test_save_revocation_marks_attestation(self, store):
        now = datetime.now(timezone.utc)
        att = Attestation(
            type=AttestationType.SKILL,
            subject=Subject(pubkey=_make_pubkey(1), name="S"),
            attestor=Attestor(pubkey=_make_pubkey(2), name="A", type=AttestorType.AGENT),
            skill=Skill(domain="reasoning", specific="planning", proficiency=Proficiency.NOVICE),
            evidence=Evidence(context="test"),
            issued=now,
            expires=now + timedelta(days=365),
        )
        att_json = att.model_dump_json()
        store.save_attestation(att_json)

        rev = Revocation(
            attestation_id=att.id,
            revoker=Subject(pubkey=_make_pubkey(2), name="A"),
            reason="Changed my mind",
        )
        store.save_revocation(rev.model_dump_json())

        # Attestation should be marked revoked
        row = store.get_attestation_row(att.id)
        assert row["is_revoked"] == 1

        # Search should exclude revoked by default
        results = store.search_attestations(subject_pubkey=_make_pubkey(1))
        assert len(results) == 0

        # But include_revoked shows it
        results = store.search_attestations(subject_pubkey=_make_pubkey(1), include_revoked=True)
        assert len(results) == 1


class TestDisputeStorage:
    def test_save_and_get_dispute(self, store):
        # First create a warning to dispute against (FK constraint)
        now = datetime.now(timezone.utc)
        warning = Attestation(
            type=AttestationType.WARNING,
            subject=Subject(pubkey=_make_pubkey(1), name="S"),
            attestor=Attestor(pubkey=_make_pubkey(2), name="A", type=AttestorType.AGENT),
            warning_category="spam",
            evidence=Evidence(context="A" * 150, artifacts=["log:evidence"]),
            issued=now,
            expires=now + timedelta(days=365),
        )
        store.save_attestation(warning.model_dump_json())

        disp = Dispute(
            warning_id=warning.id,
            disputor=Subject(pubkey=_make_pubkey(1), name="D"),
            response="I dispute this",
        )
        store.save_dispute(disp.model_dump_json())
        disputes = store.get_disputes_for(warning.id)
        assert len(disputes) == 1
        assert disputes[0]["response"] == "I dispute this"


class TestImportExport:
    def test_export_roundtrip(self, store):
        now = datetime.now(timezone.utc)
        att = Attestation(
            type=AttestationType.COMMUNITY,
            subject=Subject(pubkey=_make_pubkey(1), name="S"),
            attestor=Attestor(pubkey=_make_pubkey(2), name="A", type=AttestorType.HUMAN),
            skill=Skill(domain="collaboration", specific="mentoring", proficiency=Proficiency.AUTHORITY),
            evidence=Evidence(context="Excellent mentor"),
            issued=now,
            expires=now + timedelta(days=365),
        )
        store.save_attestation(att.model_dump_json())
        exported = store.export_attestation_json(att.id)
        assert exported is not None
        data = json.loads(exported)
        assert data["id"] == att.id

    def test_import_attestation(self, store):
        now = datetime.now(timezone.utc)
        att = Attestation(
            type=AttestationType.INTELLECTUAL,
            subject=Subject(pubkey=_make_pubkey(1), name="S"),
            attestor=Attestor(pubkey=_make_pubkey(2), name="A", type=AttestorType.AGENT),
            skill=Skill(domain="reasoning", specific="conceptual-analysis", proficiency=Proficiency.EXPERT),
            evidence=Evidence(context="Great conceptual analysis"),
            issued=now,
            expires=now + timedelta(days=365),
        )
        json_str = att.model_dump_json(indent=2)
        att_id = store.import_attestation_json(json_str)
        assert att_id == att.id
        result = store.get_attestation(att_id)
        assert result is not None


class TestContacts:
    def test_find_key_by_name_identity(self, store):
        """find_key_by_name should search identities first."""
        store.save_identity(_make_pubkey(1), "Alice", "agent", b"seed", False, True)
        result = store.find_key_by_name("Alice")
        assert result is not None
        assert result["pubkey"] == _make_pubkey(1)

    def test_find_key_by_name_case_insensitive(self, store):
        store.save_identity(_make_pubkey(1), "Alice", "agent", b"seed", False, True)
        result = store.find_key_by_name("alice")
        assert result is not None
        assert result["name"] == "Alice"

    def test_find_key_by_name_known_key(self, store):
        """find_key_by_name should fall back to known_keys."""
        store.register_known_key(_make_pubkey(2), name="Bob", attestor_type="human")
        result = store.find_key_by_name("Bob")
        assert result is not None
        assert result["pubkey"] == _make_pubkey(2)

    def test_find_key_by_name_not_found(self, store):
        result = store.find_key_by_name("Nobody")
        assert result is None

    def test_list_contacts(self, store):
        store.register_known_key(_make_pubkey(1), name="Alice")
        store.register_known_key(_make_pubkey(2), name="Bob")
        contacts = store.list_contacts()
        assert len(contacts) == 2
        names = {c["name"] for c in contacts}
        assert names == {"Alice", "Bob"}

    def test_list_contacts_empty(self, store):
        assert store.list_contacts() == []

    def test_remove_contact_by_name(self, store):
        store.register_known_key(_make_pubkey(1), name="Alice")
        assert store.remove_contact("Alice") is True
        assert store.list_contacts() == []

    def test_remove_contact_by_pubkey(self, store):
        pk = _make_pubkey(1)
        store.register_known_key(pk, name="Alice")
        assert store.remove_contact(pk) is True
        assert store.list_contacts() == []

    def test_remove_nonexistent_contact(self, store):
        assert store.remove_contact("Nobody") is False
