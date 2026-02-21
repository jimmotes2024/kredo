/**
 * Kredo Web App — LocalStorage key management
 *
 * Stores identity (name, type, pubkey, secretKey) in localStorage.
 * Optionally encrypts the secret key with a passphrase via nacl.secretbox.
 */

const KredoStorage = (() => {
  const STORAGE_KEY = 'kredo_identity';
  const PUBKEY_RE = /^ed25519:[0-9a-f]{64}$/;
  const SECRET_KEY_HEX_RE = /^[0-9a-f]{128}$/;
  const HEX_RE = /^[0-9a-f]+$/;
  let lastSecretKeyError = null;

  function isHex(value) {
    return typeof value === 'string' && HEX_RE.test(value);
  }

  function isAtlasBrowser() {
    const ua = (globalThis.navigator && globalThis.navigator.userAgent) || '';
    return /atlas/i.test(ua);
  }

  /**
   * Get the stored identity, or null if none exists.
   * Returns { name, type, pubkey, secretKey } or
   *         { name, type, pubkey, encrypted: { nonce, ciphertext } } if encrypted.
   */
  function getIdentity() {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  /**
   * Save identity to localStorage.
   * If passphrase is provided, encrypts the secret key.
   */
  async function saveIdentity(name, type, pubkey, secretKeyHex, passphrase) {
    const normalizedPubkey = String(pubkey || '').toLowerCase().trim();
    if (!PUBKEY_RE.test(normalizedPubkey)) {
      throw new Error('Invalid public key format');
    }
    if (!SECRET_KEY_HEX_RE.test(String(secretKeyHex || '').toLowerCase().trim())) {
      throw new Error('Invalid private key format');
    }

    const normalizedSecret = String(secretKeyHex).toLowerCase().trim();
    const identity = { name, type, pubkey: normalizedPubkey };
    if (passphrase) {
      identity.encrypted = await KredoCrypto.encryptSecretKey(normalizedSecret, passphrase);
    } else {
      identity.secretKey = normalizedSecret;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(identity));
  }

  /**
   * Get the secret key hex string, prompting for passphrase if encrypted.
   * Returns the hex string or null if decryption fails / no identity.
   */
  async function getSecretKey(passphrase) {
    lastSecretKeyError = null;
    const id = getIdentity();
    if (!id) {
      lastSecretKeyError = 'no_identity';
      return null;
    }
    if (id.secretKey) {
      return id.secretKey;
    }
    if (id.encrypted && passphrase) {
      // Atlas currently hard-crashes on some PBKDF2 WebCrypto operations.
      // Fail safely with an explicit compatibility code instead of invoking PBKDF2.
      if (isAtlasBrowser() && id.encrypted.kdf === 'pbkdf2-sha256') {
        lastSecretKeyError = 'atlas_pbkdf2_unsupported';
        return null;
      }
      const decrypted = await KredoCrypto.decryptSecretKey(id.encrypted, passphrase);
      if (!decrypted) {
        lastSecretKeyError = 'decrypt_failed';
        return null;
      }
      return decrypted;
    }
    if (id.encrypted && !passphrase) {
      lastSecretKeyError = 'passphrase_required';
      return null;
    }
    lastSecretKeyError = 'secret_unavailable';
    return null;
  }

  function getLastSecretKeyError() {
    return lastSecretKeyError;
  }

  /**
   * Check whether the stored identity has an encrypted key.
   */
  function isEncrypted() {
    const id = getIdentity();
    return id ? !!id.encrypted : false;
  }

  /**
   * Check whether any identity is stored.
   */
  function hasIdentity() {
    return getIdentity() !== null;
  }

  /**
   * Remove stored identity.
   */
  function clearIdentity() {
    localStorage.removeItem(STORAGE_KEY);
  }

  /**
   * Export identity as a downloadable JSON file.
   */
  function exportIdentity() {
    const id = getIdentity();
    if (!id) return null;

    // For export, we need the unencrypted secret key
    // If encrypted, caller must decrypt first and pass it
    return {
      name: id.name,
      type: id.type,
      pubkey: id.pubkey,
      secretKey: id.secretKey || null,
      encrypted: id.encrypted || null,
      exported_at: KredoCrypto.formatDate(new Date()),
    };
  }

  /**
   * Import identity from a parsed JSON object (from file upload).
   */
  function importIdentity(data) {
    if (!data.pubkey || !data.name) {
      throw new Error('Invalid identity file: missing pubkey or name');
    }
    const normalizedPubkey = String(data.pubkey).toLowerCase().trim();
    if (!PUBKEY_RE.test(normalizedPubkey)) {
      throw new Error('Invalid identity file: bad pubkey format');
    }
    const identity = {
      name: data.name,
      type: data.type || 'human',
      pubkey: normalizedPubkey,
    };
    if (data.encrypted) {
      const enc = data.encrypted;
      if (
        !enc
        || !isHex(enc.nonce || '')
        || !isHex(enc.ciphertext || '')
        || (enc.salt && !isHex(enc.salt))
      ) {
        throw new Error('Invalid identity file: malformed encrypted key blob');
      }
      identity.encrypted = data.encrypted;
    } else if (data.secretKey) {
      const normalizedSecret = String(data.secretKey).toLowerCase().trim();
      if (!SECRET_KEY_HEX_RE.test(normalizedSecret)) {
        throw new Error('Invalid identity file: bad secretKey format');
      }
      identity.secretKey = normalizedSecret;
    } else {
      throw new Error('Identity file has no secret key (plain or encrypted)');
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(identity));
    return identity;
  }

  /**
   * Trigger a file download of the identity JSON.
   */
  function downloadIdentity(secretKeyHex) {
    const id = getIdentity();
    if (!id) return;

    const exportData = {
      name: id.name,
      type: id.type,
      pubkey: id.pubkey,
      backup_format: 'kredo-identity-v2',
      exported_at: KredoCrypto.formatDate(new Date()),
    };

    const providedSecret = typeof secretKeyHex === 'string' ? secretKeyHex : null;
    if (providedSecret) {
      exportData.secretKey = providedSecret;
      exportData.key_storage = 'plaintext';
    } else if (id.encrypted) {
      exportData.encrypted = id.encrypted;
      exportData.key_storage = 'encrypted';
    } else if (id.secretKey) {
      exportData.secretKey = id.secretKey;
      exportData.key_storage = 'plaintext';
    }

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kredo-identity-${id.name}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return {
    getIdentity,
    saveIdentity,
    getSecretKey,
    getLastSecretKeyError,
    isEncrypted,
    hasIdentity,
    clearIdentity,
    exportIdentity,
    importIdentity,
    downloadIdentity,
  };
})();
