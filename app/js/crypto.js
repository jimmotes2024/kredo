/**
 * Kredo Web App — Cryptographic operations
 *
 * Ed25519 keypair generation, canonical JSON serialization, signing, verification.
 * Uses tweetnacl (loaded globally as `nacl`).
 *
 * CRITICAL: canonicalJSON() must produce byte-identical output to Python's
 * kredo._canonical.canonical_json(). Rules:
 *   - Keys sorted recursively at every level
 *   - No whitespace: separators (",", ":")
 *   - ensure_ascii=True equivalent (escape non-ASCII to \uXXXX)
 *   - null/undefined values excluded from dicts
 *   - Dates must be pre-formatted as "YYYY-MM-DDTHH:MM:SSZ" strings
 *   - Enums serialized to their string value
 */

const KredoCrypto = (() => {
  // --- Hex encoding helpers ---

  function bytesToHex(bytes) {
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }

  function hexToBytes(hex) {
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
    }
    return bytes;
  }

  // --- Canonical JSON (must match Python _canonical.py byte-for-byte) ---

  /**
   * Recursively normalize an object for canonical serialization.
   * - Sort object keys
   * - Drop null/undefined values
   * - Process arrays recursively
   */
  function sortAndClean(obj) {
    if (obj === null || obj === undefined) {
      return undefined;
    }
    if (Array.isArray(obj)) {
      return obj.map(sortAndClean);
    }
    if (typeof obj === 'object' && !(obj instanceof Date)) {
      const sorted = {};
      const keys = Object.keys(obj).sort();
      for (const key of keys) {
        const val = sortAndClean(obj[key]);
        if (val !== undefined && val !== null) {
          sorted[key] = val;
        }
      }
      return sorted;
    }
    return obj;
  }

  /**
   * Escape non-ASCII characters to \uXXXX to match Python's ensure_ascii=True.
   */
  function ensureAscii(str) {
    let result = '';
    for (let i = 0; i < str.length; i++) {
      const code = str.charCodeAt(i);
      if (code > 127) {
        result += '\\u' + code.toString(16).padStart(4, '0');
      } else {
        result += str[i];
      }
    }
    return result;
  }

  /**
   * Produce canonical JSON string from an object.
   * Byte-compatible with Python's kredo._canonical.canonical_json().
   */
  function canonicalJSON(obj) {
    const normalized = sortAndClean(obj);
    const json = JSON.stringify(normalized, null, 0);
    // JSON.stringify with no replacer and indent=0 uses compact format
    // but we need to ensure no extra whitespace AND ascii-only output
    return ensureAscii(json);
  }

  /**
   * Produce canonical JSON as UTF-8 bytes (what gets signed).
   */
  function canonicalJSONBytes(obj) {
    const str = canonicalJSON(obj);
    return new TextEncoder().encode(str);
  }

  // --- Key management ---

  /**
   * Generate a new Ed25519 keypair.
   * Returns { publicKey: "ed25519:<hex>", secretKey: "<hex>" }
   * secretKey is the full 64-byte tweetnacl secret (seed + pubkey).
   */
  function generateKeypair() {
    const kp = nacl.sign.keyPair();
    return {
      publicKey: 'ed25519:' + bytesToHex(kp.publicKey),
      secretKey: bytesToHex(kp.secretKey),
    };
  }

  /**
   * Derive public key from secret key hex string.
   */
  function pubkeyFromSecret(secretKeyHex) {
    const secretBytes = hexToBytes(secretKeyHex);
    // tweetnacl secretKey is 64 bytes: seed(32) + pubkey(32)
    // The last 32 bytes ARE the public key
    const pubBytes = secretBytes.slice(32, 64);
    return 'ed25519:' + bytesToHex(pubBytes);
  }

  /**
   * Derive keypair from a 32-byte seed (hex string).
   */
  function keypairFromSeed(seedHex) {
    const seed = hexToBytes(seedHex);
    const kp = nacl.sign.keyPair.fromSeed(seed);
    return {
      publicKey: 'ed25519:' + bytesToHex(kp.publicKey),
      secretKey: bytesToHex(kp.secretKey),
    };
  }

  // --- Signing ---

  /**
   * Sign a payload (object) with a secret key.
   * Returns "ed25519:<128 hex chars>" signature string.
   */
  function sign(obj, secretKeyHex) {
    const payload = canonicalJSONBytes(obj);
    const secretBytes = hexToBytes(secretKeyHex);
    const signed = nacl.sign.detached(payload, secretBytes);
    return 'ed25519:' + bytesToHex(signed);
  }

  /**
   * Verify a signature against a payload and public key.
   * Returns true if valid, false otherwise.
   */
  function verify(obj, signature, pubkey) {
    try {
      const payload = canonicalJSONBytes(obj);
      const sigHex = signature.replace('ed25519:', '');
      const sigBytes = hexToBytes(sigHex);
      const pubHex = pubkey.replace('ed25519:', '');
      const pubBytes = hexToBytes(pubHex);
      return nacl.sign.detached.verify(payload, sigBytes, pubBytes);
    } catch (e) {
      return false;
    }
  }

  // --- Attestation helpers ---

  /**
   * Format a Date as ISO 8601 UTC string matching Python's "%Y-%m-%dT%H:%M:%SZ".
   * No milliseconds, Z suffix.
   */
  function formatDate(date) {
    const d = date instanceof Date ? date : new Date(date);
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, '0');
    const day = String(d.getUTCDate()).padStart(2, '0');
    const h = String(d.getUTCHours()).padStart(2, '0');
    const min = String(d.getUTCMinutes()).padStart(2, '0');
    const s = String(d.getUTCSeconds()).padStart(2, '0');
    return `${y}-${m}-${day}T${h}:${min}:${s}Z`;
  }

  /**
   * Generate a UUID v4.
   */
  function uuid4() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    // Fallback
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  /**
   * Build a signable attestation object (everything except signature).
   * Mirrors Python's _attestation_signable().
   */
  function buildSignableAttestation(attestation) {
    const copy = JSON.parse(JSON.stringify(attestation));
    delete copy.signature;
    return copy;
  }

  /**
   * Sign an attestation object. Returns a new object with signature field.
   */
  function signAttestation(attestation, secretKeyHex) {
    const signable = buildSignableAttestation(attestation);
    const signature = sign(signable, secretKeyHex);
    return { ...attestation, signature };
  }

  /**
   * Verify an attestation's signature.
   */
  function verifyAttestation(attestation) {
    if (!attestation.signature) return false;
    const signable = buildSignableAttestation(attestation);
    return verify(signable, attestation.signature, attestation.attestor.pubkey);
  }

  // --- Optional key encryption (nacl.secretbox + PBKDF2 key derivation) ---

  const PBKDF2_ITERATIONS = 210000;
  const PBKDF2_SALT_BYTES = 16;

  function deriveEncryptionKeyLegacy(passphrase) {
    const encoded = new TextEncoder().encode(passphrase);
    const hash = nacl.hash(encoded); // SHA-512, 64 bytes
    return hash.slice(0, 32);
  }

  /**
   * Derive a 32-byte key with PBKDF2-SHA256 from passphrase+salt.
   */
  async function deriveEncryptionKey(passphrase, saltBytes, iterations = PBKDF2_ITERATIONS) {
    const subtle = globalThis.crypto && globalThis.crypto.subtle;
    if (subtle) {
      const encoder = new TextEncoder();
      const keyMaterial = await subtle.importKey(
        'raw',
        encoder.encode(passphrase),
        'PBKDF2',
        false,
        ['deriveBits']
      );
      const bits = await subtle.deriveBits(
        {
          name: 'PBKDF2',
          salt: saltBytes,
          iterations,
          hash: 'SHA-256',
        },
        keyMaterial,
        256
      );
      return new Uint8Array(bits);
    }

    // Legacy fallback if WebCrypto PBKDF2 is unavailable.
    const legacySeed = new Uint8Array([...saltBytes, ...new TextEncoder().encode(passphrase)]);
    const legacyHash = nacl.hash(legacySeed);
    return legacyHash.slice(0, 32);
  }

  /**
   * Encrypt a secret key hex string with a passphrase.
   * Returns a versioned envelope for backward-compatible decryption.
   */
  async function encryptSecretKey(secretKeyHex, passphrase) {
    const salt = nacl.randomBytes(PBKDF2_SALT_BYTES);
    const key = await deriveEncryptionKey(passphrase, salt, PBKDF2_ITERATIONS);
    const nonce = nacl.randomBytes(24);
    const message = new TextEncoder().encode(secretKeyHex);
    const encrypted = nacl.secretbox(message, nonce, key);
    return {
      version: 2,
      kdf: 'pbkdf2-sha256',
      iterations: PBKDF2_ITERATIONS,
      salt: bytesToHex(salt),
      nonce: bytesToHex(nonce),
      ciphertext: bytesToHex(encrypted),
    };
  }

  /**
   * Decrypt a secret key hex string with a passphrase.
   * Supports both new (v2 PBKDF2) and legacy envelope format.
   */
  async function decryptSecretKey(encrypted, passphrase) {
    try {
      if (!encrypted || !encrypted.nonce || !encrypted.ciphertext) {
        return null;
      }

      let key;
      if (encrypted.version === 2 && encrypted.kdf === 'pbkdf2-sha256' && encrypted.salt) {
        const saltBytes = hexToBytes(encrypted.salt);
        const iterations = Number(encrypted.iterations) || PBKDF2_ITERATIONS;
        key = await deriveEncryptionKey(passphrase, saltBytes, iterations);
      } else {
        // Backward compatibility with older unsalted format.
        key = deriveEncryptionKeyLegacy(passphrase);
      }

      const nonce = hexToBytes(encrypted.nonce);
      const ciphertext = hexToBytes(encrypted.ciphertext);
      const decrypted = nacl.secretbox.open(ciphertext, nonce, key);
      if (!decrypted) return null;
      return new TextDecoder().decode(decrypted);
    } catch {
      return null;
    }
  }

  // --- Public API ---

  return {
    bytesToHex,
    hexToBytes,
    canonicalJSON,
    canonicalJSONBytes,
    generateKeypair,
    pubkeyFromSecret,
    keypairFromSeed,
    sign,
    verify,
    formatDate,
    uuid4,
    buildSignableAttestation,
    signAttestation,
    verifyAttestation,
    encryptSecretKey,
    decryptSecretKey,
  };
})();
