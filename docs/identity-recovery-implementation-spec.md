# Kredo Identity + Recovery Implementation Spec

Status: Draft for implementation  
Date: 2026-02-20  
Owner: Kredo core/web teams  
Scope: Key-native auth continuity, optional zero-knowledge recovery, key rotation

## 1) Goals

1. Preserve Kredo's key-native trust model (Ed25519 signatures as identity authority).
2. Improve user survivability across device/browser loss.
3. Avoid custodial/private-key server trust.
4. Support enterprise reliability with auditable recovery and rotation flows.

## 2) Non-Goals

1. No server-side session auth as the source of identity authority.
2. No plaintext private key storage on server.
3. No email/password login that can authorize signed protocol actions.
4. No deterministic `name + passphrase -> identity key` as the primary identity model.

## 3) Architecture Decisions

1. Identity keypair remains client-generated and client-held.
2. All protocol writes remain signature-authenticated with Ed25519.
3. Recovery is optional and encrypted client-side using Argon2id-derived key encryption.
4. Server stores only encrypted recovery envelopes + metadata.
5. Recovery retrieval restores local key material; it does not replace signature-based auth.

## 4) Phased Delivery

## Phase 1: UX Hardening (no backend schema change required)

1. Setup screen starts with equal-weight options:
   1. Create new identity
   2. Recover existing identity
2. Mandatory backup confirmation step before identity completion.
3. Clear warning text for local-only persistence risk.
4. Recover flow supports existing file/seed import first-class.

## Phase 2: Optional Zero-Knowledge Cloud Recovery

1. Add recovery enrollment, fetch, and revoke endpoints.
2. Add encrypted envelope storage table + audit table.
3. Add Governance UI card for recovery enrollment and restore.

## Phase 3: Key Rotation

1. Add signed old->new key rotation API.
2. Add key-resolution endpoint for canonical subject identity.
3. Add UI flow for rotation and post-rotation verification.

## 5) Data Model Contracts

## Table: `recovery_blobs`

| Column | Type | Notes |
|---|---|---|
| `recovery_id` | TEXT PK | Opaque identifier (`rky_[a-zA-Z0-9]{24,64}`) |
| `pubkey` | TEXT NOT NULL | `ed25519:` + 64 hex |
| `envelope_json` | TEXT NOT NULL | Encrypted payload envelope |
| `envelope_sha256` | TEXT NOT NULL | Integrity hash of canonical envelope |
| `status` | TEXT NOT NULL | `active` or `revoked` |
| `created_at` | TEXT NOT NULL | UTC ISO8601 |
| `updated_at` | TEXT NOT NULL | UTC ISO8601 |
| `revoked_at` | TEXT NULL | UTC ISO8601 |
| `revoked_by` | TEXT NULL | Pubkey that signed revoke |
| `last_retrieved_at` | TEXT NULL | UTC ISO8601 |
| `retrieve_count` | INTEGER NOT NULL | Default `0` |

Indexes:
1. `idx_recovery_pubkey_active(pubkey, status)`
2. `idx_recovery_updated(updated_at DESC)`

## Table: `key_rotations`

| Column | Type | Notes |
|---|---|---|
| `rotation_id` | TEXT PK | Opaque id |
| `old_pubkey` | TEXT NOT NULL | Signing key for rotation proof |
| `new_pubkey` | TEXT NOT NULL | Replacement key |
| `reason` | TEXT NOT NULL | `scheduled` / `compromise` / `migration` |
| `payload_json` | TEXT NOT NULL | Canonical signed payload |
| `signature` | TEXT NOT NULL | Ed25519 signature by old key |
| `created_at` | TEXT NOT NULL | UTC ISO8601 |
| `status` | TEXT NOT NULL | `active` / `superseded` |

Indexes:
1. `idx_rotation_old(old_pubkey)`
2. `idx_rotation_new(new_pubkey)`

## Table: `recovery_audit`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | Audit row id |
| `timestamp` | TEXT NOT NULL | UTC ISO8601 |
| `action` | TEXT NOT NULL | `recovery.enroll`, `recovery.fetch`, `recovery.revoke`, `key.rotate` |
| `outcome` | TEXT NOT NULL | `accepted` / `rejected` |
| `recovery_id` | TEXT NULL | Correlation |
| `actor_pubkey` | TEXT NULL | For signed actions |
| `source_ip` | TEXT NULL | Raw IP for ops |
| `source_ip_hash` | TEXT NULL | Hashed risk signal |
| `user_agent` | TEXT NULL | Request UA |
| `details_json` | TEXT NULL | Error codes/reasons |

## 6) Cryptography Contract

1. Identity key:
   1. Ed25519 keypair, generated locally.
2. Recovery envelope encryption:
   1. Symmetric: `nacl.secretbox` (XSalsa20-Poly1305).
   2. Key derivation: Argon2id (required for new enrollments).
3. Envelope structure:

```json
{
  "version": 1,
  "cipher": "xsalsa20-poly1305",
  "kdf": {
    "name": "argon2id",
    "memory_kib": 65536,
    "iterations": 3,
    "parallelism": 1,
    "salt_hex": "..."
  },
  "nonce_hex": "...",
  "ciphertext_hex": "...",
  "wrapped_pubkey": "ed25519:...",
  "created_at": "2026-02-20T18:00:00Z"
}
```

4. Minimum KDF guardrails for new enrollment:
   1. `memory_kib >= 65536`
   2. `iterations >= 2`
   3. `parallelism >= 1`
5. Server must reject non-compliant envelope metadata.

## 7) API Contracts

All signed payloads must use canonical JSON serialization already used by Kredo signature verification.

## 7.1 POST `/recovery/enroll`

Purpose: Create or replace encrypted recovery blob for a key.  
Auth: Ed25519 signature by `pubkey`.

Request:

```json
{
  "recovery_id": "rky_a1b2c3d4e5f6g7h8i9j0k1l2",
  "pubkey": "ed25519:...",
  "envelope": { "...": "see crypto contract" },
  "signature": "ed25519:..."
}
```

Signed payload:

```json
{
  "action": "recovery_enroll",
  "recovery_id": "rky_...",
  "pubkey": "ed25519:...",
  "envelope_sha256": "..."
}
```

Responses:
1. `201`:

```json
{
  "status": "active",
  "recovery_id": "rky_...",
  "pubkey": "ed25519:...",
  "updated_at": "2026-02-20T18:00:00Z"
}
```

2. `400` invalid shape/signature/KDF policy.
3. `409` duplicate conflict where replacement policy disallows overwrite without explicit flag.
4. `429` rate limit exceeded.

## 7.2 GET `/recovery/blob/{recovery_id}`

Purpose: Retrieve encrypted envelope for local decryption on new device.  
Auth: None (must not reveal sensitive metadata beyond envelope + pubkey).  
Security controls: strict rate limiting, generic not-found responses, audit.

Response `200`:

```json
{
  "recovery_id": "rky_...",
  "pubkey": "ed25519:...",
  "envelope": { "...": "encrypted envelope" },
  "updated_at": "2026-02-20T18:00:00Z"
}
```

Response `404`:

```json
{
  "error": "Recovery blob unavailable"
}
```

## 7.3 POST `/recovery/revoke`

Purpose: Revoke recovery blob enrollment (compromise/suspicion/cleanup).  
Auth: Ed25519 signature by enrolled `pubkey`.

Request:

```json
{
  "recovery_id": "rky_...",
  "pubkey": "ed25519:...",
  "reason": "compromise suspected",
  "signature": "ed25519:..."
}
```

Signed payload:

```json
{
  "action": "recovery_revoke",
  "recovery_id": "rky_...",
  "pubkey": "ed25519:...",
  "reason": "..."
}
```

Response `200`:

```json
{
  "status": "revoked",
  "recovery_id": "rky_..."
}
```

## 7.4 POST `/keys/rotate`

Purpose: Rotate key identity with cryptographic continuity proof.  
Auth: Ed25519 signature by `old_pubkey`.

Request:

```json
{
  "rotation_id": "rot_...",
  "old_pubkey": "ed25519:...",
  "new_pubkey": "ed25519:...",
  "reason": "scheduled",
  "signature": "ed25519:..."
}
```

Signed payload:

```json
{
  "action": "key_rotate",
  "rotation_id": "rot_...",
  "old_pubkey": "ed25519:...",
  "new_pubkey": "ed25519:...",
  "reason": "scheduled"
}
```

Response `200`:

```json
{
  "status": "active",
  "rotation_id": "rot_...",
  "old_pubkey": "ed25519:...",
  "new_pubkey": "ed25519:..."
}
```

## 7.5 GET `/keys/resolve/{pubkey}`

Purpose: Resolve current canonical key and chain info for any historical key.

Response `200`:

```json
{
  "query_pubkey": "ed25519:...",
  "canonical_pubkey": "ed25519:...",
  "is_rotated": true,
  "chain": [
    {
      "rotation_id": "rot_...",
      "old_pubkey": "ed25519:...",
      "new_pubkey": "ed25519:...",
      "created_at": "2026-02-20T18:00:00Z"
    }
  ]
}
```

## 8) UI Wireflow

## 8.1 Setup Entry

Screen: `Create or Recover Identity`

Primary choices (equal visual prominence):
1. `Create New Identity`
2. `Recover Existing Identity`

Global helper text:
1. "Kredo never stores plaintext private keys."
2. "Until backup is complete, identity exists only in this browser."

## 8.2 Create Flow

1. Enter `name`, `type`, `passphrase` (passphrase optional but strongly recommended).
2. Generate local keypair.
3. Mandatory Backup Confirmation:
   1. Download JSON backup.
   2. Optional copy seed/recovery code.
   3. Optional enable cloud recovery.
4. Backup verification challenge (must pass one):
   1. Re-upload file.
   2. Enter verification phrase/code fragment.
5. Register key.
6. Success + next actions.

Failure states:
1. Backup not confirmed -> cannot continue.
2. Registration rate-limited -> identity still local, show retry guidance.

## 8.3 Recover Flow

Tabs:
1. `Backup File`
2. `Seed`
3. `Cloud Recovery`

Cloud Recovery sequence:
1. Enter `recovery_id`.
2. Enter passphrase.
3. Fetch envelope from `/recovery/blob/{recovery_id}`.
4. Decrypt locally.
5. Validate decrypted key matches `wrapped_pubkey`.
6. Persist to local storage.
7. Show recovered identity summary.

Failure states:
1. Invalid recovery id -> generic unavailable error.
2. Wrong passphrase -> decryption failed.
3. Corrupt envelope -> integrity error, no import.

## 8.4 Governance Additions

New card: `Recovery`

Actions:
1. `Enroll/Update Cloud Recovery` (signed)
2. `Revoke Cloud Recovery` (signed)
3. Show last update time and enrollment status.

## 8.5 Rotation Flow

1. User creates new keypair locally.
2. Old key signs rotation payload.
3. Submit `/keys/rotate`.
4. Update local default identity to new key.
5. Offer immediate recovery re-enrollment for new key.

## 9) Acceptance Criteria

## 9.1 Phase 1 UX Acceptance

1. Setup page shows create/recover as equal-weight entry points.
2. User cannot complete create flow without backup confirmation.
3. App displays explicit localStorage survivability warning.
4. Recover flow reachable in one click from initial setup screen.

## 9.2 Recovery Security Acceptance

1. Server never stores plaintext private key or passphrase.
2. New enrollments require Argon2id metadata meeting policy minimums.
3. Signed recovery enrollment/revoke verification enforced.
4. Recovery fetch rate limit active and audited.
5. Recovery fetch unavailable responses do not leak existence detail.

## 9.3 Recovery Functional Acceptance

1. User can recover identity on new device using recovery id + passphrase.
2. Decrypted key signs successfully after restore.
3. Revoked recovery ids can no longer fetch active envelope.
4. Re-enroll updates envelope and timestamp.

## 9.4 Rotation Acceptance

1. Old key can rotate to new key with valid signature.
2. Invalid old-key signature is rejected.
3. `GET /keys/resolve/{pubkey}` returns canonical current key.
4. Existing trust/profile endpoints resolve historical keys to canonical key for continuity.

## 9.5 Regression Acceptance

1. Existing attest/verify/search/taxonomy APIs remain behaviorally compatible.
2. Existing local file import/export flows remain functional.
3. Existing encrypted local storage flow remains compatible.

## 10) Test Plan

## Unit

1. Envelope validator tests (kdf bounds, field types, hex sizes).
2. Signature payload canonicalization tests for new recovery/rotation actions.
3. Key-chain resolution logic tests.

## Integration

1. Enroll -> fetch -> decrypt -> sign roundtrip.
2. Enroll -> revoke -> fetch denied.
3. Rotate -> resolve old key -> canonical new key.
4. Rotation + profile lookup continuity.

## E2E UI

1. Create flow blocked until backup confirmation.
2. Recover via file, seed, cloud.
3. Governance recovery enroll/revoke actions.
4. New-device simulation using separate browser profile.

## 11) Operational Controls

1. Rate limits:
   1. `POST /recovery/enroll`: e.g., 5/hour/pubkey.
   2. `GET /recovery/blob/{id}`: e.g., 20/hour/IP.
   3. `POST /keys/rotate`: e.g., 3/day/pubkey.
2. Audit + alerts:
   1. Excess fetch failures by source.
   2. Repeated rotation attempts on same key.
3. Metrics:
   1. Recovery enrollment adoption rate.
   2. Recovery success/failure rates.
   3. Mean restore completion time.

## 12) Open Decisions (must resolve before implementation start)

1. Recovery identifier format:
   1. Opaque random id only.
   2. Optional QR packaging for easy transfer.
2. Backward compatibility policy:
   1. Allow scrypt-enveloped legacy blobs for import only.
   2. New writes must be Argon2id.
3. Rotation resolution policy:
   1. Auto-resolve in all profile/trust endpoints.
   2. Resolve only in dedicated endpoint first, then expand.

## 13) Definition of Done

1. All acceptance criteria pass in CI and manual QA.
2. API docs updated with new endpoints and signed payload contracts.
3. App UX deployed with create/recover parity and mandatory backup confirmation.
4. Recovery and rotation audit logs queryable for incident review.
5. Migration notes published for existing users and operators.

