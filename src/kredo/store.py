"""SQLite storage for Kredo identities, attestations, revocations, and disputes.

Follows ~/vanguard/agent_memory.py patterns: WAL mode, row_factory=sqlite3.Row,
context manager, check_same_thread=False.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kredo.exceptions import KeyNotFoundError, StoreError

DEFAULT_DB_PATH = Path.home() / ".kredo" / "kredo.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    pubkey TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    private_key_encrypted BLOB,
    is_encrypted INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS known_keys (
    pubkey TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'agent',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attestations (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    attestor_pubkey TEXT NOT NULL,
    subject_pubkey TEXT NOT NULL,
    domain TEXT,
    specific_skill TEXT,
    proficiency INTEGER,
    warning_category TEXT,
    evidence_context TEXT,
    evidence_artifacts TEXT,
    evidence_outcome TEXT,
    evidence_interaction_date TEXT,
    issued TEXT NOT NULL,
    expires TEXT NOT NULL,
    signature TEXT,
    raw_json TEXT NOT NULL,
    is_revoked INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revocations (
    id TEXT PRIMARY KEY,
    attestation_id TEXT NOT NULL,
    revoker_pubkey TEXT NOT NULL,
    reason TEXT NOT NULL,
    issued TEXT NOT NULL,
    signature TEXT,
    raw_json TEXT NOT NULL,
    FOREIGN KEY (attestation_id) REFERENCES attestations(id)
);

CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,
    warning_id TEXT NOT NULL,
    disputor_pubkey TEXT NOT NULL,
    response TEXT NOT NULL,
    evidence_json TEXT,
    issued TEXT NOT NULL,
    signature TEXT,
    raw_json TEXT NOT NULL,
    FOREIGN KEY (warning_id) REFERENCES attestations(id)
);

CREATE INDEX IF NOT EXISTS idx_attestations_subject ON attestations(subject_pubkey);
CREATE INDEX IF NOT EXISTS idx_attestations_attestor ON attestations(attestor_pubkey);
CREATE INDEX IF NOT EXISTS idx_attestations_domain ON attestations(domain);
CREATE INDEX IF NOT EXISTS idx_attestations_type ON attestations(type);
CREATE INDEX IF NOT EXISTS idx_revocations_attestation ON revocations(attestation_id);
CREATE INDEX IF NOT EXISTS idx_disputes_warning ON disputes(warning_id);

CREATE TABLE IF NOT EXISTS ipfs_pins (
    cid TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    pinned_at TEXT NOT NULL,
    provider TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ipfs_pins_document ON ipfs_pins(document_id);

CREATE TABLE IF NOT EXISTS custom_domains (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_skills (
    id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (domain_id, id)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KredoStore:
    """SQLite-backed store for all Kredo data."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Identities ---

    def save_identity(
        self,
        pubkey: str,
        name: str,
        attestor_type: str,
        private_key_encrypted: Optional[bytes] = None,
        is_encrypted: bool = False,
        is_default: bool = False,
    ) -> None:
        """Save a local identity (with private key)."""
        try:
            # If setting as default, clear other defaults first
            if is_default:
                self._conn.execute(
                    "UPDATE identities SET is_default = 0 WHERE is_default = 1"
                )
            self._conn.execute(
                """INSERT OR REPLACE INTO identities
                   (pubkey, name, type, private_key_encrypted, is_encrypted, is_default, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pubkey, name, attestor_type, private_key_encrypted,
                 int(is_encrypted), int(is_default), _now_iso()),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreError(f"Failed to save identity: {e}") from e

    def get_identity(self, pubkey: str) -> dict:
        """Get an identity by pubkey. Raises KeyNotFoundError if missing."""
        row = self._conn.execute(
            "SELECT * FROM identities WHERE pubkey = ?", (pubkey,)
        ).fetchone()
        if row is None:
            raise KeyNotFoundError(f"Identity not found: {pubkey}")
        return dict(row)

    def list_identities(self) -> list[dict]:
        """List all local identities."""
        rows = self._conn.execute(
            "SELECT pubkey, name, type, is_encrypted, is_default, created_at FROM identities ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_default_identity(self) -> Optional[dict]:
        """Get the default identity, or None."""
        row = self._conn.execute(
            "SELECT * FROM identities WHERE is_default = 1"
        ).fetchone()
        return dict(row) if row else None

    def set_default_identity(self, pubkey: str) -> None:
        """Set an identity as the default."""
        # Verify it exists
        self.get_identity(pubkey)
        self._conn.execute("UPDATE identities SET is_default = 0 WHERE is_default = 1")
        self._conn.execute("UPDATE identities SET is_default = 1 WHERE pubkey = ?", (pubkey,))
        self._conn.commit()

    def get_private_key(self, pubkey: str) -> tuple[bytes, bool]:
        """Get encrypted private key bytes and whether it's encrypted."""
        row = self.get_identity(pubkey)
        if row["private_key_encrypted"] is None:
            raise KeyNotFoundError(f"No private key stored for: {pubkey}")
        return row["private_key_encrypted"], bool(row["is_encrypted"])

    # --- Known Keys ---

    def register_known_key(self, pubkey: str, name: str = "", attestor_type: str = "agent") -> None:
        """Register a known external key (no private key)."""
        now = _now_iso()
        try:
            self._conn.execute(
                """INSERT INTO known_keys (pubkey, name, type, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(pubkey) DO UPDATE SET last_seen = ?, name = CASE WHEN ? != '' THEN ? ELSE name END""",
                (pubkey, name, attestor_type, now, now, now, name, name),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreError(f"Failed to register key: {e}") from e

    def find_key_by_name(self, name: str) -> Optional[dict]:
        """Find a known key or identity by name (case-insensitive)."""
        # Check identities first
        row = self._conn.execute(
            "SELECT pubkey, name, type FROM identities WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()
        if row:
            return dict(row)
        # Then known_keys
        row = self._conn.execute(
            "SELECT pubkey, name, type FROM known_keys WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def list_contacts(self) -> list[dict]:
        """List all known keys with last seen dates."""
        rows = self._conn.execute(
            "SELECT pubkey, name, type, first_seen, last_seen FROM known_keys ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def remove_contact(self, name_or_pubkey: str) -> bool:
        """Remove a known key by name or pubkey."""
        if name_or_pubkey.startswith("ed25519:"):
            self._conn.execute(
                "DELETE FROM known_keys WHERE pubkey = ?", (name_or_pubkey,)
            )
        else:
            self._conn.execute(
                "DELETE FROM known_keys WHERE LOWER(name) = LOWER(?)",
                (name_or_pubkey,),
            )
        self._conn.commit()
        return self._conn.total_changes > 0

    # --- Attestations ---

    def save_attestation(self, attestation_json: str) -> str:
        """Save an attestation from its JSON representation. Returns the attestation ID."""
        data = json.loads(attestation_json)
        att_id = data["id"]
        try:
            skill = data.get("skill") or {}
            evidence = data.get("evidence", {})
            self._conn.execute(
                """INSERT OR REPLACE INTO attestations
                   (id, type, attestor_pubkey, subject_pubkey, domain, specific_skill,
                    proficiency, warning_category, evidence_context, evidence_artifacts,
                    evidence_outcome, evidence_interaction_date, issued, expires,
                    signature, raw_json, is_revoked, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    att_id,
                    data["type"],
                    data["attestor"]["pubkey"],
                    data["subject"]["pubkey"],
                    skill.get("domain"),
                    skill.get("specific"),
                    skill.get("proficiency"),
                    data.get("warning_category"),
                    evidence.get("context"),
                    json.dumps(evidence.get("artifacts", [])),
                    evidence.get("outcome"),
                    evidence.get("interaction_date"),
                    data["issued"],
                    data["expires"],
                    data.get("signature"),
                    attestation_json,
                    _now_iso(),
                ),
            )
            self._conn.commit()
            return att_id
        except sqlite3.Error as e:
            raise StoreError(f"Failed to save attestation: {e}") from e

    def get_attestation(self, att_id: str) -> Optional[dict]:
        """Get attestation by ID. Returns parsed JSON or None."""
        row = self._conn.execute(
            "SELECT raw_json FROM attestations WHERE id = ?", (att_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["raw_json"])

    def get_attestation_row(self, att_id: str) -> Optional[dict]:
        """Get the full attestation row (including metadata)."""
        row = self._conn.execute(
            "SELECT * FROM attestations WHERE id = ?", (att_id,)
        ).fetchone()
        return dict(row) if row else None

    def search_attestations(
        self,
        subject_pubkey: Optional[str] = None,
        attestor_pubkey: Optional[str] = None,
        domain: Optional[str] = None,
        att_type: Optional[str] = None,
        include_revoked: bool = False,
    ) -> list[dict]:
        """Search attestations by criteria. Returns parsed JSON dicts."""
        conditions = []
        params = []
        if subject_pubkey:
            conditions.append("subject_pubkey = ?")
            params.append(subject_pubkey)
        if attestor_pubkey:
            conditions.append("attestor_pubkey = ?")
            params.append(attestor_pubkey)
        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if att_type:
            conditions.append("type = ?")
            params.append(att_type)
        if not include_revoked:
            conditions.append("is_revoked = 0")

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT raw_json FROM attestations WHERE {where} ORDER BY issued DESC",
            params,
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    # --- Trust Graph ---

    def get_attestors_for(self, subject_pubkey: str) -> list[dict]:
        """Get all attestors who have attested for a subject."""
        rows = self._conn.execute(
            """SELECT DISTINCT attestor_pubkey, type,
                      COUNT(*) as attestation_count
               FROM attestations
               WHERE subject_pubkey = ? AND is_revoked = 0
               GROUP BY attestor_pubkey""",
            (subject_pubkey,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_attested_by(self, attestor_pubkey: str) -> list[dict]:
        """Get all subjects attested by a given attestor."""
        rows = self._conn.execute(
            """SELECT DISTINCT subject_pubkey,
                      COUNT(*) as attestation_count
               FROM attestations
               WHERE attestor_pubkey = ? AND is_revoked = 0
               GROUP BY subject_pubkey""",
            (attestor_pubkey,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_attestation_edges(self) -> list[tuple[str, str]]:
        """Get all (attestor, subject) directed edges for ring detection."""
        rows = self._conn.execute(
            "SELECT DISTINCT attestor_pubkey, subject_pubkey "
            "FROM attestations WHERE is_revoked = 0"
        ).fetchall()
        return [(r["attestor_pubkey"], r["subject_pubkey"]) for r in rows]

    # --- Revocations ---

    def save_revocation(self, revocation_json: str) -> str:
        """Save a revocation and mark the attestation as revoked."""
        data = json.loads(revocation_json)
        rev_id = data["id"]
        att_id = data["attestation_id"]
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO revocations
                   (id, attestation_id, revoker_pubkey, reason, issued, signature, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rev_id,
                    att_id,
                    data["revoker"]["pubkey"],
                    data["reason"],
                    data["issued"],
                    data.get("signature"),
                    revocation_json,
                ),
            )
            self._conn.execute(
                "UPDATE attestations SET is_revoked = 1 WHERE id = ?", (att_id,)
            )
            self._conn.commit()
            return rev_id
        except sqlite3.Error as e:
            raise StoreError(f"Failed to save revocation: {e}") from e

    def get_revocations_for(self, attestation_id: str) -> list[dict]:
        """Get all revocations for an attestation."""
        rows = self._conn.execute(
            "SELECT raw_json FROM revocations WHERE attestation_id = ?",
            (attestation_id,),
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    # --- Disputes ---

    def save_dispute(self, dispute_json: str) -> str:
        """Save a dispute against a behavioral warning."""
        data = json.loads(dispute_json)
        disp_id = data["id"]
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO disputes
                   (id, warning_id, disputor_pubkey, response, evidence_json, issued, signature, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    disp_id,
                    data["warning_id"],
                    data["disputor"]["pubkey"],
                    data["response"],
                    json.dumps(data.get("evidence")) if data.get("evidence") else None,
                    data["issued"],
                    data.get("signature"),
                    dispute_json,
                ),
            )
            self._conn.commit()
            return disp_id
        except sqlite3.Error as e:
            raise StoreError(f"Failed to save dispute: {e}") from e

    def get_disputes_for(self, warning_id: str) -> list[dict]:
        """Get all disputes for a behavioral warning."""
        rows = self._conn.execute(
            "SELECT raw_json FROM disputes WHERE warning_id = ?", (warning_id,)
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def get_revocation(self, rev_id: str) -> Optional[dict]:
        """Get a revocation by ID. Returns parsed JSON or None."""
        row = self._conn.execute(
            "SELECT raw_json FROM revocations WHERE id = ?", (rev_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["raw_json"])

    def get_dispute(self, disp_id: str) -> Optional[dict]:
        """Get a dispute by ID. Returns parsed JSON or None."""
        row = self._conn.execute(
            "SELECT raw_json FROM disputes WHERE id = ?", (disp_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["raw_json"])

    # --- IPFS Pins ---

    def save_ipfs_pin(self, cid: str, document_id: str, document_type: str, provider: str) -> None:
        """Record an IPFS pin for a document."""
        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO ipfs_pins
                   (cid, document_id, document_type, pinned_at, provider)
                   VALUES (?, ?, ?, ?, ?)""",
                (cid, document_id, document_type, _now_iso(), provider),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreError(f"Failed to save IPFS pin: {e}") from e

    def get_ipfs_cid(self, document_id: str) -> Optional[str]:
        """Get the CID for a document, or None if not pinned."""
        row = self._conn.execute(
            "SELECT cid FROM ipfs_pins WHERE document_id = ?", (document_id,)
        ).fetchone()
        return row["cid"] if row else None

    def get_ipfs_pin(self, cid: str) -> Optional[dict]:
        """Get pin metadata by CID."""
        row = self._conn.execute(
            "SELECT * FROM ipfs_pins WHERE cid = ?", (cid,)
        ).fetchone()
        return dict(row) if row else None

    def list_ipfs_pins(self) -> list[dict]:
        """List all IPFS pins."""
        rows = self._conn.execute(
            "SELECT * FROM ipfs_pins ORDER BY pinned_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Custom Taxonomy ---

    def create_custom_domain(self, domain_id: str, label: str, creator_pubkey: str) -> None:
        """Create a custom taxonomy domain. Rejects if id exists in bundled or custom."""
        from kredo.taxonomy import get_domains as _get_bundled_domains
        bundled = _get_bundled_domains(bundled_only=True)
        if domain_id in bundled:
            raise StoreError(f"Domain '{domain_id}' already exists in the bundled taxonomy")
        try:
            self._conn.execute(
                "INSERT INTO custom_domains (id, label, created_by, created_at) VALUES (?, ?, ?, ?)",
                (domain_id, label, creator_pubkey, _now_iso()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise StoreError(f"Domain '{domain_id}' already exists")
        except sqlite3.Error as e:
            raise StoreError(f"Failed to create domain: {e}") from e

    def create_custom_skill(self, domain_id: str, skill_id: str, creator_pubkey: str) -> None:
        """Create a custom skill under a domain. Domain must exist (bundled or custom)."""
        from kredo.taxonomy import get_domains as _get_domains, is_valid_skill as _is_valid
        all_domains = _get_domains()
        if domain_id not in all_domains:
            raise StoreError(f"Domain '{domain_id}' does not exist")
        if _is_valid(domain_id, skill_id):
            raise StoreError(f"Skill '{skill_id}' already exists in domain '{domain_id}'")
        try:
            self._conn.execute(
                "INSERT INTO custom_skills (id, domain_id, created_by, created_at) VALUES (?, ?, ?, ?)",
                (skill_id, domain_id, creator_pubkey, _now_iso()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise StoreError(f"Skill '{skill_id}' already exists in domain '{domain_id}'")
        except sqlite3.Error as e:
            raise StoreError(f"Failed to create skill: {e}") from e

    def list_custom_domains(self) -> list[dict]:
        """List all custom domains."""
        rows = self._conn.execute(
            "SELECT id, label, created_by, created_at FROM custom_domains ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_custom_skills(self, domain_id: str) -> list[dict]:
        """List custom skills for a domain."""
        rows = self._conn.execute(
            "SELECT id, domain_id, created_by, created_at FROM custom_skills WHERE domain_id = ? ORDER BY id",
            (domain_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_custom_domain(self, domain_id: str, requester_pubkey: str) -> None:
        """Delete a custom domain (creator only). Cascades to skills."""
        row = self._conn.execute(
            "SELECT created_by FROM custom_domains WHERE id = ?", (domain_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"Custom domain '{domain_id}' not found")
        if row["created_by"] != requester_pubkey:
            raise StoreError("Only the creator can delete this domain")
        self._conn.execute("DELETE FROM custom_skills WHERE domain_id = ?", (domain_id,))
        self._conn.execute("DELETE FROM custom_domains WHERE id = ?", (domain_id,))
        self._conn.commit()

    def delete_custom_skill(self, domain_id: str, skill_id: str, requester_pubkey: str) -> None:
        """Delete a custom skill (creator only)."""
        row = self._conn.execute(
            "SELECT created_by FROM custom_skills WHERE domain_id = ? AND id = ?",
            (domain_id, skill_id),
        ).fetchone()
        if row is None:
            raise StoreError(f"Custom skill '{skill_id}' not found in domain '{domain_id}'")
        if row["created_by"] != requester_pubkey:
            raise StoreError("Only the creator can delete this skill")
        self._conn.execute(
            "DELETE FROM custom_skills WHERE domain_id = ? AND id = ?",
            (domain_id, skill_id),
        )
        self._conn.commit()

    def is_custom_domain(self, domain_id: str) -> bool:
        """Check if a domain exists as a custom domain."""
        row = self._conn.execute(
            "SELECT 1 FROM custom_domains WHERE id = ?", (domain_id,)
        ).fetchone()
        return row is not None

    def is_custom_skill(self, domain_id: str, skill_id: str) -> bool:
        """Check if a skill exists as a custom skill."""
        row = self._conn.execute(
            "SELECT 1 FROM custom_skills WHERE domain_id = ? AND id = ?",
            (domain_id, skill_id),
        ).fetchone()
        return row is not None

    # --- Import/Export ---

    def export_attestation_json(self, att_id: str) -> Optional[str]:
        """Export a single attestation as formatted JSON."""
        row = self._conn.execute(
            "SELECT raw_json FROM attestations WHERE id = ?", (att_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["raw_json"])
        return json.dumps(data, indent=2, sort_keys=True)

    def import_attestation_json(self, json_str: str) -> str:
        """Import an attestation from JSON string. Returns attestation ID."""
        # Validate it parses
        data = json.loads(json_str)
        if "id" not in data or "type" not in data:
            raise StoreError("Invalid attestation JSON: missing id or type")
        return self.save_attestation(json_str)
