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
        assert "Attestation created and signed" in result.output

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
        assert "Behavioral warning created and signed" in result.output


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
        # Extract attestation ID from output
        for line in result.output.splitlines():
            if "Attestation created" in line:
                att_id = line.split()[-1]
                break

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
        for line in result.output.splitlines():
            if "Attestation created" in line:
                att_id = line.split()[-1]
                break

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


class TestVersion:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.2.0" in result.output
