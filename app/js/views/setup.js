/**
 * Setup View — Identity creation, recovery, and backup gate
 *
 * State machine: landing | create | recover | backup | existing
 *
 * Landing: Two equal-weight cards — Create New Identity / Recover Existing
 * Create:  Name → Type → Passphrase → Generate keypair → Register → Backup gate
 * Recover: Upload key file | Paste seed | Cloud Recovery (coming soon)
 * Backup:  Mandatory restore proof (download/copy seed, verify last 8 chars)
 * Existing: Shown when identity already exists (fallback — router normally redirects)
 */

const SetupView = (() => {
  let state = {
    step: 'landing',
    keypair: null,
    name: '',
    type: 'human',
    registered: false,
    backupVerified: false,
    secretKeyHex: null, // held in memory during backup gate only
  };

  function render() {
    if (KredoStorage.hasIdentity() && state.step !== 'backup') {
      renderExisting();
    } else if (state.step === 'backup' && state.secretKeyHex) {
      renderBackupGate();
    } else {
      renderLanding();
    }
  }

  // --- Landing: Equal-weight Create / Recover ---

  function renderLanding() {
    state = { step: 'landing', keypair: null, name: '', type: 'human', registered: false, backupVerified: false, secretKeyHex: null };

    KredoUI.renderView(`
      <h1 class="page-title">Welcome to Kredo</h1>
      <p class="page-subtitle">Your identity is an Ed25519 keypair. No accounts, no passwords &mdash; just keys.</p>

      <div class="landing-cards">
        <div class="landing-card" onclick="SetupView.showCreate()">
          <div class="landing-card-icon">+</div>
          <h3>Create New Identity</h3>
          <p>Generate a fresh keypair and register on the network.</p>
        </div>
        <div class="landing-card" onclick="SetupView.showRecover()">
          <div class="landing-card-icon">&#8635;</div>
          <h3>Recover Existing Identity</h3>
          <p>Import a backup or paste your seed to restore access.</p>
        </div>
      </div>

      <div class="warning-banner">
        <strong>&#9888; Your identity lives only in this browser until you back it up.</strong>
        Clearing browser data, switching devices, or using a different browser means losing access.
        Download your key backup or copy your seed &mdash; and keep it safe.
      </div>
    `);
  }

  // --- Create flow ---

  function showCreate() {
    state.step = 'create';

    KredoUI.renderView(`
      <h1 class="page-title">Create New Identity</h1>
      <p class="page-subtitle">Your Ed25519 keypair is generated locally in your browser &mdash; your private key never leaves this device.</p>

      <div class="card">
        <div class="card-header">Identity Details</div>
        <form id="setup-form" onsubmit="SetupView.handleCreate(event)">
          <div class="form-group">
            <label for="setup-name">Your name</label>
            <input type="text" id="setup-name" name="name" required placeholder="e.g. Alice, Vanguard, SecurityBot" maxlength="100" autofocus>
            <div class="hint">How others will see you in the Kredo network</div>
          </div>

          <div class="form-group">
            <label>Identity type</label>
            <div class="option-grid">
              <div class="option-card selected" onclick="SetupView.setType('human')" id="opt-human">
                <h4>Human</h4>
                <p>Security analyst, team lead, developer, or other human reviewer</p>
              </div>
              <div class="option-card" onclick="SetupView.setType('agent')" id="opt-agent">
                <h4>Agent</h4>
                <p>AI agent, bot, or automated system</p>
              </div>
            </div>
          </div>

          <div class="form-group">
            <label for="setup-passphrase">Passphrase (optional)</label>
            <input type="password" id="setup-passphrase" name="passphrase" placeholder="Encrypt your private key locally">
            <div class="hint">If set, you'll need this passphrase to sign attestations. Recommended for shared computers.</div>
          </div>

          <div class="btn-group">
            <button type="submit" class="btn btn-primary">Generate Keypair &amp; Register</button>
            <button type="button" class="btn" onclick="SetupView.showLanding()">Back</button>
          </div>
        </form>
      </div>
    `);
  }

  // --- Recover flow ---

  function showRecover() {
    state.step = 'recover';

    KredoUI.renderView(`
      <h1 class="page-title">Recover Existing Identity</h1>
      <p class="page-subtitle">Restore your identity from a backup file or seed.</p>

      <div class="landing-cards">
        <div class="landing-card" onclick="SetupView.triggerImport()">
          <div class="landing-card-icon">&#128194;</div>
          <h3>Upload Key File</h3>
          <p>Select a <code>.json</code> backup file from a previous session.</p>
        </div>
        <div class="landing-card" onclick="SetupView.showPasteSeed()">
          <div class="landing-card-icon">&#128273;</div>
          <h3>Paste Seed</h3>
          <p>Enter your 64-character hex seed to regenerate your keypair.</p>
        </div>
        <div class="landing-card landing-card-disabled">
          <div class="landing-card-icon">&#9729;</div>
          <h3>Cloud Recovery</h3>
          <p>Restore your identity using your recovery ID and passphrase.</p>
          <span class="coming-soon-badge">Coming Soon</span>
        </div>
      </div>

      <input type="file" id="import-file" accept=".json" style="display:none" onchange="SetupView.handleImport(event)">

      <div id="paste-seed-form" style="display:none">
        <div class="card">
          <div class="card-header">Import from Seed</div>
          <div class="form-group">
            <label for="import-seed">64-character hex seed</label>
            <input type="text" id="import-seed" placeholder="Paste your 64-hex-char seed..." maxlength="64">
            <div class="hint">The raw 32-byte seed in hexadecimal (64 characters, 0-9 and a-f)</div>
          </div>
          <div class="form-group">
            <label for="import-name">Name</label>
            <input type="text" id="import-name" placeholder="Your name" required maxlength="100">
          </div>
          <div class="btn-group">
            <button class="btn btn-primary" onclick="SetupView.handleSeedImport()">Import from Seed</button>
          </div>
        </div>
      </div>

      <div style="margin-top:1rem">
        <button class="btn" onclick="SetupView.showLanding()">Back</button>
      </div>
    `);
  }

  function showPasteSeed() {
    const el = document.getElementById('paste-seed-form');
    if (el) el.style.display = 'block';
    const input = document.getElementById('import-seed');
    if (input) input.focus();
  }

  // --- Backup gate with restore proof ---

  function renderBackupGate() {
    const identity = KredoStorage.getIdentity();
    if (!identity || !state.secretKeyHex) {
      KredoApp.navigate('dashboard');
      return;
    }

    const seed = KredoCrypto.seedFromSecret(state.secretKeyHex);
    const pubkey = identity.pubkey;

    KredoUI.renderView(`
      <h1 class="page-title" style="color:var(--green)">&#10003; Identity Created</h1>

      <div class="card" style="border-color:var(--yellow)">
        <div class="card-header" style="color:var(--yellow)">&#9888; BACK UP YOUR KEY NOW</div>
        <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:1rem">
          Your private key exists <strong>ONLY</strong> in this browser's localStorage.
          If you clear browser data, switch devices, or use a different browser,
          your identity is gone forever.
        </p>

        <div class="review-panel" style="margin-bottom:1rem">
          <div class="review-row">
            <div class="review-key">Name</div>
            <div class="review-val">${KredoUI.escapeHtml(identity.name)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Public Key</div>
            <div class="review-val">
              <div class="key-display" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(pubkey)}', 'Public key')" title="Click to copy">${KredoUI.escapeHtml(pubkey)}</div>
            </div>
          </div>
          <div class="review-row">
            <div class="review-key">Registered</div>
            <div class="review-val">${state.registered ? 'Yes &mdash; visible on the Kredo network' : 'Local only &mdash; register later from Dashboard'}</div>
          </div>
        </div>

        <h4 style="margin-bottom:0.75rem">Step 1: Save your backup</h4>
        <div class="btn-group" style="margin-bottom:1.25rem">
          <button class="btn btn-primary" onclick="SetupView.downloadBackup()">Download Key Backup</button>
          <button class="btn" onclick="SetupView.copySeed()">Copy Seed to Clipboard</button>
        </div>

        <div class="seed-display-wrapper" id="seed-display-wrapper" style="margin-bottom:1.25rem">
          <label style="font-size:0.85rem;color:var(--text-muted);display:block;margin-bottom:0.3rem">Your seed (click to reveal)</label>
          <div class="seed-masked" id="seed-display" onclick="SetupView.toggleSeedReveal()">Click to reveal seed</div>
        </div>

        <h4 style="margin-bottom:0.5rem">Step 2: Verify your backup</h4>
        <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:0.5rem">
          Enter the <strong>last 8 characters</strong> of your seed to confirm you saved it:
        </p>
        <div class="verify-input-row">
          <input type="text" id="backup-verify" class="verify-input" maxlength="8" placeholder="Last 8 hex chars" oninput="SetupView.checkVerification()">
          <span id="verify-status" class="verify-status"></span>
        </div>
        <div id="verify-error" style="color:var(--red);font-size:0.8rem;margin-top:0.3rem;min-height:1.2em"></div>

        <div class="btn-group" style="margin-top:1.25rem">
          <button class="btn btn-primary" id="btn-continue" disabled onclick="SetupView.continueToDashboard()">Continue to Dashboard</button>
        </div>
      </div>
    `);
  }

  function toggleSeedReveal() {
    const el = document.getElementById('seed-display');
    if (!el || !state.secretKeyHex) return;
    const seed = KredoCrypto.seedFromSecret(state.secretKeyHex);
    if (el.classList.contains('seed-masked')) {
      el.textContent = seed;
      el.classList.remove('seed-masked');
      el.classList.add('seed-revealed');
    } else {
      el.textContent = 'Click to reveal seed';
      el.classList.remove('seed-revealed');
      el.classList.add('seed-masked');
    }
  }

  function copySeed() {
    if (!state.secretKeyHex) return;
    const seed = KredoCrypto.seedFromSecret(state.secretKeyHex);
    KredoUI.copyToClipboard(seed, 'Seed');
  }

  function checkVerification() {
    const input = document.getElementById('backup-verify');
    const statusEl = document.getElementById('verify-status');
    const errorEl = document.getElementById('verify-error');
    const btn = document.getElementById('btn-continue');
    if (!input || !statusEl || !btn || !state.secretKeyHex) return;

    const value = input.value.trim().toLowerCase();
    const seed = KredoCrypto.seedFromSecret(state.secretKeyHex);
    const expected = seed.slice(-8).toLowerCase();

    if (value.length === 0) {
      statusEl.textContent = '';
      if (errorEl) errorEl.textContent = '';
      btn.disabled = true;
      state.backupVerified = false;
      return;
    }

    if (value.length < 8) {
      statusEl.textContent = '';
      if (errorEl) errorEl.textContent = '';
      btn.disabled = true;
      state.backupVerified = false;
      return;
    }

    if (value === expected) {
      statusEl.textContent = '\u2713';
      statusEl.style.color = 'var(--green)';
      if (errorEl) errorEl.textContent = '';
      btn.disabled = false;
      state.backupVerified = true;
    } else {
      statusEl.textContent = '\u2717';
      statusEl.style.color = 'var(--red)';
      if (errorEl) errorEl.textContent = 'Does not match. Check your backup and try again.';
      btn.disabled = true;
      state.backupVerified = false;
    }
  }

  function continueToDashboard() {
    if (!state.backupVerified) return;
    // Clear sensitive data from memory
    state.secretKeyHex = null;
    state.step = 'landing';
    KredoApp.navigate('dashboard');
  }

  // --- Existing identity view (fallback) ---

  function renderExisting() {
    state.step = 'existing';
    const id = KredoStorage.getIdentity();
    KredoUI.renderView(`
      <h1 class="page-title">Identity Setup</h1>

      <div class="card">
        <div class="card-header">Current Identity</div>
        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">Name</div>
            <div class="review-val">${KredoUI.escapeHtml(id.name)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Type</div>
            <div class="review-val">${KredoUI.typeBadge(id.type)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Public Key</div>
            <div class="review-val">
              <div class="key-display" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(id.pubkey)}', 'Public key')" title="Click to copy">${KredoUI.escapeHtml(id.pubkey)}</div>
            </div>
          </div>
          <div class="review-row">
            <div class="review-key">Encryption</div>
            <div class="review-val">${KredoStorage.isEncrypted() ? 'Private key is passphrase-encrypted' : 'Private key stored in plaintext'}</div>
          </div>
        </div>

        <div class="btn-group" style="margin-top:1rem">
          <button class="btn" onclick="SetupView.downloadBackup()">Download Key Backup</button>
          <button class="btn" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(id.pubkey)}', 'Public key')">Copy Public Key</button>
          <button class="btn btn-danger" onclick="SetupView.confirmReset()">Reset Identity</button>
        </div>
      </div>
    `);
  }

  // --- Event handlers ---

  function showLanding() {
    renderLanding();
  }

  function setType(type) {
    state.type = type;
    document.getElementById('opt-human').classList.toggle('selected', type === 'human');
    document.getElementById('opt-agent').classList.toggle('selected', type === 'agent');
  }

  async function handleCreate(e) {
    e.preventDefault();
    const name = document.getElementById('setup-name').value.trim();
    let passphrase = document.getElementById('setup-passphrase').value.trim();

    if (!name) {
      KredoUI.showError('Please enter a name');
      return;
    }

    if (!passphrase) {
      const prompted = prompt('Set a passphrase to encrypt your private key (recommended). Leave blank to continue without encryption:');
      if (prompted !== null) {
        passphrase = prompted.trim();
      }
    }
    if (passphrase && passphrase.length < 8) {
      KredoUI.showError('Passphrase must be at least 8 characters');
      return;
    }
    if (!passphrase) {
      const allowPlaintext = confirm(
        "Warning: your private key will be stored in plaintext in this browser's localStorage.\n\nContinue without encryption?"
      );
      if (!allowPlaintext) {
        KredoUI.showInfo('Identity creation cancelled. Set a passphrase to continue securely.');
        return;
      }
    }

    // Generate keypair
    const keypair = KredoCrypto.generateKeypair();

    // Store locally
    await KredoStorage.saveIdentity(name, state.type, keypair.publicKey, keypair.secretKey, passphrase || null);

    // Register with API
    let registered = false;
    try {
      await KredoAPI.register(keypair.publicKey, name, state.type);
      registered = true;
    } catch (err) {
      if (err.status === 429) {
        KredoUI.showWarning('Rate limited — your identity is saved locally. Register later from Dashboard.');
      } else {
        KredoUI.showWarning('Could not register with network: ' + err.message + '. Your identity is saved locally.');
      }
    }

    KredoApp.updateIdentityStatus();

    // Transition to backup gate
    state.step = 'backup';
    state.name = name;
    state.keypair = keypair;
    state.registered = registered;
    state.secretKeyHex = keypair.secretKey;
    state.backupVerified = false;
    renderBackupGate();
  }

  function triggerImport() {
    document.getElementById('import-file').click();
  }

  function handleImport(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        if (data.secretKey && !data.encrypted) {
          const allowPlaintext = confirm(
            'This backup contains an unencrypted private key. Importing it will store the key in plaintext in localStorage.\n\nContinue?'
          );
          if (!allowPlaintext) {
            return;
          }
        }
        KredoStorage.importIdentity(data);
        KredoApp.updateIdentityStatus();
        KredoUI.showSuccess('Identity imported successfully');
        KredoApp.navigate('dashboard');
      } catch (err) {
        KredoUI.showError('Import failed: ' + err.message);
      }
    };
    reader.readAsText(file);
  }

  async function handleSeedImport() {
    const seed = document.getElementById('import-seed').value.trim();
    const name = document.getElementById('import-name').value.trim();

    if (!/^[0-9a-f]{64}$/i.test(seed)) {
      KredoUI.showError('Seed must be exactly 64 hex characters');
      return;
    }
    if (!name) {
      KredoUI.showError('Please enter a name');
      return;
    }

    let passphrase = prompt('Set a passphrase to encrypt this imported key (recommended). Leave blank to continue without encryption:');
    passphrase = passphrase ? passphrase.trim() : '';
    if (passphrase && passphrase.length < 8) {
      KredoUI.showError('Passphrase must be at least 8 characters');
      return;
    }
    if (!passphrase) {
      const allowPlaintext = confirm(
        'Warning: imported private key will be stored in plaintext in localStorage.\n\nContinue without encryption?'
      );
      if (!allowPlaintext) {
        return;
      }
    }

    try {
      const keypair = KredoCrypto.keypairFromSeed(seed);
      await KredoStorage.saveIdentity(name, 'human', keypair.publicKey, keypair.secretKey, passphrase || null);
      KredoApp.updateIdentityStatus();
      KredoUI.showSuccess('Identity imported from seed');
      KredoApp.navigate('dashboard');
    } catch (err) {
      KredoUI.showError('Invalid seed: ' + err.message);
    }
  }

  async function downloadBackup() {
    const id = KredoStorage.getIdentity();
    if (!id) return;

    if (KredoStorage.isEncrypted()) {
      const passphrase = prompt('Enter your passphrase to include the private key in the backup:');
      if (passphrase) {
        const sk = await KredoStorage.getSecretKey(passphrase);
        if (sk) {
          KredoStorage.downloadIdentity(sk);
        } else {
          KredoUI.showError('Wrong passphrase');
        }
      } else {
        // Download without secret key — encrypted blob only
        KredoStorage.downloadIdentity(null);
      }
    } else {
      KredoStorage.downloadIdentity();
    }
  }

  function confirmReset() {
    if (confirm('This will delete your identity from this browser. Make sure you have a backup!\n\nContinue?')) {
      KredoStorage.clearIdentity();
      KredoApp.updateIdentityStatus();
      KredoUI.showInfo('Identity cleared');
      renderLanding();
    }
  }

  return {
    render,
    showCreate,
    showRecover,
    showPasteSeed,
    showLanding,
    setType,
    handleCreate,
    triggerImport,
    handleImport,
    handleSeedImport,
    downloadBackup,
    confirmReset,
    toggleSeedReveal,
    copySeed,
    checkVerification,
    continueToDashboard,
  };
})();
