/**
 * Dashboard View — Your Profile (mirrors kredo me)
 *
 * Identity panel, network profile, skills, attestation counts, trust analysis.
 */

const DashboardView = (() => {

  async function render() {
    const identity = KredoStorage.getIdentity();
    if (!identity) {
      KredoUI.renderView('<div class="empty-state">Not logged in. <a href="#/setup/recover">Load Identity (Login)</a> to continue.</div>');
      return;
    }

    // Render identity panel immediately, then load network data
    const showPlaintextWarning = !KredoStorage.isEncrypted();

    KredoUI.renderView(`
      <h1 class="page-title">Dashboard</h1>

      ${showPlaintextWarning ? `
      <div class="warning-banner warning-banner-subtle">
        <strong>&#9888; Unencrypted key:</strong> Your private key is stored without passphrase encryption.
        Use Setup &rarr; <strong>Encrypt Current Key</strong> to secure this identity without changing pubkey.
      </div>` : ''}

      <div class="card">
        <div class="card-header">Identity</div>
        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">Name</div>
            <div class="review-val">${KredoUI.escapeHtml(identity.name)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Type</div>
            <div class="review-val">${KredoUI.typeBadge(identity.type)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Public Key</div>
            <div class="review-val">
              <div class="key-display" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(identity.pubkey)}', 'Public key')" title="Click to copy">${KredoUI.escapeHtml(identity.pubkey)}</div>
            </div>
          </div>
        </div>
        <div class="btn-group" style="margin-top:0.75rem">
          <button class="btn btn-sm" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(identity.pubkey)}', 'Public key')">Copy Key</button>
          <button class="btn btn-sm" onclick="SetupView.downloadBackup()">Backup</button>
          <a href="#/attest" class="btn btn-sm btn-primary">New Attestation</a>
        </div>
      </div>

      <div id="network-profile">
        <div class="loading"><div class="spinner"></div><span>Loading network profile...</span></div>
      </div>
    `);

    // Load profile from API
    try {
      const profile = await KredoAPI.getProfile(identity.pubkey);
      renderProfile(profile);
    } catch (err) {
      if (err.status === 404) {
        document.getElementById('network-profile').innerHTML = `
          <div class="card">
            <div class="card-header">Network Profile</div>
            <div class="empty-state">
              Not yet registered on the Kredo network.<br>
              <button class="btn btn-primary" style="margin-top:0.75rem" onclick="DashboardView.registerNow()">Register Now</button>
            </div>
          </div>`;
      } else {
        document.getElementById('network-profile').innerHTML = `
          <div class="card">
            <div class="card-header">Network Profile</div>
            <p style="color:var(--text-muted);padding:1rem">Could not load network profile: ${KredoUI.escapeHtml(err.message)}</p>
          </div>`;
      }
    }
  }

  function renderProfile(profile) {
    const container = document.getElementById('network-profile');
    if (!container) return;

    const ac = profile.attestation_count || {};
    const trust = profile.trust_analysis || {};
    const accountability = profile.accountability || {};
    const integrity = profile.integrity || {};

    let html = `
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-value">${ac.total || 0}</div>
          <div class="stat-label">Attestations Received</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${ac.by_humans || 0}</div>
          <div class="stat-label">From Humans</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${ac.by_agents || 0}</div>
          <div class="stat-label">From Agents</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${profile.evidence_quality_avg != null ? Math.round(profile.evidence_quality_avg * 100) + '%' : '-'}</div>
          <div class="stat-label">Evidence Quality</div>
        </div>
      </div>`;

    if (profile.accountability || profile.integrity) {
      const tier = accountability.tier || 'unlinked';
      const tierCls = tier === 'human-linked' ? 'gov-pill-green' : 'gov-pill-yellow';
      const owner = accountability.owner || null;
      const traffic = integrity.traffic_light || 'red';
      const trafficCls = traffic === 'green' ? 'gov-pill-green' : (traffic === 'yellow' ? 'gov-pill-yellow' : 'gov-pill-red');

      html += `
        <div class="card-grid">
          <div class="card">
            <div class="card-header">Accountability</div>
            <div class="review-panel">
              <div class="review-row">
                <div class="review-key">Tier</div>
                <div class="review-val"><span class="gov-pill ${tierCls}">${KredoUI.escapeHtml(tier)}</span></div>
              </div>
              <div class="review-row">
                <div class="review-key">Multiplier</div>
                <div class="review-val">${accountability.multiplier != null ? accountability.multiplier : '-'}</div>
              </div>
              <div class="review-row">
                <div class="review-key">Owner</div>
                <div class="review-val">${owner ? `${KredoUI.escapeHtml(owner.name || '')} ${KredoUI.shortKey(owner.pubkey)}` : 'No active owner link'}</div>
              </div>
            </div>
          </div>
          <div class="card">
            <div class="card-header">Integrity Run-Gate</div>
            <div class="review-panel">
              <div class="review-row">
                <div class="review-key">Traffic Light</div>
                <div class="review-val"><span class="gov-pill ${trafficCls}">${KredoUI.escapeHtml(traffic)}</span></div>
              </div>
              <div class="review-row">
                <div class="review-key">Status</div>
                <div class="review-val">${KredoUI.escapeHtml(integrity.status_label || '-')}</div>
              </div>
              <div class="review-row">
                <div class="review-key">Action</div>
                <div class="review-val">${KredoUI.escapeHtml(integrity.recommended_action || '-')}</div>
              </div>
            </div>
            <div class="btn-group" style="margin-top:0.75rem">
              <a class="btn btn-sm" href="#/governance">Open Governance</a>
            </div>
          </div>
        </div>`;
    }

    // Reputation score
    if (trust.reputation_score != null) {
      html += `
        <div class="card">
          <div class="card-header">Trust Analysis</div>
          ${KredoUI.scoreBar(trust.reputation_score, 'Reputation')}
          ${trust.ring_flags && trust.ring_flags.length > 0 ? `
            <div style="margin-top:0.75rem;padding:0.5rem;background:var(--bg);border-radius:var(--radius-sm);border:1px solid var(--yellow)">
              <span style="color:var(--yellow);font-size:0.85rem">Ring detected: ${trust.ring_flags.map(r => r.ring_type + ' (' + r.size + ' members)').join(', ')}</span>
            </div>` : ''}
        </div>`;
    }

    // Skills
    if (profile.skills && profile.skills.length > 0) {
      html += '<div class="card"><div class="card-header">Skills</div>';
      html += KredoUI.buildTable(
        [
          { label: 'Domain', key: 'domain' },
          { label: 'Skill', key: 'specific' },
          { label: 'Proficiency', render: row => KredoUI.proficiencyBar(Math.round(row.weighted_avg_proficiency || row.avg_proficiency || 0)) },
          { label: 'Attestations', key: 'attestation_count' },
        ],
        profile.skills,
        { emptyMessage: 'No skills attested yet' }
      );
      html += '</div>';
    }

    // Warnings
    if (profile.warnings && profile.warnings.length > 0) {
      html += '<div class="card" style="border-color:var(--red)"><div class="card-header" style="color:var(--red)">Warnings</div>';
      html += KredoUI.buildTable(
        [
          { label: 'Category', key: 'category' },
          { label: 'From', render: row => KredoUI.shortKey(row.attestor) },
          { label: 'Date', render: row => KredoUI.formatDate(row.issued) },
          { label: 'Status', render: row => row.is_revoked ? '<span style="color:var(--green)">Revoked</span>' : (row.dispute_count > 0 ? `Disputed (${row.dispute_count})` : '<span style="color:var(--red)">Active</span>') },
        ],
        profile.warnings,
        { emptyMessage: 'No warnings' }
      );
      html += '</div>';
    }

    // Trust network
    if (profile.trust_network && profile.trust_network.length > 0) {
      html += '<div class="card"><div class="card-header">Trust Network</div>';
      html += KredoUI.buildTable(
        [
          { label: 'Attestor', render: row => KredoUI.shortKey(row.pubkey) },
          { label: 'Type', render: row => KredoUI.typeBadge(row.type) },
          { label: 'Attestations', key: 'attestation_count_for_subject' },
        ],
        profile.trust_network,
        { emptyMessage: 'No attestors yet' }
      );
      html += '</div>';
    }

    container.innerHTML = html;
  }

  async function registerNow() {
    const identity = KredoStorage.getIdentity();
    if (!identity) return;
    try {
      await KredoAPI.register(identity.pubkey, identity.name, identity.type);
      KredoUI.showSuccess('Registered on the Kredo network!');
      render();
    } catch (err) {
      KredoUI.showError('Registration failed: ' + err.message);
    }
  }

  return { render, registerNow };
})();
