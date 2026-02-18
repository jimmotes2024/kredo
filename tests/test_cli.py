"""Tests for kredo.cli — Typer CLI commands via CliRunner."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kredo.cli import app
from kredo.identity import generate_keypair
from kredo.models import AttestorType
from kredo.store import KredoStore

runner = CliRunner()


@pytest.fixture
def cli_db(tmp_path):
    """Return a temp db path string for CLI --db flag."""
    return str(tmp_path / "cli_test.db")


@pytest.fixture
def cli_store(cli_db):
    """Store pre-initialized for CLI tests."""
    return KredoStore(db_path=Path(cli_db))


@pytest.fixture
def cli_identity(cli_store, cli_db):
    """Create an identity and return (pubkey, db_path)."""
    identity = generate_keypair("test_agent", AttestorType.AGENT, cli_store)
    return identity.pubkey, cli_db


class TestIdentityCommands:
    def test_create(self, cli_db):
        result = runner.invoke(app, [
            "identity", "create",
            "--name", "myagent",
            "--type", "agent",
            "--db", cli_db,
        ])
        assert result.exit_code == 0
        assert "Identity created" in result.output
        assert "ed25519:" in result.output

    def test_list(self, cli_identity):
        pubkey, db = cli_identity
        result = runner.invoke(app, ["identity", "list", "--db", db])
        assert result.exit_code == 0
        assert "test_agent" in result.output

    def test_export(self, cli_identity):
        pubkey, db = cli_identity
        result = runner.invoke(app, ["identity", "export", pubkey, "--db", db])
        assert result.exit_code == 0
        # Should output 64 hex chars
        assert len(result.output.strip()) == 64

    def test_set_default(self, cli_store, cli_db):
        id1 = generate_keypair("first", AttestorType.AGENT, cli_store)
        id2 = generate_keypair("second", AttestorType.AGENT, cli_store)
        result = runner.invoke(app, [
            "identity", "set-default", id2.pubkey, "--db", cli_db,
        ])
        assert result.exit_code == 0
        assert "Default identity set" in result.output


class TestAttestCommands:
    def test_attest_skill(self, cli_identity, tmp_path):
        pubkey, db = cli_identity
        # Create a subject key
        subject_key = "ed25519:" + "a" * 64
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", subject_key,
            "--domain", "security-operations",
            "--skill", "incident-triage",
            "--proficiency", "4",
            "--context", "Worked together on incident response",
            "--artifacts", "chain:abc123,output:report",
            "--outcome", "success",
            "--interaction-date", "2026-02-16",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Attestation Created" in result.output

    def test_warn(self, cli_identity):
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "b" * 64
        result = runner.invoke(app, [
            "warn",
            "--subject", subject_key,
            "--category", "spam",
            "--context", "A" * 150,
            "--artifacts", "log:spam-evidence-001",
            "--outcome", "confirmed",
            "--interaction-date", "2026-02-16",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Behavioral Warning Created" in result.output


class TestVerifyCommand:
    def test_verify_exported(self, cli_identity, tmp_path):
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "c" * 64

        # Create an attestation
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", subject_key,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Planned well",
            "--db", db,
        ])
        assert result.exit_code == 0
        # Extract attestation ID from Rich panel output
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break
        assert att_id is not None

        # Export it
        out_file = tmp_path / "test_att.json"
        result = runner.invoke(app, [
            "export", att_id,
            "--output", str(out_file),
            "--db", db,
        ])
        assert result.exit_code == 0

        # Verify it
        result = runner.invoke(app, ["verify", str(out_file)])
        assert result.exit_code == 0
        assert "signature valid" in result.output


class TestExportImport:
    def test_export_import_roundtrip(self, cli_identity, tmp_path):
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "d" * 64

        # Create
        result = runner.invoke(app, [
            "attest", "intellectual",
            "--subject", subject_key,
            "--domain", "reasoning",
            "--skill", "conceptual-analysis",
            "--proficiency", "5",
            "--context", "Brilliant analysis",
            "--db", db,
        ])
        assert result.exit_code == 0
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break
        assert att_id is not None

        # Export
        out_file = tmp_path / "export.json"
        result = runner.invoke(app, [
            "export", att_id, "-o", str(out_file), "--db", db,
        ])
        assert result.exit_code == 0
        assert out_file.exists()

        # Import into new db
        db2 = str(tmp_path / "import_test.db")
        result = runner.invoke(app, [
            "import", str(out_file), "--db", db2,
        ])
        assert result.exit_code == 0
        assert "Imported attestation" in result.output


class TestTaxonomyCommands:
    def test_domains(self):
        result = runner.invoke(app, ["taxonomy", "domains"])
        assert result.exit_code == 0
        assert "security-operations" in result.output
        assert "reasoning" in result.output

    def test_skills(self):
        result = runner.invoke(app, ["taxonomy", "skills", "security-operations"])
        assert result.exit_code == 0
        assert "incident-triage" in result.output


class TestTrustCommands:
    def test_who_attested_empty(self, cli_db):
        result = runner.invoke(app, [
            "trust", "who-attested", "ed25519:" + "f" * 64,
            "--db", cli_db,
        ])
        assert result.exit_code == 0
        assert "No attestations" in result.output


class TestInitCommand:
    def test_init_creates_identity(self, cli_db):
        """Init should create an identity via interactive prompts."""
        result = runner.invoke(app, ["init", "--db", cli_db], input="Jim\n1\nn\nn\n")
        assert result.exit_code == 0
        assert "Identity created" in result.output
        assert "Jim" in result.output
        assert "ed25519:" in result.output
        assert "You're ready" in result.output

    def test_init_agent_type(self, cli_db):
        """Init should accept agent type selection."""
        result = runner.invoke(app, ["init", "--db", cli_db], input="MyBot\n2\nn\n")
        assert result.exit_code == 0
        assert "Identity created" in result.output
        assert "MyBot" in result.output

    def test_init_existing_identity_declines(self, cli_identity):
        """Init with existing identity should ask before creating another."""
        _, db = cli_identity
        result = runner.invoke(app, ["init", "--db", db], input="n\n")
        assert result.exit_code == 0
        assert "already have" in result.output

    def test_init_existing_identity_creates_second(self, cli_identity):
        """Init with existing identity can create a second if user confirms."""
        _, db = cli_identity
        result = runner.invoke(app, ["init", "--db", db], input="y\nSecond\n2\nn\n")
        assert result.exit_code == 0
        assert "Identity created" in result.output
        assert "Second" in result.output

    def test_init_shows_welcome_panel(self, cli_db):
        """Init should show the welcome panel."""
        result = runner.invoke(app, ["init", "--db", cli_db], input="Test\n1\nn\nn\n")
        assert result.exit_code == 0
        assert "Welcome to Kredo" in result.output


class TestMeCommand:
    def test_me_no_identity(self, cli_db):
        """Me with no identity should suggest kredo init."""
        result = runner.invoke(app, ["me", "--db", cli_db])
        assert result.exit_code == 0
        assert "No identity found" in result.output
        assert "kredo init" in result.output

    def test_me_shows_identity(self, cli_identity):
        """Me should show the current identity."""
        pubkey, db = cli_identity
        result = runner.invoke(app, ["me", "--db", db])
        assert result.exit_code == 0
        assert "test_agent" in result.output
        assert "Your Identity" in result.output
        assert "Local Stats" in result.output

    def test_me_shows_attestation_counts(self, cli_identity):
        """Me should show attestation counts (even if zero)."""
        pubkey, db = cli_identity
        result = runner.invoke(app, ["me", "--db", db])
        assert result.exit_code == 0
        assert "Attestations given" in result.output
        assert "Attestations received" in result.output

    def test_me_shows_network_offline_gracefully(self, cli_identity):
        """Me should handle network failure gracefully."""
        _, db = cli_identity
        # With default API URL, this might fail if offline — should not crash
        result = runner.invoke(app, ["me", "--db", db])
        assert result.exit_code == 0
        # Should show either network profile or offline message
        assert "Your Identity" in result.output

    def test_me_with_attestations(self, cli_identity):
        """Me should count local attestations correctly."""
        pubkey, db = cli_identity
        # Create an attestation so the count is non-zero
        subject_key = "ed25519:" + "a" * 64
        runner.invoke(app, [
            "attest", "skill",
            "--subject", subject_key,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Good planning skills",
            "--db", db,
        ])
        result = runner.invoke(app, ["me", "--db", db])
        assert result.exit_code == 0
        assert "Attestations given:    1" in result.output


class TestInteractiveAttest:
    def test_interactive_full_flow(self, cli_identity):
        """Interactive mode should walk through all steps and create attestation."""
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "a" * 64
        # Inputs: type=1(skill), subject=pubkey, domain=1(security-ops), skill=1(incident-triage),
        #         proficiency=4, evidence text, artifacts (skip), outcome (skip), confirm=y, submit=n
        inputs = f"1\n{subject_key}\n1\n1\n4\nCollaborated on incident response\n\n\ny\nn\n"
        result = runner.invoke(app, ["attest", "-i", "--db", db], input=inputs)
        assert result.exit_code == 0
        assert "Attestation Created" in result.output
        assert "security-operations" in result.output.lower() or "Security Operations" in result.output

    def test_interactive_with_number_subject(self, cli_identity):
        """Interactive mode should allow selecting subject by number from contacts."""
        pubkey, db = cli_identity
        # Add a known contact first
        store = KredoStore(db_path=Path(db))
        contact_pk = "ed25519:" + "b" * 64
        store.register_known_key(contact_pk, name="TestBot")
        store.close()
        # Select subject by number (should be #2 since our identity is #1)
        inputs = "1\n2\n1\n1\n3\nGood work on log analysis\n\n\ny\nn\n"
        result = runner.invoke(app, ["attest", "-i", "--db", db], input=inputs)
        assert result.exit_code == 0
        assert "Attestation Created" in result.output

    def test_interactive_cancel(self, cli_identity):
        """Interactive mode should allow cancelling before signing."""
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "a" * 64
        # Answer all prompts but decline confirmation
        inputs = f"1\n{subject_key}\n1\n1\n3\nTest evidence\n\n\nn\n"
        result = runner.invoke(app, ["attest", "-i", "--db", db], input=inputs)
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        assert "Attestation Created" not in result.output

    def test_interactive_community_type(self, cli_identity):
        """Interactive mode should support community contribution type."""
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "c" * 64
        # Type 3 = community
        inputs = f"3\n{subject_key}\n6\n6\n4\nExcellent mentoring\n\n\ny\nn\n"
        result = runner.invoke(app, ["attest", "-i", "--db", db], input=inputs)
        assert result.exit_code == 0
        assert "Attestation Created" in result.output

    def test_flag_mode_still_works(self, cli_identity):
        """Flag-based mode should work exactly as before."""
        pubkey, db = cli_identity
        subject_key = "ed25519:" + "d" * 64
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", subject_key,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Good planning",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Attestation Created" in result.output

    def test_missing_type_suggests_interactive(self, cli_db):
        """Missing type without -i should suggest interactive mode."""
        result = runner.invoke(app, ["attest", "--db", cli_db])
        assert result.exit_code == 1
        assert "interactive" in result.output.lower()

    def test_flag_mode_name_resolution(self, cli_identity):
        """Flag-based mode should resolve contact names to pubkeys."""
        pubkey, db = cli_identity
        # Add a known contact
        store = KredoStore(db_path=Path(db))
        contact_pk = "ed25519:" + "e" * 64
        store.register_known_key(contact_pk, name="Alice")
        store.close()

        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "Alice",
            "--domain", "collaboration",
            "--skill", "communication-clarity",
            "--proficiency", "4",
            "--context", "Clear communication throughout",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Attestation Created" in result.output


class TestContactsCommands:
    def test_add_contact(self, cli_db):
        """contacts add should register a known key."""
        result = runner.invoke(app, [
            "contacts", "add",
            "--name", "TestBot",
            "--pubkey", "ed25519:" + "a" * 64,
            "--db", cli_db,
        ])
        assert result.exit_code == 0
        assert "Contact added" in result.output
        assert "TestBot" in result.output

    def test_add_contact_bad_pubkey(self, cli_db):
        """contacts add should reject invalid pubkey format."""
        result = runner.invoke(app, [
            "contacts", "add",
            "--name", "Bad",
            "--pubkey", "not_a_key",
            "--db", cli_db,
        ])
        assert result.exit_code == 1
        assert "ed25519:" in result.output

    def test_add_contact_bad_type(self, cli_db):
        """contacts add should reject invalid type."""
        result = runner.invoke(app, [
            "contacts", "add",
            "--name", "Bad",
            "--pubkey", "ed25519:" + "a" * 64,
            "--type", "robot",
            "--db", cli_db,
        ])
        assert result.exit_code == 1

    def test_list_contacts_empty(self, cli_db):
        """contacts list with no contacts should show help text."""
        result = runner.invoke(app, ["contacts", "list", "--db", cli_db])
        assert result.exit_code == 0
        assert "No contacts" in result.output

    def test_list_contacts_with_identity(self, cli_identity):
        """contacts list should include local identities."""
        _, db = cli_identity
        result = runner.invoke(app, ["contacts", "list", "--db", db])
        assert result.exit_code == 0
        assert "test_agent" in result.output
        assert "(you)" in result.output

    def test_list_contacts_with_contacts(self, cli_identity):
        """contacts list should show added contacts."""
        _, db = cli_identity
        runner.invoke(app, [
            "contacts", "add",
            "--name", "AliceBot",
            "--pubkey", "ed25519:" + "b" * 64,
            "--db", db,
        ])
        result = runner.invoke(app, ["contacts", "list", "--db", db])
        assert result.exit_code == 0
        assert "AliceBot" in result.output

    def test_remove_contact_by_name(self, cli_db):
        """contacts remove should remove by name."""
        runner.invoke(app, [
            "contacts", "add",
            "--name", "ToRemove",
            "--pubkey", "ed25519:" + "c" * 64,
            "--db", cli_db,
        ])
        result = runner.invoke(app, ["contacts", "remove", "ToRemove", "--db", cli_db])
        assert result.exit_code == 0
        assert "Contact removed" in result.output

    def test_remove_contact_not_found(self, cli_db):
        """contacts remove with unknown name should show not found."""
        result = runner.invoke(app, ["contacts", "remove", "Nobody", "--db", cli_db])
        assert result.exit_code == 0
        assert "not found" in result.output


class TestWarnNameResolution:
    def test_warn_resolves_name(self, cli_identity):
        """warn --subject should resolve contact names."""
        pubkey, db = cli_identity
        store = KredoStore(db_path=Path(db))
        contact_pk = "ed25519:" + "f" * 64
        store.register_known_key(contact_pk, name="BadBot")
        store.close()
        result = runner.invoke(app, [
            "warn",
            "--subject", "BadBot",
            "--category", "spam",
            "--context", "A" * 150,
            "--artifacts", "log:evidence-001",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Behavioral Warning Created" in result.output


class TestOutputFormatting:
    def test_attest_shows_evidence_dimensions(self, cli_identity):
        """Flag-mode attest should show 4-dimension evidence breakdown."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "ed25519:" + "a" * 64,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Demonstrated strong planning during project execution",
            "--artifacts", "chain:run123,output:report.pdf",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Specificity" in result.output
        assert "Verifiability" in result.output
        assert "Relevance" in result.output
        assert "Recency" in result.output

    def test_attest_shows_proficiency_bar(self, cli_identity):
        """Flag-mode attest should show visual proficiency bar."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "ed25519:" + "a" * 64,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "4",
            "--context", "Good work",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Expert" in result.output
        assert "████░" in result.output

    def test_warn_shows_panel(self, cli_identity):
        """Warn output should be a Rich panel."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "warn",
            "--subject", "ed25519:" + "b" * 64,
            "--category", "spam",
            "--context", "A" * 150,
            "--artifacts", "log:evidence",
            "--db", db,
        ])
        assert result.exit_code == 0
        assert "Behavioral Warning Created" in result.output
        assert "Evidence:" in result.output


class TestHumanExport:
    def test_export_human_format(self, cli_identity, tmp_path):
        """Export --format human should render a readable attestation card."""
        pubkey, db = cli_identity
        # Create an attestation
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "ed25519:" + "a" * 64,
            "--domain", "security-operations",
            "--skill", "incident-triage",
            "--proficiency", "4",
            "--context", "Collaborated on the February incident chain analysis",
            "--artifacts", "chain:abc123,output:report",
            "--outcome", "successful_resolution",
            "--db", db,
        ])
        assert result.exit_code == 0
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break
        assert att_id is not None

        # Export in human format
        result = runner.invoke(app, ["export", att_id, "--format", "human", "--db", db])
        assert result.exit_code == 0
        assert "SKILL ATTESTATION" in result.output
        assert "EXPERT" in result.output
        assert "Security Operations" in result.output
        assert "incident-triage" in result.output
        assert "Issued:" in result.output
        assert "Signature:" in result.output

    def test_export_markdown_format(self, cli_identity, tmp_path):
        """Export --format markdown should render shareable Markdown."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "ed25519:" + "b" * 64,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Good planning work on the project",
            "--db", db,
        ])
        assert result.exit_code == 0
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break
        assert att_id is not None

        result = runner.invoke(app, ["export", att_id, "--format", "markdown", "--db", db])
        assert result.exit_code == 0
        assert "## Kredo Skill Attestation" in result.output
        assert "**Proficient**" in result.output
        assert "### Evidence" in result.output

    def test_export_human_to_file(self, cli_identity, tmp_path):
        """Export --format human -o should write to file."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "ed25519:" + "c" * 64,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Good work",
            "--db", db,
        ])
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break

        out_file = tmp_path / "attestation.txt"
        result = runner.invoke(app, [
            "export", att_id, "--format", "human", "-o", str(out_file), "--db", db,
        ])
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "SKILL ATTESTATION" in content

    def test_export_json_still_default(self, cli_identity):
        """Export without --format should still output JSON."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "attest", "skill",
            "--subject", "ed25519:" + "d" * 64,
            "--domain", "reasoning",
            "--skill", "planning",
            "--proficiency", "3",
            "--context", "Good work",
            "--db", db,
        ])
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break

        result = runner.invoke(app, ["export", att_id, "--db", db])
        assert result.exit_code == 0
        # Should be valid JSON
        data = json.loads(result.output)
        assert data["id"] == att_id

    def test_export_invalid_format(self, cli_db):
        """Export with unknown format should fail."""
        result = runner.invoke(app, ["export", "fake-id", "--format", "xml", "--db", cli_db])
        assert result.exit_code == 1
        assert "Unknown format" in result.output

    def test_export_warning_human(self, cli_identity):
        """Export --format human should handle behavioral warnings."""
        pubkey, db = cli_identity
        result = runner.invoke(app, [
            "warn",
            "--subject", "ed25519:" + "e" * 64,
            "--category", "spam",
            "--context", "A" * 150,
            "--artifacts", "log:evidence-001",
            "--db", db,
        ])
        assert result.exit_code == 0
        att_id = None
        for line in result.output.splitlines():
            stripped = line.strip().strip("│").strip()
            if stripped.startswith("ID:"):
                att_id = stripped.split()[-1]
                break
        assert att_id is not None

        result = runner.invoke(app, ["export", att_id, "--format", "human", "--db", db])
        assert result.exit_code == 0
        assert "BEHAVIORAL WARNING" in result.output
        assert "SPAM" in result.output


class TestFriendlyErrors:
    def test_no_identity_suggests_init(self, cli_db):
        """Missing identity should suggest kredo init."""
        result = runner.invoke(app, ["me", "--db", cli_db])
        assert result.exit_code == 0
        assert "kredo init" in result.output

    def test_attest_missing_type_suggests_interactive(self, cli_db):
        """Missing attest type should suggest interactive mode."""
        result = runner.invoke(app, ["attest", "--db", cli_db])
        assert result.exit_code == 1
        assert "interactive" in result.output.lower()

    def test_taxonomy_skills_bad_domain_suggests(self):
        """Bad domain in taxonomy skills should suggest closest match."""
        result = runner.invoke(app, ["taxonomy", "skills", "security"])
        assert result.exit_code == 1
        assert "security-operations" in result.output

    def test_taxonomy_skills_no_match(self):
        """Completely unknown domain should suggest kredo taxonomy domains."""
        result = runner.invoke(app, ["taxonomy", "skills", "xyzzy"])
        assert result.exit_code == 1
        assert "kredo taxonomy domains" in result.output

    def test_contacts_add_bad_pubkey(self, cli_db):
        """contacts add with bad pubkey should explain format."""
        result = runner.invoke(app, [
            "contacts", "add",
            "--name", "Bad",
            "--pubkey", "not_a_key",
            "--db", cli_db,
        ])
        assert result.exit_code == 1
        assert "ed25519:" in result.output


class TestQuickstart:
    def test_quickstart_full_flow(self, cli_db):
        """Quickstart should walk through full tutorial and create attestation."""
        # Inputs: name="Tester", domain=1, skill=1, proficiency=4,
        #         evidence=(accept default), cleanup=n
        inputs = "Tester\n1\n1\n4\n\nn\n"
        result = runner.invoke(app, ["quickstart", "--db", cli_db], input=inputs)
        assert result.exit_code == 0
        assert "Kredo Quickstart" in result.output
        assert "Step 1" in result.output
        assert "Demo-Agent" in result.output
        assert "Attestation Created" in result.output
        assert "Signature verified" in result.output
        assert "Specificity" in result.output
        assert "What's Next" in result.output

    def test_quickstart_with_existing_identity(self, cli_identity):
        """Quickstart should reuse existing identity."""
        _, db = cli_identity
        # Inputs: domain=1, skill=1, proficiency=3, evidence=(default), cleanup=y
        inputs = "1\n1\n3\n\ny\n"
        result = runner.invoke(app, ["quickstart", "--db", db], input=inputs)
        assert result.exit_code == 0
        assert "test_agent" in result.output
        assert "Attestation Created" in result.output
        assert "cleaned up" in result.output

    def test_quickstart_cleanup(self, cli_db):
        """Quickstart cleanup should remove demo data."""
        inputs = "Cleaner\n1\n1\n3\n\ny\n"
        result = runner.invoke(app, ["quickstart", "--db", cli_db], input=inputs)
        assert result.exit_code == 0
        assert "cleaned up" in result.output

    def test_quickstart_evidence_explanation(self, cli_db):
        """Quickstart should explain all four evidence dimensions."""
        inputs = "Learner\n1\n1\n3\n\nn\n"
        result = runner.invoke(app, ["quickstart", "--db", cli_db], input=inputs)
        assert result.exit_code == 0
        assert "Specificity" in result.output
        assert "Verifiability" in result.output
        assert "Relevance" in result.output
        assert "Recency" in result.output
        assert "How detailed" in result.output
        assert "independently check" in result.output


class TestVersion:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "kredo" in result.output
