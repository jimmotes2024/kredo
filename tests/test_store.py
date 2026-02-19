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
    def _make_attestation_json(
        self,
        attestor_n=1,
        subject_n=2,
        domain="reasoning",
        specific="planning",
        proficiency=Proficiency.PROFICIENT,
    ):
        now = datetime.now(timezone.utc)
        att = Attestation(
            type=AttestationType.SKILL,
            subject=Subject(pubkey=_make_pubkey(subject_n), name="Subject"),
            attestor=Attestor(pubkey=_make_pubkey(attestor_n), name="Attestor", type=AttestorType.AGENT),
            skill=Skill(domain=domain, specific=specific, proficiency=proficiency),
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

    def test_search_by_skill_and_min_proficiency(self, store):
        low_json, _ = self._make_attestation_json(
            domain="security-operations",
            specific="incident-triage",
            proficiency=Proficiency.NOVICE,
        )
        mid_json, _ = self._make_attestation_json(
            domain="security-operations",
            specific="incident-triage",
            proficiency=Proficiency.COMPETENT,
        )
        high_json, _ = self._make_attestation_json(
            domain="security-operations",
            specific="threat-hunting",
            proficiency=Proficiency.EXPERT,
        )
        store.save_attestation(low_json)
        store.save_attestation(mid_json)
        store.save_attestation(high_json)

        triage_only = store.search_attestations(skill="incident-triage")
        assert len(triage_only) == 2

        high_proficiency = store.search_attestations(min_proficiency=4)
        assert len(high_proficiency) == 1
        assert high_proficiency[0]["skill"]["specific"] == "threat-hunting"

    def test_search_with_limit_offset_and_filtered_count(self, store):
        for prof in (
            Proficiency.NOVICE,
            Proficiency.COMPETENT,
            Proficiency.PROFICIENT,
            Proficiency.EXPERT,
        ):
            json_str, _ = self._make_attestation_json(
                domain="security-operations",
                specific="incident-triage",
                proficiency=prof,
            )
            store.save_attestation(json_str)

        first_page = store.search_attestations(
            skill="incident-triage",
            limit=2,
            offset=0,
        )
        second_page = store.search_attestations(
            skill="incident-triage",
            limit=2,
            offset=2,
        )
        assert len(first_page) == 2
        assert len(second_page) == 2

        total = store.count_attestations_filtered(skill="incident-triage")
        assert total == 4


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


class TestIntegrityStorage:
    def test_set_and_get_active_integrity_baseline(self, store):
        baseline_id = "baseline-store-01"
        agent_pubkey = _make_pubkey(51)
        owner_pubkey = _make_pubkey(61)
        manifest = json.dumps(
            {"file_hashes": [{"path": "agent.py", "sha256": "a" * 64}]},
            sort_keys=True,
        )

        store.set_integrity_baseline(
            baseline_id=baseline_id,
            agent_pubkey=agent_pubkey,
            owner_pubkey=owner_pubkey,
            manifest_json=manifest,
            signature="ed25519:" + "b" * 128,
        )

        active = store.get_active_integrity_baseline(agent_pubkey)
        assert active is not None
        assert active["id"] == baseline_id
        assert active["agent_pubkey"] == agent_pubkey
        assert active["owner_pubkey"] == owner_pubkey
        assert active["is_active"] == 1

    def test_new_baseline_marks_previous_inactive(self, store):
        agent_pubkey = _make_pubkey(52)
        owner_pubkey = _make_pubkey(62)
        manifest_v1 = json.dumps(
            {"file_hashes": [{"path": "agent.py", "sha256": "1" * 64}]},
            sort_keys=True,
        )
        manifest_v2 = json.dumps(
            {"file_hashes": [{"path": "agent.py", "sha256": "2" * 64}]},
            sort_keys=True,
        )

        store.set_integrity_baseline(
            baseline_id="baseline-store-02",
            agent_pubkey=agent_pubkey,
            owner_pubkey=owner_pubkey,
            manifest_json=manifest_v1,
            signature="ed25519:" + "c" * 128,
        )
        store.set_integrity_baseline(
            baseline_id="baseline-store-03",
            agent_pubkey=agent_pubkey,
            owner_pubkey=owner_pubkey,
            manifest_json=manifest_v2,
            signature="ed25519:" + "d" * 128,
        )

        active = store.get_active_integrity_baseline(agent_pubkey)
        assert active is not None
        assert active["id"] == "baseline-store-03"

        baselines = store.list_integrity_baselines(agent_pubkey)
        assert len(baselines) == 2
        baseline_by_id = {row["id"]: row for row in baselines}
        assert baseline_by_id["baseline-store-02"]["is_active"] == 0
        assert baseline_by_id["baseline-store-03"]["is_active"] == 1

    def test_save_integrity_check_and_get_latest(self, store):
        agent_pubkey = _make_pubkey(53)
        owner_pubkey = _make_pubkey(63)
        baseline_id = "baseline-store-04"

        store.set_integrity_baseline(
            baseline_id=baseline_id,
            agent_pubkey=agent_pubkey,
            owner_pubkey=owner_pubkey,
            manifest_json=json.dumps(
                {"file_hashes": [{"path": "agent.py", "sha256": "3" * 64}]},
                sort_keys=True,
            ),
            signature="ed25519:" + "a" * 128,
        )

        first_id = store.save_integrity_check(
            agent_pubkey=agent_pubkey,
            status="green",
            baseline_id=baseline_id,
            diff_json=json.dumps({"changed_paths": []}),
            measured_by_pubkey=agent_pubkey,
            signature="ed25519:" + "e" * 128,
            signature_valid=True,
            raw_manifest_json=json.dumps(
                {"file_hashes": [{"path": "agent.py", "sha256": "3" * 64}]},
                sort_keys=True,
            ),
        )
        second_id = store.save_integrity_check(
            agent_pubkey=agent_pubkey,
            status="yellow",
            baseline_id=baseline_id,
            diff_json=json.dumps({"changed_paths": ["agent.py"]}),
            measured_by_pubkey=agent_pubkey,
            signature="ed25519:" + "f" * 128,
            signature_valid=True,
            raw_manifest_json=json.dumps(
                {"file_hashes": [{"path": "agent.py", "sha256": "4" * 64}]},
                sort_keys=True,
            ),
        )

        assert second_id > first_id
        latest = store.get_latest_integrity_check(agent_pubkey)
        assert latest is not None
        assert latest["id"] == second_id
        assert latest["status"] == "yellow"
        assert latest["baseline_id"] == baseline_id
        assert latest["signature_valid"] == 1

    def test_list_contacts(self, store):
        store.register_known_key(_make_pubkey(1), name="Alice")
        store.register_known_key(_make_pubkey(2), name="Bob")
        contacts = store.list_contacts()
        assert len(contacts) == 2
        names = {c["name"] for c in contacts}
        assert names == {"Alice", "Bob"}

    def test_public_known_key_queries(self, store):
        pk1 = _make_pubkey(1)
        pk2 = _make_pubkey(2)
        store.register_known_key(pk1, name="Alice", attestor_type="agent")
        store.register_known_key(pk2, name="Bob", attestor_type="human")

        assert store.count_known_keys() == 2

        row = store.get_known_key(pk1)
        assert row is not None
        assert row["name"] == "Alice"
        assert row["type"] == "agent"

        listed = store.list_known_keys(limit=10, offset=0)
        assert len(listed) == 2
        listed_pubkeys = {item["pubkey"] for item in listed}
        assert listed_pubkeys == {pk1, pk2}

    def test_register_known_key_conflict_does_not_overwrite_metadata(self, store):
        pk = _make_pubkey(1)
        store.register_known_key(pk, name="Alice", attestor_type="human")
        store.register_known_key(pk, name="Mallory", attestor_type="agent")

        result = store.find_key_by_name("Alice")
        assert result is not None
        assert result["pubkey"] == pk
        assert result["type"] == "human"

        assert store.find_key_by_name("Mallory") is None

    def test_update_known_key_identity(self, store):
        pk = _make_pubkey(1)
        store.register_known_key(pk, name="Alice", attestor_type="agent")
        store.update_known_key_identity(pk, "Alice Smith", "human")

        row = store.find_key_by_name("Alice Smith")
        assert row is not None
        assert row["pubkey"] == pk
        assert row["type"] == "human"

    def test_update_known_key_identity_missing_raises(self, store):
        with pytest.raises(KeyNotFoundError):
            store.update_known_key_identity(_make_pubkey(999), "Nobody", "agent")

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


class TestOwnershipAndAudit:
    def test_ownership_claim_confirm_and_active_owner(self, store):
        agent = _make_pubkey(101)
        human = _make_pubkey(202)
        claim_id = "own-claim-test-1"

        store.register_known_key(agent, name="Agent1", attestor_type="agent")
        store.register_known_key(human, name="Human1", attestor_type="human")

        store.create_ownership_claim(
            claim_id=claim_id,
            agent_pubkey=agent,
            human_pubkey=human,
            agent_signature="ed25519:" + "a" * 128,
            claim_payload_json='{"action":"ownership_claim"}',
        )

        pending = store.get_ownership_claim(claim_id)
        assert pending is not None
        assert pending["status"] == "pending"

        store.confirm_ownership_claim(
            claim_id=claim_id,
            human_signature="ed25519:" + "b" * 128,
            confirm_payload_json='{"action":"ownership_confirm"}',
        )

        active = store.get_active_owner(agent)
        assert active is not None
        assert active["id"] == claim_id
        assert active["status"] == "active"
        assert active["human_pubkey"] == human

    def test_ownership_revoke(self, store):
        agent = _make_pubkey(303)
        human = _make_pubkey(404)
        claim_id = "own-claim-test-2"

        store.create_ownership_claim(
            claim_id=claim_id,
            agent_pubkey=agent,
            human_pubkey=human,
            agent_signature="ed25519:" + "c" * 128,
            claim_payload_json='{"action":"ownership_claim"}',
        )
        store.confirm_ownership_claim(
            claim_id=claim_id,
            human_signature="ed25519:" + "d" * 128,
            confirm_payload_json='{"action":"ownership_confirm"}',
        )

        store.revoke_ownership_claim(
            claim_id=claim_id,
            revoked_by=human,
            reason="ownership transferred",
        )
        row = store.get_ownership_claim(claim_id)
        assert row is not None
        assert row["status"] == "revoked"
        assert row["revoked_by"] == human

    def test_source_anomaly_signals(self, store):
        for idx in range(4):
            store.append_audit_event(
                action="registration.create",
                outcome="accepted",
                actor_pubkey=_make_pubkey(idx + 1),
                source_ip="203.0.113.9",
                user_agent="pytest",
                details={"idx": idx},
            )

        signals = store.get_source_anomaly_signals(
            hours=24,
            min_events=3,
            min_unique_actors=3,
            limit=10,
        )
        assert len(signals) == 1
        assert signals[0]["event_count"] >= 4
        assert signals[0]["unique_actor_count"] >= 4
        assert signals[0]["registration_count"] >= 4
