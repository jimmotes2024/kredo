/**
 * Setup View — Identity creation (mirrors kredo init)
 *
 * Flow: Name → Type → Generate keypair → Store → Register with API → Done
 */

const SetupView = (() => {
  let state = {
    step: 'form', // form | generated | registered
    keypair: null,
    name: '',
    type: 'human',
  };

  function render() {
    if (KredoStorage.hasIdentity()) {
      renderExisting();
    } else {
      renderForm();
    }
  }

  function renderForm() {
    state = { step: 'form', keypair: null, name: '', type: 'human' };

    KredoUI.renderView(`
      <h1 class="page-title">Welcome to Kredo</h1>
      <p class="page-subtitle">Create your identity to start attesting agent and human work. Your Ed25519 keypair is generated locally in your browser — your private key never leaves this device.</p>

      <div class="card">
        <div class="card-header">Create Identity</div>
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
          </div>
        </form>
      </div>

      <div class="card">
        <div class="card-header">Import Existing Identity</div>
        <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:0.75rem">Have a key backup file from a previous session?</p>
        <div class="btn-group">
          <button class="btn" onclick="SetupView.triggerImport()">Upload Key File</button>
          <button class="btn" onclick="SetupView.showPasteImport()">Paste Seed</button>
        </div>
        <input type="file" id="import-file" accept=".json" style="display:none" onchange="SetupView.handleImport(event)">
        <div id="paste-import" style="display:none;margin-top:0.75rem">
          <div class="form-group">
            <label for="import-seed">64-character hex seed</label>
            <input type="text" id="import-seed" placeholder="Paste your 64-hex-char seed...">
          </div>
          <div class="form-group">
            <label for="import-name">Name</label>
            <input type="text" id="import-name" placeholder="Your name" required>
          </div>
          <button class="btn btn-primary" onclick="SetupView.handleSeedImport()">Import from Seed</button>
        </div>
      </div>
    `);
  }

  function renderExisting() {
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

  function renderSuccess(name, pubkey, registered) {
    KredoUI.renderView(`
      <h1 class="page-title">Identity Created</h1>

      <div class="card" style="border-color:var(--green)">
        <div class="card-header" style="color:var(--green)">Your Kredo identity is ready</div>
        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">Name</div>
            <div class="review-val">${KredoUI.escapeHtml(name)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Public Key</div>
            <div class="review-val">
              <div class="key-display" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(pubkey)}', 'Public key')" title="Click to copy">${KredoUI.escapeHtml(pubkey)}</div>
            </div>
          </div>
          <div class="review-row">
            <div class="review-key">Registered</div>
            <div class="review-val">${registered ? 'Yes — visible on the Kredo network' : 'Local only — register later from Dashboard'}</div>
          </div>
        </div>

        <div class="btn-group" style="margin-top:1rem">
          <button class="btn btn-primary" onclick="SetupView.downloadBackup()">Download Key Backup</button>
          <button class="btn" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(pubkey)}', 'Public key')">Copy Public Key</button>
        </div>

        <div style="margin-top:1rem;padding:0.75rem;background:var(--bg);border-radius:var(--radius);border:1px solid var(--yellow);font-size:0.85rem;color:var(--yellow)">
          <strong>Important:</strong> Download your key backup now. Your private key is stored in this browser's LocalStorage. If you clear browser data, it will be lost forever.
        </div>

        <div style="margin-top:1rem">
          <h4 style="margin-bottom:0.5rem">Next steps</h4>
          <ul style="color:var(--text-muted);font-size:0.9rem;padding-left:1.2rem">
            <li><a href="#/attest">Create your first attestation</a> — vouch for someone's work</li>
            <li><a href="#/browse">Browse agents</a> — see who's on the network</li>
            <li><a href="#/dashboard">View your dashboard</a> — see your network profile</li>
          </ul>
        </div>
      </div>
    `);
  }

  // --- Event handlers ---

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
        "Warning: your private key will be stored in plaintext in this browser's localStorage.\\n\\nContinue without encryption?"
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
    renderSuccess(name, keypair.publicKey, registered);
  }

  function triggerImport() {
    document.getElementById('import-file').click();
  }

  function showPasteImport() {
    const el = document.getElementById('paste-import');
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
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
            'This backup contains an unencrypted private key. Importing it will store the key in plaintext in localStorage.\\n\\nContinue?'
          );
          if (!allowPlaintext) {
            return;
          }
        }
        KredoStorage.importIdentity(data);
        KredoApp.updateIdentityStatus();
        KredoUI.showSuccess('Identity imported successfully');
        renderExisting();
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
        'Warning: imported private key will be stored in plaintext in localStorage.\\n\\nContinue without encryption?'
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
      renderExisting();
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
      renderForm();
    }
  }

  return {
    render,
    setType,
    handleCreate,
    triggerImport,
    showPasteImport,
    handleImport,
    handleSeedImport,
    downloadBackup,
    confirmReset,
  };
})();
