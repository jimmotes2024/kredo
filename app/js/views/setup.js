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
  const ENCRYPT_ACTION_TIMEOUT_MS = 12000;

  function isAtlasBrowser() {
    const ua = (globalThis.navigator && globalThis.navigator.userAgent) || '';
    return /atlas/i.test(ua);
  }

  function render() {
    const setupModeRaw = ((KredoApp.getHashParam && KredoApp.getHashParam()) || '').toLowerCase();
    const setupMode = setupModeRaw.split('/')[0];
    const setupSubmode = setupModeRaw.split('/')[1] || '';
    if (state.step === 'backup' && state.secretKeyHex) {
      renderBackupGate();
    } else if (setupMode === 'recover') {
      showRecover(setupSubmode === 'seed');
    } else if (setupMode === 'create') {
      showCreate();
    } else if (KredoStorage.hasIdentity() && state.step !== 'backup') {
      renderExisting();
    } else {
      renderLanding();
    }
  }

  // --- Landing: Equal-weight Create / Recover ---

  function renderLanding() {
    state = { step: 'landing', keypair: null, name: '', type: 'human', registered: false, backupVerified: false, secretKeyHex: null };

    KredoUI.renderView(`
      <h1 class="page-title">Welcome to Kredo</h1>
      <p class="page-subtitle">Simple model: create identity once, then load it anywhere. Your identity is your Ed25519 key.</p>

      <div class="landing-cards">
        <div class="landing-card" data-setup-action="show-create">
          <div class="landing-card-icon">+</div>
          <h3>Create New Identity</h3>
          <p>Generate a fresh keypair and register on the network.</p>
        </div>
        <div class="landing-card" data-setup-action="show-recover">
          <div class="landing-card-icon">&#8635;</div>
          <h3>Load Identity (Login)</h3>
          <p>Use backup file or seed to sign in on this browser/device.</p>
        </div>
      </div>

      <div class="warning-banner">
        <strong>&#9888; Portable identity requires a backup.</strong>
        New browser, new computer, or cleared browser data means you must load your key again.
        Save backup JSON or seed and keep it safe.
      </div>

      <div class="card" style="margin-top:1rem">
        <div class="card-header">How It Works</div>
        <ol style="padding-left:1.2rem;color:var(--text-muted);font-size:0.9rem">
          <li>Create identity once.</li>
          <li>Save your backup JSON or seed.</li>
          <li>On any browser/device, use <strong>Load Identity (Login)</strong>.</li>
        </ol>
      </div>
    `);
    bindSetupActions();
  }

  // --- Create flow ---

  function showCreate() {
    state.step = 'create';

    KredoUI.renderView(`
      <h1 class="page-title">Create New Identity</h1>
      <p class="page-subtitle">Your Ed25519 keypair is generated locally in your browser &mdash; your private key never leaves this device.</p>

      <div class="card">
        <div class="card-header">Identity Details</div>
        <form id="setup-form">
          <div class="form-group">
            <label for="setup-name">Your name</label>
            <input type="text" id="setup-name" name="name" required placeholder="e.g. Alice, Vanguard, SecurityBot" maxlength="100" autofocus>
            <div class="hint">How others will see you in the Kredo network</div>
          </div>

          <div class="form-group">
            <label>Identity type</label>
            <div class="option-grid">
              <div class="option-card selected" data-setup-action="set-type" data-type="human" id="opt-human">
                <h4>Human</h4>
                <p>Security analyst, team lead, developer, or other human reviewer</p>
              </div>
              <div class="option-card" data-setup-action="set-type" data-type="agent" id="opt-agent">
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
            <button type="button" class="btn" data-setup-action="show-landing">Back</button>
          </div>
        </form>
      </div>
    `);
    bindSetupActions();
  }

  // --- Recover flow ---

  function showRecover(showSeed = false) {
    state.step = 'recover';
    const hasCurrentIdentity = KredoStorage.hasIdentity();

    KredoUI.renderView(`
      <h1 class="page-title">Load Identity (Login)</h1>
      <p class="page-subtitle">Sign in by loading your existing key from backup file or seed.</p>

      ${hasCurrentIdentity ? `
      <div class="warning-banner warning-banner-subtle">
        <strong>Note:</strong> Loading a key here replaces the identity currently stored in this browser.
      </div>
      ` : ''}

      <div class="landing-cards">
        <label class="landing-card" for="import-file">
          <div class="landing-card-icon">&#128194;</div>
          <h3>Login with Backup File</h3>
          <p>Select a <code>.json</code> backup file from a previous session.</p>
        </label>
        <a class="landing-card" href="#/setup/recover/seed">
          <div class="landing-card-icon">&#128273;</div>
          <h3>Login with Seed</h3>
          <p>Enter your 64-character hex seed to regenerate your keypair.</p>
        </a>
        <div class="landing-card landing-card-disabled">
          <div class="landing-card-icon">&#9729;</div>
          <h3>Cloud Recovery</h3>
          <p>Restore your identity using your recovery ID and passphrase.</p>
          <span class="coming-soon-badge">Coming Soon</span>
        </div>
      </div>

      <input type="file" id="import-file" accept=".json" style="display:none">

      <div id="paste-seed-form" style="${showSeed ? 'display:block' : 'display:none'}">
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
            <button class="btn btn-primary" data-setup-action="seed-import">Import from Seed</button>
          </div>
        </div>
      </div>

      <div style="margin-top:1rem">
        <a class="btn" href="#/setup">Back</a>
      </div>
    `);
    bindSetupActions();
    if (showSeed) {
      const input = document.getElementById('import-seed');
      if (input) input.focus();
    }
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
              <div class="key-display" data-setup-action="copy-pubkey" data-pubkey="${KredoUI.escapeHtml(pubkey)}" title="Click to copy">${KredoUI.escapeHtml(pubkey)}</div>
            </div>
          </div>
          <div class="review-row">
            <div class="review-key">Registered</div>
            <div class="review-val">${state.registered ? 'Yes &mdash; visible on the Kredo network' : 'Local only &mdash; register later from Dashboard'}</div>
          </div>
        </div>

        <h4 style="margin-bottom:0.75rem">Step 1: Save your backup</h4>
        <div class="btn-group" style="margin-bottom:1.25rem">
          <button class="btn btn-primary" data-setup-action="download-backup">Download Key Backup</button>
          <button class="btn" data-setup-action="copy-seed">Copy Seed to Clipboard</button>
        </div>

        <div class="seed-display-wrapper" id="seed-display-wrapper" style="margin-bottom:1.25rem">
          <label style="font-size:0.85rem;color:var(--text-muted);display:block;margin-bottom:0.3rem">Your seed (click to reveal)</label>
          <div class="seed-masked" id="seed-display" data-setup-action="toggle-seed">Click to reveal seed</div>
        </div>

        <h4 style="margin-bottom:0.5rem">Step 2: Verify your backup</h4>
        <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:0.5rem">
          Enter the <strong>last 8 characters</strong> of your seed to confirm you saved it:
        </p>
        <div class="verify-input-row">
          <input type="text" id="backup-verify" class="verify-input" maxlength="8" placeholder="Last 8 hex chars">
          <span id="verify-status" class="verify-status"></span>
        </div>
        <div id="verify-error" style="color:var(--red);font-size:0.8rem;margin-top:0.3rem;min-height:1.2em"></div>

        <div class="btn-group" style="margin-top:1.25rem">
          <button class="btn btn-primary" id="btn-continue" disabled data-setup-action="continue-dashboard">Continue to Dashboard</button>
        </div>
      </div>
    `);
    bindSetupActions();
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
    const encrypted = KredoStorage.isEncrypted();
    const backupLabel = encrypted ? 'Download Encrypted Backup' : 'Download Key Backup';
    const atlasCompat = isAtlasBrowser();
    const atlasPbkdf2Blocked = atlasCompat && encrypted && id && id.encrypted && id.encrypted.kdf === 'pbkdf2-sha256';
    KredoUI.renderView(`
      <h1 class="page-title">Identity Setup</h1>

      ${atlasPbkdf2Blocked ? `
      <div class="warning-banner" style="margin-bottom:1rem">
        <strong>Atlas compatibility warning:</strong> this key uses PBKDF2 encryption and Atlas cannot safely unlock it.
        Use Chrome/Safari for signing, or re-import a compatibility-encrypted backup in Atlas.
      </div>
      ` : ''}

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
              <div class="key-display" data-setup-action="copy-pubkey" data-pubkey="${KredoUI.escapeHtml(id.pubkey)}" title="Click to copy">${KredoUI.escapeHtml(id.pubkey)}</div>
            </div>
          </div>
          <div class="review-row">
            <div class="review-key">Encryption</div>
            <div class="review-val">${encrypted ? 'Private key is passphrase-encrypted' : 'Private key stored in plaintext'}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Storage Origin</div>
            <div class="review-val">${KredoUI.escapeHtml(window.location.origin)}</div>
          </div>
        </div>

        <div class="btn-group" style="margin-top:1rem">
          <button class="btn" data-setup-action="download-backup">${backupLabel}</button>
          <button class="btn" data-setup-action="copy-pubkey" data-pubkey="${KredoUI.escapeHtml(id.pubkey)}">Copy Public Key</button>
          <button class="btn" data-setup-action="show-encrypt-panel" ${encrypted ? 'disabled' : ''}>${encrypted ? 'Key Encrypted' : 'Encrypt Current Key'}</button>
          <a class="btn" href="#/setup/create">Create New Identity</a>
          <button class="btn btn-primary" data-setup-action="show-recover">Load Different Identity (Login)</button>
          <button class="btn btn-danger" data-setup-action="confirm-reset">Reset Identity</button>
        </div>

        ${encrypted ? `
        <p style="margin-top:0.75rem;color:var(--text-muted);font-size:0.85rem">
          Backup export uses encrypted format. Importing it on another browser requires your passphrase.
        </p>
        ` : ''}

        ${encrypted ? '' : `
        <div id="encrypt-key-panel" class="card" style="margin-top:1rem;display:none">
          <div class="card-header">Encrypt Current Key</div>
          <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:0.75rem">
            This secures your existing local key with a passphrase. Your public key stays the same.
          </p>
          <div class="form-group">
            <label for="encrypt-passphrase">Passphrase</label>
            <input type="password" id="encrypt-passphrase" placeholder="Minimum 8 characters" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
          </div>
          ${atlasCompat ? `
          <div class="form-group">
            <label for="encrypt-confirm-token">Type ENCRYPT to confirm</label>
            <input type="text" id="encrypt-confirm-token" placeholder="ENCRYPT" autocomplete="off" autocorrect="off" autocapitalize="characters" spellcheck="false">
            <div class="hint">Atlas compatibility mode: using token confirmation to avoid second-password-field browser freeze.</div>
          </div>
          ` : `
          <div class="form-group">
            <label for="encrypt-passphrase-confirm">Confirm Passphrase</label>
            <input type="password" id="encrypt-passphrase-confirm" placeholder="Re-enter passphrase" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
          </div>
          `}
          <div class="btn-group">
            <button class="btn btn-primary" data-setup-action="apply-encrypt-key">Apply Encryption</button>
            <button class="btn" data-setup-action="hide-encrypt-panel">Cancel</button>
          </div>
        </div>
        `}
      </div>
    `);
    bindSetupActions();
  }

  function bindSetupActions() {
    const root = document.getElementById('view');
    if (!root) return;

    const setupForm = root.querySelector('#setup-form');
    if (setupForm && !setupForm.dataset.bound) {
      setupForm.dataset.bound = '1';
      setupForm.addEventListener('submit', handleCreate);
    }

    const importFile = root.querySelector('#import-file');
    if (importFile && !importFile.dataset.bound) {
      importFile.dataset.bound = '1';
      importFile.addEventListener('change', handleImport);
    }

    const verifyInput = root.querySelector('#backup-verify');
    if (verifyInput && !verifyInput.dataset.bound) {
      verifyInput.dataset.bound = '1';
      verifyInput.addEventListener('input', checkVerification);
    }

    root.querySelectorAll('[data-setup-action]').forEach((el) => {
      if (el.dataset.bound) return;
      el.dataset.bound = '1';
      el.addEventListener('click', (event) => {
        const action = el.dataset.setupAction;
        switch (action) {
          case 'show-create':
            event.preventDefault();
            showCreate();
            break;
          case 'show-recover':
            event.preventDefault();
            showRecover();
            break;
          case 'show-landing':
            event.preventDefault();
            showLanding();
            break;
          case 'set-type':
            event.preventDefault();
            setType(el.dataset.type || 'human');
            break;
          case 'trigger-import':
            event.preventDefault();
            triggerImport();
            break;
          case 'show-paste-seed':
            event.preventDefault();
            showPasteSeed();
            break;
          case 'seed-import':
            event.preventDefault();
            handleSeedImport();
            break;
          case 'download-backup':
            event.preventDefault();
            downloadBackup();
            break;
          case 'copy-seed':
            event.preventDefault();
            copySeed();
            break;
          case 'toggle-seed':
            event.preventDefault();
            toggleSeedReveal();
            break;
          case 'continue-dashboard':
            event.preventDefault();
            continueToDashboard();
            break;
          case 'confirm-reset':
            event.preventDefault();
            confirmReset();
            break;
          case 'copy-pubkey':
            event.preventDefault();
            if (el.dataset.pubkey) {
              KredoUI.copyToClipboard(el.dataset.pubkey, 'Public key');
            }
            break;
          case 'show-encrypt-panel':
            event.preventDefault();
            showEncryptPanel();
            break;
          case 'hide-encrypt-panel':
            event.preventDefault();
            hideEncryptPanel();
            break;
          case 'apply-encrypt-key':
            event.preventDefault();
            encryptCurrentKeyFromForm();
            break;
          default:
            break;
        }
      });
    });
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
      const prompted = await KredoUI.requestPassphrase(
        'Set a passphrase to encrypt your private key (recommended). Leave blank to continue without encryption.',
        { allowBlank: true, submitLabel: 'Continue' },
      );
      if (prompted === null) {
        KredoUI.showInfo('Identity creation cancelled.');
        return;
      }
      passphrase = prompted.trim();
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

    const prompted = await KredoUI.requestPassphrase(
      'Set a passphrase to encrypt this imported key (recommended). Leave blank to continue without encryption.',
      { allowBlank: true, submitLabel: 'Continue' },
    );
    if (prompted === null) {
      KredoUI.showInfo('Seed import cancelled.');
      return;
    }
    let passphrase = prompted.trim();
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

    if (id.encrypted) {
      KredoStorage.downloadIdentity();
      KredoUI.showSuccess('Encrypted backup downloaded.');
    } else {
      const allowPlaintext = confirm(
        'This backup will contain an unencrypted private key.\n\nStore it only in a secure password manager or encrypted vault.\n\nContinue?'
      );
      if (!allowPlaintext) return;
      KredoStorage.downloadIdentity();
      KredoUI.showWarning('Plaintext backup downloaded. Encrypt this identity in Setup for safer backups.');
    }
  }

  function showEncryptPanel() {
    const panel = document.getElementById('encrypt-key-panel');
    if (!panel) return;
    panel.style.display = 'block';
    const input = document.getElementById('encrypt-passphrase');
    if (input) input.focus();
  }

  function hideEncryptPanel() {
    const panel = document.getElementById('encrypt-key-panel');
    if (panel) panel.style.display = 'none';
    const p1 = document.getElementById('encrypt-passphrase');
    const p2 = document.getElementById('encrypt-passphrase-confirm');
    const token = document.getElementById('encrypt-confirm-token');
    if (p1) p1.value = '';
    if (p2) p2.value = '';
    if (token) token.value = '';
  }

  async function encryptCurrentKeyFromForm() {
    const p1 = document.getElementById('encrypt-passphrase');
    const p2 = document.getElementById('encrypt-passphrase-confirm');
    const passphrase = p1 ? p1.value.trim() : '';
    let confirmPassphrase = p2 ? p2.value.trim() : '';
    if (isAtlasBrowser()) {
      const token = document.getElementById('encrypt-confirm-token');
      const value = token ? token.value.trim().toUpperCase() : '';
      if (value !== 'ENCRYPT') {
        KredoUI.showError('Type ENCRYPT to confirm key encryption.');
        return;
      }
      confirmPassphrase = passphrase;
    }
    await encryptCurrentKey(passphrase, confirmPassphrase);
  }

  async function encryptCurrentKey(passphrase, confirmPassphrase) {
    const id = KredoStorage.getIdentity();
    if (!id) {
      KredoUI.showError('No identity loaded');
      return;
    }
    if (id.encrypted) {
      KredoUI.showInfo('Key is already passphrase-encrypted.');
      return;
    }
    if (!id.secretKey) {
      KredoUI.showError('No local secret key found. Load Identity (Login) again.');
      return;
    }
    if (passphrase.length < 8) {
      KredoUI.showError('Passphrase must be at least 8 characters');
      return;
    }
    if (confirmPassphrase !== passphrase) {
      KredoUI.showError('Passphrases do not match');
      return;
    }

    const applyBtn = document.querySelector('[data-setup-action="apply-encrypt-key"]');
    const cancelBtn = document.querySelector('[data-setup-action="hide-encrypt-panel"]');
    const previousApplyText = applyBtn ? applyBtn.textContent : '';
    if (applyBtn) {
      applyBtn.textContent = 'Encrypting...';
      applyBtn.disabled = true;
    }
    if (cancelBtn) cancelBtn.disabled = true;

    try {
      await Promise.race([
        KredoStorage.saveIdentity(id.name, id.type, id.pubkey, id.secretKey, passphrase),
        new Promise((_, reject) => setTimeout(() => reject(new Error('Encryption timed out in this browser. Try again or use Safari/Chrome.')), ENCRYPT_ACTION_TIMEOUT_MS)),
      ]);
      const verify = KredoStorage.getIdentity();
      if (!verify || !verify.encrypted || verify.secretKey) {
        throw new Error('Encryption verification failed. Storage still appears plaintext.');
      }
      KredoApp.updateIdentityStatus();
      if (verify.encrypted.kdf && verify.encrypted.kdf !== 'pbkdf2-sha256') {
        KredoUI.showWarning('Encrypted with browser compatibility mode (not PBKDF2). Prefer Safari/Chrome for strongest local KDF.');
      }
      KredoUI.showSuccess('Key encrypted successfully. Download a fresh backup file next.');
      renderExisting();
    } catch (err) {
      KredoUI.showError('Failed to encrypt key: ' + err.message);
      if (applyBtn) {
        applyBtn.textContent = previousApplyText || 'Apply Encryption';
        applyBtn.disabled = false;
      }
      if (cancelBtn) cancelBtn.disabled = false;
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
    encryptCurrentKeyFromForm,
    encryptCurrentKey,
    confirmReset,
    toggleSeedReveal,
    copySeed,
    checkVerification,
    continueToDashboard,
  };
})();
