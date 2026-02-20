/**
 * Browse View — Agent search and profile viewer
 *
 * Search by name, domain, skill. Click agent to view full profile.
 */

const BrowseView = (() => {
  let currentProfile = null;

  async function render() {
    KredoUI.renderView(`
      <h1 class="page-title">Browse Agents</h1>

      <div class="card">
        <div class="form-row">
          <div class="form-group">
            <label for="search-name">Agent name or pubkey</label>
            <input type="text" id="search-name" placeholder="Search by name...">
          </div>
          <div class="form-group">
            <label for="search-domain">Domain</label>
            <select id="search-domain">
              <option value="">All domains</option>
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label for="search-skill">Skill</label>
            <input type="text" id="search-skill" placeholder="e.g. incident-triage">
          </div>
          <div class="form-group">
            <label for="search-min-prof">Min proficiency</label>
            <select id="search-min-prof">
              <option value="">Any</option>
              <option value="1">1 - Novice</option>
              <option value="2">2 - Competent</option>
              <option value="3">3 - Proficient</option>
              <option value="4">4 - Expert</option>
              <option value="5">5 - Authority</option>
            </select>
          </div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="BrowseView.doSearch()">Search</button>
          <button class="btn" onclick="BrowseView.listAll()">Show All Agents</button>
        </div>
      </div>

      <div id="browse-results"></div>
      <div id="browse-profile"></div>
    `);

    // Load domain options
    try {
      const taxonomy = await KredoAPI.getTaxonomy();
      const select = document.getElementById('search-domain');
      for (const [key, d] of Object.entries(taxonomy.domains)) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = d.label;
        select.appendChild(opt);
      }
    } catch {}

    // Check for deep-link: #/browse/{pubkey}
    const linkedPubkey = KredoApp.getHashParam ? KredoApp.getHashParam() : null;
    if (linkedPubkey && linkedPubkey.startsWith('ed25519:')) {
      listAll();
      viewProfile(linkedPubkey);
    } else {
      listAll();
    }
  }

  async function listAll() {
    const resultsDiv = document.getElementById('browse-results');
    KredoUI.showLoading(resultsDiv);

    try {
      const data = await KredoAPI.listAgents(200);
      renderAgentList(data.agents, data.total);
    } catch (err) {
      resultsDiv.innerHTML = `<p style="color:var(--red);padding:1rem">${KredoUI.escapeHtml(err.message)}</p>`;
    }
  }

  async function doSearch() {
    const name = document.getElementById('search-name').value.trim();
    const domain = document.getElementById('search-domain').value;
    const skill = document.getElementById('search-skill').value.trim();
    const minProf = document.getElementById('search-min-prof').value;

    // If the input looks like a pubkey, go directly to profile
    if (name.startsWith('ed25519:')) {
      await viewProfile(name);
      return;
    }

    const resultsDiv = document.getElementById('browse-results');
    document.getElementById('browse-profile').innerHTML = '';
    KredoUI.showLoading(resultsDiv);

    try {
      // Search attestations by domain/skill, then extract unique subjects
      const params = {};
      if (domain) params.domain = domain;
      if (skill) params.skill = skill;
      if (minProf) params.min_proficiency = minProf;
      params.limit = 200;

      if (domain || skill || minProf) {
        const data = await KredoAPI.search(params);
        // Extract unique subjects
        const seen = new Set();
        const subjects = [];
        for (const a of data.attestations) {
          if (!seen.has(a.subject.pubkey)) {
            seen.add(a.subject.pubkey);
            subjects.push({
              pubkey: a.subject.pubkey,
              name: a.subject.name || '',
              type: 'unknown',
            });
          }
        }

        // If also filtering by name
        const filtered = name
          ? subjects.filter(s => s.name.toLowerCase().includes(name.toLowerCase()))
          : subjects;

        renderAgentList(filtered, filtered.length);
      } else {
        // Just list agents, filter by name client-side
        const data = await KredoAPI.listAgents(200);
        const filtered = name
          ? data.agents.filter(a => (a.name || '').toLowerCase().includes(name.toLowerCase()))
          : data.agents;
        renderAgentList(filtered, filtered.length);
      }
    } catch (err) {
      resultsDiv.innerHTML = `<p style="color:var(--red);padding:1rem">${KredoUI.escapeHtml(err.message)}</p>`;
    }
  }

  function renderAgentList(agents, total) {
    const resultsDiv = document.getElementById('browse-results');
    if (!agents || agents.length === 0) {
      resultsDiv.innerHTML = '<div class="empty-state">No agents found</div>';
      return;
    }

    resultsDiv.innerHTML = `
      <div class="card">
        <div class="card-header">${total} agent${total !== 1 ? 's' : ''}</div>
        ${KredoUI.buildTable(
          [
            { label: 'Name', render: row => `<a href="javascript:void(0)" onclick="BrowseView.viewProfile('${KredoUI.escapeHtml(row.pubkey)}')">${KredoUI.escapeHtml(row.name || '(unnamed)')}</a>` },
            { label: 'Type', render: row => row.type !== 'unknown' ? KredoUI.typeBadge(row.type) : '' },
            { label: 'Key', render: row => `<span style="font-family:var(--mono);font-size:0.8rem">${KredoUI.shortKey(row.pubkey)}</span>` },
            { label: 'Registered', render: row => row.registered_at ? KredoUI.formatDate(row.registered_at) : '' },
          ],
          agents
        )}
      </div>`;
  }

  async function viewProfile(pubkey) {
    const profileDiv = document.getElementById('browse-profile');
    KredoUI.showLoading(profileDiv);

    try {
      const profile = await KredoAPI.getProfile(pubkey);
      renderProfileDetail(profile);
    } catch (err) {
      profileDiv.innerHTML = `<div class="card" style="border-color:var(--red)"><p style="padding:1rem">${KredoUI.escapeHtml(err.message)}</p></div>`;
    }
  }

  function renderProfileDetail(profile) {
    const profileDiv = document.getElementById('browse-profile');
    const ac = profile.attestation_count || {};
    const trust = profile.trust_analysis || {};

    let html = `
      <div class="card" id="profile-detail">
        <div class="card-header">${KredoUI.escapeHtml(profile.name || '(unnamed)')} ${KredoUI.typeBadge(profile.type)}</div>
        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">Public Key</div>
            <div class="review-val">
              <div class="key-display" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(profile.pubkey)}', 'Public key')" title="Click to copy">${KredoUI.escapeHtml(profile.pubkey)}</div>
            </div>
          </div>
          <div class="review-row">
            <div class="review-key">Registered</div>
            <div class="review-val">${KredoUI.formatDate(profile.registered)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Last Seen</div>
            <div class="review-val">${KredoUI.timeAgo(profile.last_seen)}</div>
          </div>
        </div>

        <div class="stat-grid" style="margin-top:1rem">
          <div class="stat-card">
            <div class="stat-value">${ac.total || 0}</div>
            <div class="stat-label">Attestations</div>
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

    // Reputation
    if (trust.reputation_score != null) {
      html += `<div style="margin-top:1rem">${KredoUI.scoreBar(trust.reputation_score, 'Reputation')}</div>`;
    }

    // Skills
    if (profile.skills && profile.skills.length > 0) {
      html += `<div style="margin-top:1rem"><h4 style="margin-bottom:0.5rem">Skills</h4>`;
      html += KredoUI.buildTable(
        [
          { label: 'Domain', key: 'domain' },
          { label: 'Skill', key: 'specific' },
          { label: 'Proficiency', render: row => KredoUI.proficiencyBar(Math.round(row.weighted_avg_proficiency || row.avg_proficiency || 0)) },
          { label: 'Count', key: 'attestation_count' },
        ],
        profile.skills
      );
      html += '</div>';
    }

    // Trust network
    if (profile.trust_network && profile.trust_network.length > 0) {
      html += `<div style="margin-top:1rem"><h4 style="margin-bottom:0.5rem">Attestors</h4>`;
      html += KredoUI.buildTable(
        [
          { label: 'Key', render: row => `<a href="javascript:void(0)" onclick="BrowseView.viewProfile('${KredoUI.escapeHtml(row.pubkey)}')">${KredoUI.shortKey(row.pubkey)}</a>` },
          { label: 'Type', render: row => KredoUI.typeBadge(row.type) },
          { label: 'Attestations', key: 'attestation_count_for_subject' },
        ],
        profile.trust_network
      );
      html += '</div>';
    }

    // Warnings
    if (profile.warnings && profile.warnings.length > 0) {
      html += `<div style="margin-top:1rem;padding:0.75rem;border:1px solid var(--red);border-radius:var(--radius)"><h4 style="color:var(--red);margin-bottom:0.5rem">Warnings</h4>`;
      html += KredoUI.buildTable(
        [
          { label: 'Category', key: 'category' },
          { label: 'From', render: row => KredoUI.shortKey(row.attestor) },
          { label: 'Date', render: row => KredoUI.formatDate(row.issued) },
        ],
        profile.warnings
      );
      html += '</div>';
    }

    const identity = KredoStorage.getIdentity();
    if (identity) {
      html += `<div class="btn-group" style="margin-top:1rem">
        <a href="#/attest" class="btn btn-primary" onclick="localStorage.setItem('attest_subject','${KredoUI.escapeHtml(profile.pubkey)}')">Attest This Agent</a>
        <button class="btn" onclick="KredoUI.copyToClipboard('${KredoUI.escapeHtml(profile.pubkey)}', 'Public key')">Copy Key</button>
      </div>`;
    }

    html += '</div>';
    profileDiv.innerHTML = html;

    // Scroll to profile
    document.getElementById('profile-detail').scrollIntoView({ behavior: 'smooth' });
  }

  return { render, doSearch, listAll, viewProfile };
})();
