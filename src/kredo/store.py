"""SQLite storage for Kredo identities, attestations, revocations, and disputes.

Follows ~/vanguard/agent_memory.py patterns: WAL mode, row_factory=sqlite3.Row,
context manager, check_same_thread=False.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kredo.exceptions import DuplicateAttestationError, KeyNotFoundError, StoreError

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

CREATE TABLE IF NOT EXISTS ownership_links (
    id TEXT PRIMARY KEY,
    agent_pubkey TEXT NOT NULL,
    human_pubkey TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_signature TEXT NOT NULL,
    human_signature TEXT,
    claim_payload_json TEXT NOT NULL,
    confirm_payload_json TEXT,
    claimed_at TEXT NOT NULL,
    confirmed_at TEXT,
    revoked_at TEXT,
    revoked_by TEXT,
    revoke_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_ownership_agent_status ON ownership_links(agent_pubkey, status);
CREATE INDEX IF NOT EXISTS idx_ownership_human_status ON ownership_links(human_pubkey, status);

CREATE TABLE IF NOT EXISTS human_contacts (
    pubkey TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_pubkey TEXT,
    source_ip TEXT,
    source_ip_hash TEXT,
    user_agent TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events(action);
CREATE INDEX IF NOT EXISTS idx_audit_events_ip_hash ON audit_events(source_ip_hash);
CREATE INDEX IF NOT EXISTS idx_audit_events_actor ON audit_events(actor_pubkey);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:24]


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
        """Register a known external key (no private key).

        On conflict, only refreshes ``last_seen``. Unsigned registration must not
        overwrite identity metadata.
        """
        now = _now_iso()
        try:
            self._conn.execute(
                """INSERT INTO known_keys (pubkey, name, type, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(pubkey) DO UPDATE SET last_seen = excluded.last_seen""",
                (pubkey, name, attestor_type, now, now),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreError(f"Failed to register key: {e}") from e

    def get_known_key(self, pubkey: str) -> Optional[dict]:
        """Get one known key by pubkey."""
        row = self._conn.execute(
            "SELECT pubkey, name, type, first_seen, last_seen FROM known_keys WHERE pubkey = ?",
            (pubkey,),
        ).fetchone()
        return dict(row) if row else None

    def list_known_keys(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List known keys in registration order (newest first)."""
        rows = self._conn.execute(
            "SELECT pubkey, name, type, first_seen, last_seen "
            "FROM known_keys ORDER BY first_seen DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_known_keys(self) -> int:
        """Count total registered known keys."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM known_keys",
        ).fetchone()
        return int(row["cnt"]) if row else 0

    def update_known_key_identity(self, pubkey: str, name: str, attestor_type: str) -> None:
        """Update known-key metadata after signature verification."""
        try:
            cursor = self._conn.execute(
                """UPDATE known_keys
                   SET name = ?, type = ?, last_seen = ?
                   WHERE pubkey = ?""",
                (name, attestor_type, _now_iso(), pubkey),
            )
            if cursor.rowcount == 0:
                raise KeyNotFoundError(f"Known key not found: {pubkey}")
            self._conn.commit()
        except KeyNotFoundError:
            raise
        except sqlite3.Error as e:
            raise StoreError(f"Failed to update key: {e}") from e

    # --- Ownership / Accountability ---

    def create_ownership_claim(
        self,
        claim_id: str,
        agent_pubkey: str,
        human_pubkey: str,
        agent_signature: str,
        claim_payload_json: str,
    ) -> None:
        """Create a pending ownership claim."""
        now = _now_iso()
        try:
            self._conn.execute(
                """INSERT INTO ownership_links
                   (id, agent_pubkey, human_pubkey, status, agent_signature,
                    claim_payload_json, claimed_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    claim_id,
                    agent_pubkey,
                    human_pubkey,
                    agent_signature,
                    claim_payload_json,
                    now,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as e:
            raise StoreError(f"Ownership claim already exists: {claim_id}") from e
        except sqlite3.Error as e:
            raise StoreError(f"Failed to create ownership claim: {e}") from e

    def get_ownership_claim(self, claim_id: str) -> Optional[dict]:
        """Get one ownership claim by ID."""
        row = self._conn.execute(
            "SELECT * FROM ownership_links WHERE id = ?",
            (claim_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_ownership_for_agent(self, agent_pubkey: str) -> list[dict]:
        """List ownership claims for an agent (newest first)."""
        rows = self._conn.execute(
            """SELECT * FROM ownership_links
               WHERE agent_pubkey = ?
               ORDER BY claimed_at DESC""",
            (agent_pubkey,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_active_owner(self, agent_pubkey: str) -> Optional[dict]:
        """Return active ownership link for an agent, if any."""
        row = self._conn.execute(
            """SELECT * FROM ownership_links
               WHERE agent_pubkey = ? AND status = 'active'
               ORDER BY confirmed_at DESC, claimed_at DESC
               LIMIT 1""",
            (agent_pubkey,),
        ).fetchone()
        return dict(row) if row else None

    def confirm_ownership_claim(
        self,
        claim_id: str,
        human_signature: str,
        confirm_payload_json: str,
    ) -> None:
        """Confirm a pending ownership claim and activate it."""
        now = _now_iso()
        try:
            row = self._conn.execute(
                "SELECT agent_pubkey, status FROM ownership_links WHERE id = ?",
                (claim_id,),
            ).fetchone()
            if row is None:
                raise KeyNotFoundError(f"Ownership claim not found: {claim_id}")
            if row["status"] != "pending":
                raise StoreError(
                    f"Ownership claim {claim_id} must be pending to confirm (current: {row['status']})"
                )

            # Only one active owner per agent at a time.
            self._conn.execute(
                """UPDATE ownership_links
                   SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_reason = ?
                   WHERE agent_pubkey = ? AND status = 'active'""",
                (now, "system", f"Superseded by ownership claim {claim_id}", row["agent_pubkey"]),
            )
            self._conn.execute(
                """UPDATE ownership_links
                   SET status = 'active',
                       human_signature = ?,
                       confirm_payload_json = ?,
                       confirmed_at = ?
                   WHERE id = ?""",
                (
                    human_signature,
                    confirm_payload_json,
                    now,
                    claim_id,
                ),
            )
            self._conn.commit()
        except (KeyNotFoundError, StoreError):
            raise
        except sqlite3.Error as e:
            raise StoreError(f"Failed to confirm ownership claim: {e}") from e

    def revoke_ownership_claim(
        self,
        claim_id: str,
        revoked_by: str,
        reason: str,
    ) -> None:
        """Revoke an active or pending ownership claim."""
        now = _now_iso()
        try:
            row = self._conn.execute(
                "SELECT status FROM ownership_links WHERE id = ?",
                (claim_id,),
            ).fetchone()
            if row is None:
                raise KeyNotFoundError(f"Ownership claim not found: {claim_id}")
            if row["status"] == "revoked":
                return

            self._conn.execute(
                """UPDATE ownership_links
                   SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_reason = ?
                   WHERE id = ?""",
                (now, revoked_by, reason, claim_id),
            )
            self._conn.commit()
        except KeyNotFoundError:
            raise
        except sqlite3.Error as e:
            raise StoreError(f"Failed to revoke ownership claim: {e}") from e

    def upsert_human_contact_email(
        self,
        pubkey: str,
        email: str,
        email_verified: bool = False,
    ) -> None:
        """Store private contact email metadata for a human key."""
        try:
            self._conn.execute(
                """INSERT INTO human_contacts (pubkey, email, email_verified, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(pubkey) DO UPDATE
                   SET email = excluded.email,
                       email_verified = excluded.email_verified,
                       updated_at = excluded.updated_at""",
                (
                    pubkey,
                    email,
                    int(email_verified),
                    _now_iso(),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreError(f"Failed to upsert human contact email: {e}") from e

    def get_human_contact_email(self, pubkey: str) -> Optional[dict]:
        """Get private contact metadata for a human key."""
        row = self._conn.execute(
            "SELECT pubkey, email, email_verified, updated_at FROM human_contacts WHERE pubkey = ?",
            (pubkey,),
        ).fetchone()
        return dict(row) if row else None

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

    # --- Audit / Source Signals ---

    def append_audit_event(
        self,
        action: str,
        outcome: str,
        actor_pubkey: Optional[str] = None,
        source_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Append an audit event for a write-path action."""
        try:
            self._conn.execute(
                """INSERT INTO audit_events
                   (timestamp, action, actor_pubkey, source_ip, source_ip_hash, user_agent, outcome, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _now_iso(),
                    action,
                    actor_pubkey,
                    source_ip,
                    _hash_ip(source_ip),
                    user_agent,
                    outcome,
                    json.dumps(details) if details is not None else None,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise StoreError(f"Failed to append audit event: {e}") from e

    def get_source_anomaly_signals(
        self,
        hours: int = 24,
        min_events: int = 8,
        min_unique_actors: int = 4,
        limit: int = 100,
    ) -> list[dict]:
        """Return potential source clusters for anti-gaming review."""
        hours_clamped = max(1, min(int(hours), 24 * 30))
        min_events_clamped = max(1, int(min_events))
        min_actors_clamped = max(1, int(min_unique_actors))
        limit_clamped = max(1, min(int(limit), 500))

        cutoff_dt = datetime.now(timezone.utc).replace(microsecond=0)
        cutoff_iso = (cutoff_dt.timestamp() - hours_clamped * 3600)
        cutoff = datetime.fromtimestamp(cutoff_iso, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = self._conn.execute(
            """SELECT source_ip_hash,
                      MIN(source_ip) as sample_ip,
                      COUNT(*) as event_count,
                      COUNT(DISTINCT COALESCE(actor_pubkey, '')) as unique_actor_count,
                      COUNT(DISTINCT action) as action_type_count,
                      SUM(CASE WHEN action = 'registration.create' THEN 1 ELSE 0 END) as registration_count,
                      SUM(CASE WHEN action = 'attestation.submit' THEN 1 ELSE 0 END) as attestation_count,
                      MAX(timestamp) as last_seen
               FROM audit_events
               WHERE timestamp >= ?
                 AND source_ip_hash IS NOT NULL
               GROUP BY source_ip_hash
               HAVING COUNT(*) >= ? AND COUNT(DISTINCT COALESCE(actor_pubkey, '')) >= ?
               ORDER BY event_count DESC, unique_actor_count DESC
               LIMIT ?""",
            (cutoff, min_events_clamped, min_actors_clamped, limit_clamped),
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
                """INSERT INTO attestations
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
        except sqlite3.IntegrityError as e:
            if "attestations.id" in str(e) or "UNIQUE constraint failed: attestations.id" in str(e):
                raise DuplicateAttestationError(
                    f"Attestation ID already exists and cannot be overwritten: {att_id}"
                ) from e
            raise StoreError(f"Failed to save attestation: {e}") from e
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
        skill: Optional[str] = None,
        att_type: Optional[str] = None,
        min_proficiency: Optional[int] = None,
        include_revoked: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
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
        if skill:
            conditions.append("specific_skill = ?")
            params.append(skill)
        if att_type:
            conditions.append("type = ?")
            params.append(att_type)
        if min_proficiency is not None:
            conditions.append("COALESCE(proficiency, 0) >= ?")
            params.append(min_proficiency)
        if not include_revoked:
            conditions.append("is_revoked = 0")

        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT raw_json FROM attestations WHERE {where} ORDER BY issued DESC"
        query_params = list(params)
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            query_params.extend([limit, offset])
        rows = self._conn.execute(query, query_params).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

    def count_attestations_filtered(
        self,
        subject_pubkey: Optional[str] = None,
        attestor_pubkey: Optional[str] = None,
        domain: Optional[str] = None,
        skill: Optional[str] = None,
        att_type: Optional[str] = None,
        min_proficiency: Optional[int] = None,
        include_revoked: bool = False,
    ) -> int:
        """Count attestations matching filters."""
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
        if skill:
            conditions.append("specific_skill = ?")
            params.append(skill)
        if att_type:
            conditions.append("type = ?")
            params.append(att_type)
        if min_proficiency is not None:
            conditions.append("COALESCE(proficiency, 0) >= ?")
            params.append(min_proficiency)
        if not include_revoked:
            conditions.append("is_revoked = 0")

        where = " AND ".join(conditions) if conditions else "1=1"
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM attestations WHERE {where}",
            params,
        ).fetchone()
        return int(row["cnt"]) if row else 0

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
