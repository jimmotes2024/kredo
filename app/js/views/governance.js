/**
 * Governance View — accountability and integrity workflows.
 *
 * Simplifies signed operations for:
 * - registration updates
 * - ownership claim/confirm/revoke
 * - integrity baseline/check/status
 * - source anomaly review
 */

const GovernanceView = (() => {
  let ownershipCache = null;

  function render() {
    const identity = KredoStorage.getIdentity();
    if (!identity) {
      KredoUI.renderView('<div class="empty-state">No identity found. <a href="#/setup">Create one</a> first.</div>');
      return;
    }

    const defaultAgentPubkey = identity.type === 'agent' ? identity.pubkey : '';
    const defaultOwnerPubkey = identity.type === 'human' ? identity.pubkey : '';

    KredoUI.renderView(`
      <h1 class="page-title">Governance &amp; Integrity</h1>
      <p class="page-subtitle">Run signed accountability and integrity actions without raw API payload editing.</p>

      <div class="card">
        <div class="card-header">Signed Registration Update</div>
        <p class="gov-help">Use your current key to update display metadata in the network registry.</p>
        <div class="form-row">
          <div class="form-group">
            <label for="gov-name">Display Name</label>
            <input type="text" id="gov-name" value="${KredoUI.escapeHtml(identity.name || '')}" maxlength="120">
          </div>
          <div class="form-group">
            <label for="gov-type">Identity Type</label>
            <select id="gov-type">
              <option value="human" ${identity.type === 'human' ? 'selected' : ''}>human</option>
              <option value="agent" ${identity.type === 'agent' ? 'selected' : ''}>agent</option>
            </select>
          </div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="GovernanceView.updateRegistration()">Sign &amp; Update Registration</button>
        </div>
      </div>

      <div class="card">
        <div class="card-header">Ownership Linking</div>
        <p class="gov-help">Agent submits claim. Human confirms claim. Either side can later revoke with reason.</p>

        <div class="form-row">
          <div class="form-group">
            <label for="own-agent-key">Agent Pubkey</label>
            <input type="text" id="own-agent-key" value="${KredoUI.escapeHtml(defaultAgentPubkey)}" placeholder="ed25519:...">
          </div>
          <div class="form-group">
            <label for="own-lookup-history">Include History</label>
            <select id="own-lookup-history">
              <option value="true" selected>true</option>
              <option value="false">false</option>
            </select>
          </div>
        </div>
        <div class="btn-group" style="margin-bottom:0.75rem">
          <button class="btn" onclick="GovernanceView.lookupOwnership()">Lookup Ownership</button>
        </div>
        <div id="own-summary" class="review-panel gov-panel-muted">No ownership data loaded yet.</div>

        <div class="gov-split" style="margin-top:1rem">
          <div class="gov-subcard">
            <h3>1) Agent Claim</h3>
            <div class="form-group">
              <label for="own-claim-id">Claim ID (optional)</label>
              <input type="text" id="own-claim-id" placeholder="own-xxxxxxxxxxxxxxxxxxxxxxxx">
            </div>
            <div class="form-group">
              <label for="own-human-key">Human Pubkey</label>
              <input type="text" id="own-human-key" placeholder="ed25519:...">
            </div>
            <button class="btn btn-primary" onclick="GovernanceView.createOwnershipClaim()">Sign &amp; Create Claim</button>
          </div>

          <div class="gov-subcard">
            <h3>2) Human Confirm</h3>
            <div class="form-group">
              <label for="own-confirm-claim-id">Claim ID</label>
              <input type="text" id="own-confirm-claim-id" placeholder="own-...">
            </div>
            <div class="form-group">
              <label for="own-confirm-agent-key">Agent Pubkey</label>
              <input type="text" id="own-confirm-agent-key" value="${KredoUI.escapeHtml(defaultAgentPubkey)}" placeholder="ed25519:...">
            </div>
            <div class="form-group">
              <label for="own-confirm-human-key">Human Pubkey</label>
              <input type="text" id="own-confirm-human-key" value="${KredoUI.escapeHtml(defaultOwnerPubkey)}" placeholder="ed25519:...">
            </div>
            <div class="form-group">
              <label for="own-contact-email">Contact Email (optional)</label>
              <input type="email" id="own-contact-email" placeholder="owner@example.com">
            </div>
            <button class="btn btn-primary" onclick="GovernanceView.confirmOwnershipClaim()">Sign &amp; Confirm Claim</button>
          </div>

          <div class="gov-subcard">
            <h3>3) Revoke Claim</h3>
            <div class="form-group">
              <label for="own-revoke-claim-id">Claim ID</label>
              <input type="text" id="own-revoke-claim-id" placeholder="own-...">
            </div>
            <div class="form-group">
              <label for="own-revoke-agent-key">Agent Pubkey</label>
              <input type="text" id="own-revoke-agent-key" value="${KredoUI.escapeHtml(defaultAgentPubkey)}" placeholder="ed25519:...">
            </div>
            <div class="form-group">
              <label for="own-revoke-human-key">Human Pubkey</label>
              <input type="text" id="own-revoke-human-key" value="${KredoUI.escapeHtml(defaultOwnerPubkey)}" placeholder="ed25519:...">
            </div>
            <div class="form-group">
              <label for="own-revoke-reason">Reason</label>
              <textarea id="own-revoke-reason" rows="3" placeholder="Why this claim should be revoked."></textarea>
            </div>
            <button class="btn btn-danger" onclick="GovernanceView.revokeOwnershipClaim()">Sign &amp; Revoke Claim</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">Integrity Run-Gate</div>
        <p class="gov-help">Owner signs baseline. Agent signs runtime check. Runtime reads traffic-light status.</p>
        <div class="form-row">
          <div class="form-group">
            <label for="int-agent-key">Agent Pubkey</label>
            <input type="text" id="int-agent-key" value="${KredoUI.escapeHtml(defaultAgentPubkey)}" placeholder="ed25519:...">
          </div>
          <div class="form-group">
            <label for="int-owner-key">Owner Pubkey</label>
            <input type="text" id="int-owner-key" value="${KredoUI.escapeHtml(defaultOwnerPubkey)}" placeholder="ed25519:...">
          </div>
        </div>
        <div class="form-group">
          <label for="int-baseline-id">Baseline ID (optional)</label>
          <input type="text" id="int-baseline-id" placeholder="bl-xxxxxxxxxxxxxxxxxxxxxxxx">
        </div>
        <div class="form-group">
          <label for="int-manifest">Manifest (path=sha256, one per line)</label>
          <textarea id="int-manifest" rows="8" placeholder="SOUL.md=8f2c...\nmemory/journal.md=21a4...\nconfig/agent.yml=5bd1..."></textarea>
          <div class="hint">Accepted separators: <code>=</code> or <code>,</code>. SHA256 must be 64 lowercase hex chars.</div>
        </div>
        <div class="btn-group">
          <button class="btn" onclick="GovernanceView.loadIntegrityStatus()">Read Status</button>
          <button class="btn btn-primary" onclick="GovernanceView.setIntegrityBaseline()">Sign &amp; Set Baseline</button>
          <button class="btn btn-primary" onclick="GovernanceView.runIntegrityCheck()">Sign &amp; Run Check</button>
        </div>
        <div id="int-status" class="review-panel gov-panel-muted" style="margin-top:0.75rem">No integrity status loaded yet.</div>
      </div>

      <div class="card">
        <div class="card-header">Source Risk Signals</div>
        <p class="gov-help">Review unusual source concentration patterns in write-path activity.</p>
        <div class="form-row">
          <div class="form-group">
            <label for="risk-hours">Hours</label>
            <input type="number" id="risk-hours" value="24" min="1" max="720">
          </div>
          <div class="form-group">
            <label for="risk-min-events">Min Events</label>
            <input type="number" id="risk-min-events" value="8" min="1" max="1000">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label for="risk-min-actors">Min Unique Actors</label>
            <input type="number" id="risk-min-actors" value="4" min="1" max="1000">
          </div>
          <div class="form-group">
            <label for="risk-limit">Limit</label>
            <input type="number" id="risk-limit" value="50" min="1" max="500">
          </div>
        </div>
        <div class="btn-group">
          <button class="btn" onclick="GovernanceView.loadSourceAnomalies()">Refresh Source Signals</button>
        </div>
        <div id="risk-result" style="margin-top:0.75rem"></div>
      </div>
    `);

    if (defaultAgentPubkey) {
      lookupOwnership();
      loadIntegrityStatus();
    }
    loadSourceAnomalies();
  }

  function _isPubkey(value) {
    return /^ed25519:[0-9a-f]{64}$/.test((value || '').trim().toLowerCase());
  }

  function _normalizePubkey(value) {
    return (value || '').trim().toLowerCase();
  }

  function _normalizeClaimId(value, prefix = 'own') {
    const cleaned = (value || '').trim();
    if (cleaned) return cleaned;
    return `${prefix}-${KredoCrypto.uuid4().replace(/-/g, '').slice(0, 24)}`;
  }

  async function _resolveSigningContext(promptLabel) {
    const identity = KredoStorage.getIdentity();
    if (!identity) {
      KredoUI.showError('No local identity found.');
      return null;
    }

    let secretKey;
    if (KredoStorage.isEncrypted()) {
      const passphrase = prompt(`Enter passphrase to sign ${promptLabel}:`);
      if (!passphrase) return null;
      secretKey = await KredoStorage.getSecretKey(passphrase);
      if (!secretKey) {
        KredoUI.showError('Wrong passphrase.');
        return null;
      }
    } else {
      secretKey = await KredoStorage.getSecretKey();
      if (!secretKey) {
        KredoUI.showError('Secret key not available in local storage.');
        return null;
      }
    }

    return { identity, secretKey };
  }

  function _parseManifestFromInput(raw) {
    const lines = (raw || '').split('\n');
    const map = new Map();

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;

      let splitIndex = trimmed.indexOf('=');
      if (splitIndex < 0) splitIndex = trimmed.indexOf(',');
      if (splitIndex < 0) {
        throw new Error(`Manifest line must be "path=sha256" or "path,sha256": ${trimmed}`);
      }

      const path = trimmed.slice(0, splitIndex).trim();
      const sha256 = trimmed.slice(splitIndex + 1).trim().toLowerCase();
      if (!path) {
        throw new Error(`Manifest path missing in line: ${trimmed}`);
      }
      if (!/^[0-9a-f]{64}$/.test(sha256)) {
        throw new Error(`Invalid sha256 (must be 64 lowercase hex): ${trimmed}`);
      }
      if (map.has(path)) {
        throw new Error(`Duplicate manifest path: ${path}`);
      }
      map.set(path, sha256);
    }

    const entries = Array.from(map.entries()).map(([path, sha256]) => ({ path, sha256 }));
    entries.sort((a, b) => a.path.localeCompare(b.path));
    if (!entries.length) {
      throw new Error('Manifest is empty. Add at least one path hash.');
    }
    return entries;
  }

  async function updateRegistration() {
    const signCtx = await _resolveSigningContext('registration update');
    if (!signCtx) return;

    const name = document.getElementById('gov-name').value.trim();
    const type = document.getElementById('gov-type').value;
    if (!name) {
      KredoUI.showError('Display name is required.');
      return;
    }
    if (!['human', 'agent'].includes(type)) {
      KredoUI.showError('Identity type must be human or agent.');
      return;
    }

    const payload = {
      action: 'update_registration',
      pubkey: signCtx.identity.pubkey,
      name,
      type,
    };
    const signature = KredoCrypto.sign(payload, signCtx.secretKey);

    try {
      await KredoAPI.updateRegistration(signCtx.identity.pubkey, name, type, signature);
      KredoStorage.importIdentity({ ...signCtx.identity, name, type });
      KredoApp.updateIdentityStatus();
      KredoUI.showSuccess('Registration metadata updated.');
    } catch (err) {
      KredoUI.showError('Update failed: ' + err.message);
    }
  }

  async function lookupOwnership() {
    const agentPubkey = _normalizePubkey(document.getElementById('own-agent-key').value);
    const includeHistory = document.getElementById('own-lookup-history').value === 'true';
    const out = document.getElementById('own-summary');

    if (!_isPubkey(agentPubkey)) {
      KredoUI.showError('Enter a valid agent pubkey first.');
      return;
    }

    KredoUI.showLoading(out);
    try {
      const result = await KredoAPI.ownershipForAgent(agentPubkey, includeHistory);
      ownershipCache = result;
      renderOwnershipSummary(result);
      const intAgent = document.getElementById('int-agent-key');
      if (intAgent && !intAgent.value.trim()) intAgent.value = agentPubkey;
    } catch (err) {
      out.innerHTML = `<p style="color:var(--red)">${KredoUI.escapeHtml(err.message)}</p>`;
    }
  }

  function renderOwnershipSummary(data) {
    const out = document.getElementById('own-summary');
    if (!out) return;

    const active = data.active_owner;
    const claims = data.claims || [];

    let html = `
      <div class="review-row"><div class="review-key">Agent</div><div class="review-val">${KredoUI.shortKey(data.agent_pubkey)}</div></div>
      <div class="review-row"><div class="review-key">Active Owner</div><div class="review-val">${active ? KredoUI.shortKey(active.human_pubkey) : '<span style="color:var(--yellow)">none</span>'}</div></div>
      <div class="review-row"><div class="review-key">Status</div><div class="review-val">${active ? '<span class="gov-pill gov-pill-green">human-linked</span>' : '<span class="gov-pill gov-pill-yellow">unlinked</span>'}</div></div>
    `;

    if (!claims.length) {
      html += `<div class="review-row"><div class="review-key">Claims</div><div class="review-val">No claims found.</div></div>`;
      out.innerHTML = html;
      return;
    }

    html += '<div style="margin-top:0.75rem">';
    html += KredoUI.buildTable(
      [
        { label: 'Claim ID', render: row => `<code>${KredoUI.escapeHtml(row.id)}</code>` },
        { label: 'Human', render: row => KredoUI.shortKey(row.human_pubkey) },
        { label: 'Status', key: 'status' },
        { label: 'Claimed', render: row => KredoUI.formatDateTime(row.claimed_at) },
        { label: 'Use', render: row => `<button class="btn btn-sm" onclick="GovernanceView.useClaimId('${KredoUI.escapeHtml(row.id)}')">Use ID</button>` },
      ],
      claims,
      { emptyMessage: 'No ownership claims.' }
    );
    html += '</div>';

    out.innerHTML = html;

    if (active) {
      const ownerInput = document.getElementById('int-owner-key');
      if (ownerInput && !ownerInput.value.trim()) {
        ownerInput.value = active.human_pubkey;
      }
    }
  }

  function useClaimId(claimId) {
    const claim = (ownershipCache?.claims || []).find(c => c.id === claimId);
    if (!claim) return;

    const fill = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = value || '';
    };

    fill('own-confirm-claim-id', claim.id);
    fill('own-confirm-agent-key', claim.agent_pubkey);
    fill('own-confirm-human-key', claim.human_pubkey);
    fill('own-revoke-claim-id', claim.id);
    fill('own-revoke-agent-key', claim.agent_pubkey);
    fill('own-revoke-human-key', claim.human_pubkey);
    KredoUI.showInfo('Claim ID loaded into confirm/revoke forms.');
  }

  async function createOwnershipClaim() {
    const signCtx = await _resolveSigningContext('ownership claim');
    if (!signCtx) return;

    const agentPubkey = _normalizePubkey(document.getElementById('own-agent-key').value);
    const humanPubkey = _normalizePubkey(document.getElementById('own-human-key').value);
    const claimId = _normalizeClaimId(document.getElementById('own-claim-id').value, 'own');

    if (!_isPubkey(agentPubkey) || !_isPubkey(humanPubkey)) {
      KredoUI.showError('Agent and human pubkeys must both be valid ed25519 keys.');
      return;
    }
    if (signCtx.identity.pubkey !== agentPubkey) {
      KredoUI.showError('Ownership claim must be signed by the same agent key listed in Agent Pubkey.');
      return;
    }

    const payload = {
      action: 'ownership_claim',
      claim_id: claimId,
      agent_pubkey: agentPubkey,
      human_pubkey: humanPubkey,
    };
    const signature = KredoCrypto.sign(payload, signCtx.secretKey);

    try {
      const result = await KredoAPI.ownershipClaim(claimId, agentPubkey, humanPubkey, signature);
      KredoUI.showSuccess(`Ownership claim created: ${result.claim_id}`);
      document.getElementById('own-claim-id').value = result.claim_id;
      document.getElementById('own-confirm-claim-id').value = result.claim_id;
      document.getElementById('own-confirm-agent-key').value = agentPubkey;
      document.getElementById('own-confirm-human-key').value = humanPubkey;
      lookupOwnership();
    } catch (err) {
      KredoUI.showError('Claim failed: ' + err.message);
    }
  }

  async function confirmOwnershipClaim() {
    const signCtx = await _resolveSigningContext('ownership confirmation');
    if (!signCtx) return;

    const claimId = document.getElementById('own-confirm-claim-id').value.trim();
    const agentPubkey = _normalizePubkey(document.getElementById('own-confirm-agent-key').value);
    const humanPubkey = _normalizePubkey(document.getElementById('own-confirm-human-key').value);
    const contactEmail = document.getElementById('own-contact-email').value.trim();

    if (!claimId) {
      KredoUI.showError('Claim ID is required.');
      return;
    }
    if (!_isPubkey(agentPubkey) || !_isPubkey(humanPubkey)) {
      KredoUI.showError('Agent and human pubkeys must both be valid ed25519 keys.');
      return;
    }
    if (signCtx.identity.pubkey !== humanPubkey) {
      KredoUI.showError('Ownership confirmation must be signed by the same human key listed in Human Pubkey.');
      return;
    }

    const payload = {
      action: 'ownership_confirm',
      claim_id: claimId,
      agent_pubkey: agentPubkey,
      human_pubkey: humanPubkey,
    };
    const signature = KredoCrypto.sign(payload, signCtx.secretKey);

    try {
      const result = await KredoAPI.ownershipConfirm(claimId, humanPubkey, signature, contactEmail || undefined);
      KredoUI.showSuccess(`Ownership claim activated: ${result.claim_id}`);
      document.getElementById('own-agent-key').value = agentPubkey;
      lookupOwnership();
    } catch (err) {
      KredoUI.showError('Confirmation failed: ' + err.message);
    }
  }

  async function revokeOwnershipClaim() {
    const signCtx = await _resolveSigningContext('ownership revocation');
    if (!signCtx) return;

    const claimId = document.getElementById('own-revoke-claim-id').value.trim();
    const agentPubkey = _normalizePubkey(document.getElementById('own-revoke-agent-key').value);
    const humanPubkey = _normalizePubkey(document.getElementById('own-revoke-human-key').value);
    const reason = document.getElementById('own-revoke-reason').value.trim();
    const revokerPubkey = signCtx.identity.pubkey;

    if (!claimId) {
      KredoUI.showError('Claim ID is required.');
      return;
    }
    if (!_isPubkey(agentPubkey) || !_isPubkey(humanPubkey)) {
      KredoUI.showError('Agent and human pubkeys must both be valid ed25519 keys.');
      return;
    }
    if (revokerPubkey !== agentPubkey && revokerPubkey !== humanPubkey) {
      KredoUI.showError('Revocation must be signed by either the linked agent key or human owner key.');
      return;
    }
    if (reason.length < 8) {
      KredoUI.showError('Reason must be at least 8 characters.');
      return;
    }

    const payload = {
      action: 'ownership_revoke',
      claim_id: claimId,
      agent_pubkey: agentPubkey,
      human_pubkey: humanPubkey,
      revoker_pubkey: revokerPubkey,
      reason,
    };
    const signature = KredoCrypto.sign(payload, signCtx.secretKey);

    try {
      await KredoAPI.ownershipRevoke(claimId, revokerPubkey, reason, signature);
      KredoUI.showSuccess(`Ownership claim revoked: ${claimId}`);
      document.getElementById('own-agent-key').value = agentPubkey;
      lookupOwnership();
    } catch (err) {
      KredoUI.showError('Revocation failed: ' + err.message);
    }
  }

  async function setIntegrityBaseline() {
    const signCtx = await _resolveSigningContext('integrity baseline set');
    if (!signCtx) return;

    const baselineId = _normalizeClaimId(document.getElementById('int-baseline-id').value, 'bl');
    const agentPubkey = _normalizePubkey(document.getElementById('int-agent-key').value);
    const ownerPubkey = _normalizePubkey(document.getElementById('int-owner-key').value);
    const manifestRaw = document.getElementById('int-manifest').value;

    if (!_isPubkey(agentPubkey) || !_isPubkey(ownerPubkey)) {
      KredoUI.showError('Agent and owner pubkeys must both be valid ed25519 keys.');
      return;
    }
    if (signCtx.identity.pubkey !== ownerPubkey) {
      KredoUI.showError('Baseline set must be signed by the owner key listed in Owner Pubkey.');
      return;
    }

    let fileHashes;
    try {
      fileHashes = _parseManifestFromInput(manifestRaw);
    } catch (err) {
      KredoUI.showError(err.message);
      return;
    }

    const payload = {
      action: 'integrity_set_baseline',
      baseline_id: baselineId,
      agent_pubkey: agentPubkey,
      owner_pubkey: ownerPubkey,
      file_hashes: fileHashes,
    };
    const signature = KredoCrypto.sign(payload, signCtx.secretKey);

    try {
      const result = await KredoAPI.setIntegrityBaseline(baselineId, agentPubkey, ownerPubkey, fileHashes, signature);
      KredoUI.showSuccess(`Baseline set: ${result.baseline_id}`);
      document.getElementById('int-baseline-id').value = result.baseline_id;
      loadIntegrityStatus();
    } catch (err) {
      KredoUI.showError('Baseline set failed: ' + err.message);
    }
  }

  async function runIntegrityCheck() {
    const signCtx = await _resolveSigningContext('integrity check');
    if (!signCtx) return;

    const agentPubkey = _normalizePubkey(document.getElementById('int-agent-key').value);
    const manifestRaw = document.getElementById('int-manifest').value;

    if (!_isPubkey(agentPubkey)) {
      KredoUI.showError('Enter a valid agent pubkey.');
      return;
    }
    if (signCtx.identity.pubkey !== agentPubkey) {
      KredoUI.showError('Integrity check must be signed by the same agent key listed in Agent Pubkey.');
      return;
    }

    let fileHashes;
    try {
      fileHashes = _parseManifestFromInput(manifestRaw);
    } catch (err) {
      KredoUI.showError(err.message);
      return;
    }

    const payload = {
      action: 'integrity_check',
      agent_pubkey: agentPubkey,
      file_hashes: fileHashes,
    };
    const signature = KredoCrypto.sign(payload, signCtx.secretKey);

    try {
      const result = await KredoAPI.integrityCheck(agentPubkey, fileHashes, signature);
      KredoUI.showSuccess(`Integrity check complete: ${result.status}`);
      renderIntegrityStatus(result);
    } catch (err) {
      KredoUI.showError('Integrity check failed: ' + err.message);
    }
  }

  async function loadIntegrityStatus() {
    const agentPubkey = _normalizePubkey(document.getElementById('int-agent-key').value);
    if (!_isPubkey(agentPubkey)) return;

    const out = document.getElementById('int-status');
    KredoUI.showLoading(out);

    try {
      const status = await KredoAPI.integrityStatus(agentPubkey);
      renderIntegrityStatus(status);
    } catch (err) {
      out.innerHTML = `<p style="color:var(--red)">${KredoUI.escapeHtml(err.message)}</p>`;
    }
  }

  function renderIntegrityStatus(status) {
    const out = document.getElementById('int-status');
    if (!out) return;

    const light = status.traffic_light || status.status || 'red';
    const cls = light === 'green' ? 'gov-pill-green' : (light === 'yellow' ? 'gov-pill-yellow' : 'gov-pill-red');

    let diffHtml = '';
    const diff = status.latest_diff || status.diff;
    if (diff) {
      const added = (diff.added_paths || []).length;
      const removed = (diff.removed_paths || []).length;
      const changed = (diff.changed_paths || []).length;
      diffHtml = `
        <div class="review-row"><div class="review-key">Diff</div><div class="review-val">+${added} added, -${removed} removed, ~${changed} changed</div></div>
      `;
    }

    out.innerHTML = `
      <div class="review-row"><div class="review-key">Traffic Light</div><div class="review-val"><span class="gov-pill ${cls}">${KredoUI.escapeHtml(light)}</span></div></div>
      <div class="review-row"><div class="review-key">Status Label</div><div class="review-val">${KredoUI.escapeHtml(status.status_label || status.status || '-')}</div></div>
      <div class="review-row"><div class="review-key">Recommended Action</div><div class="review-val">${KredoUI.escapeHtml(status.recommended_action || '-')}</div></div>
      <div class="review-row"><div class="review-key">Active Baseline</div><div class="review-val">${status.active_baseline?.id || status.baseline_id || status.active_baseline_id || '-'}</div></div>
      <div class="review-row"><div class="review-key">Latest Check</div><div class="review-val">${status.latest_check?.id || status.check_id || status.latest_check_id || '-'}</div></div>
      ${diffHtml}
    `;
  }

  async function loadSourceAnomalies() {
    const out = document.getElementById('risk-result');
    if (!out) return;

    const params = {
      hours: Number(document.getElementById('risk-hours').value || 24),
      min_events: Number(document.getElementById('risk-min-events').value || 8),
      min_unique_actors: Number(document.getElementById('risk-min-actors').value || 4),
      limit: Number(document.getElementById('risk-limit').value || 50),
    };

    KredoUI.showLoading(out);
    try {
      const data = await KredoAPI.sourceAnomalies(params);
      if (!data.clusters || data.clusters.length === 0) {
        out.innerHTML = '<div class="empty-state">No anomaly clusters for the selected window.</div>';
        return;
      }

      const summary = `
        <div class="review-panel" style="margin-bottom:0.75rem">
          <div class="review-row"><div class="review-key">Window</div><div class="review-val">${data.window_hours}h</div></div>
          <div class="review-row"><div class="review-key">Thresholds</div><div class="review-val">min_events=${data.thresholds.min_events}, min_unique_actors=${data.thresholds.min_unique_actors}</div></div>
          <div class="review-row"><div class="review-key">Clusters</div><div class="review-val">${data.cluster_count}</div></div>
        </div>
      `;

      const table = KredoUI.buildTable(
        [
          { label: 'Source Hash', key: 'source_ip_hash' },
          { label: 'Sample IP', key: 'sample_ip' },
          { label: 'Events', key: 'event_count' },
          { label: 'Unique Actors', key: 'unique_actor_count' },
          { label: 'Actions', key: 'action_type_count' },
          { label: 'Last Seen', render: row => KredoUI.formatDateTime(row.last_seen) },
        ],
        data.clusters,
        { emptyMessage: 'No clusters found.' }
      );

      out.innerHTML = `<div class="card">${summary}${table}</div>`;
    } catch (err) {
      out.innerHTML = `<p style="color:var(--red)">${KredoUI.escapeHtml(err.message)}</p>`;
    }
  }

  return {
    render,
    updateRegistration,
    lookupOwnership,
    useClaimId,
    createOwnershipClaim,
    confirmOwnershipClaim,
    revokeOwnershipClaim,
    setIntegrityBaseline,
    runIntegrityCheck,
    loadIntegrityStatus,
    loadSourceAnomalies,
  };
})();
