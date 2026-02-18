/**
 * Taxonomy View — Browse domains and skills + add custom entries
 */

const TaxonomyView = (() => {

  async function render() {
    const hasId = KredoStorage.hasIdentity();
    KredoUI.renderView(`
      <h1 class="page-title">Skill Taxonomy</h1>
      <p class="page-subtitle">Kredo organizes skills into domains. Browse the taxonomy to see what skills can be attested.</p>

      ${hasId ? `
      <div class="card" style="margin-bottom:1.5rem">
        <h3>Add Custom Domain or Skill</h3>
        <p class="page-subtitle" style="margin-bottom:1rem">Extend the taxonomy with your own domains and skills. Requires your identity signature.</p>
        <div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-end">
          <div style="flex:1;min-width:200px">
            <label class="form-label">Domain ID</label>
            <input type="text" id="custom-domain-id" class="form-input" placeholder="e.g. vise-operations">
          </div>
          <div style="flex:1;min-width:200px">
            <label class="form-label">Domain Label</label>
            <input type="text" id="custom-domain-label" class="form-input" placeholder="e.g. VISE Operations">
          </div>
          <button class="btn btn-primary" onclick="TaxonomyView.addDomain()" id="btn-add-domain">Add Domain</button>
        </div>
        <div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-end;margin-top:1rem">
          <div style="flex:1;min-width:200px">
            <label class="form-label">Target Domain</label>
            <select id="custom-skill-domain" class="form-input"><option value="">Loading...</option></select>
          </div>
          <div style="flex:1;min-width:200px">
            <label class="form-label">Skill ID</label>
            <input type="text" id="custom-skill-id" class="form-input" placeholder="e.g. chain-orchestration">
          </div>
          <button class="btn btn-primary" onclick="TaxonomyView.addSkill()" id="btn-add-skill">Add Skill</button>
        </div>
      </div>
      ` : ''}

      <div id="taxonomy-tree">
        <div class="loading"><div class="spinner"></div><span>Loading taxonomy...</span></div>
      </div>
    `);

    try {
      const taxonomy = await KredoAPI.getTaxonomy();
      renderTree(taxonomy);
      if (hasId) populateDomainSelect(taxonomy);
    } catch (err) {
      document.getElementById('taxonomy-tree').innerHTML =
        `<p style="color:var(--red);padding:1rem">${KredoUI.escapeHtml(err.message)}</p>`;
    }
  }

  function populateDomainSelect(taxonomy) {
    const select = document.getElementById('custom-skill-domain');
    if (!select || !taxonomy || !taxonomy.domains) return;
    const domains = Object.entries(taxonomy.domains);
    select.innerHTML = domains.map(([key, d]) =>
      `<option value="${KredoUI.escapeHtml(key)}">${KredoUI.escapeHtml(d.label)} (${KredoUI.escapeHtml(key)})</option>`
    ).join('');
  }

  function renderTree(taxonomy) {
    const container = document.getElementById('taxonomy-tree');
    if (!taxonomy || !taxonomy.domains) {
      container.innerHTML = '<div class="empty-state">No taxonomy data</div>';
      return;
    }

    const domains = Object.entries(taxonomy.domains);
    let totalSkills = 0;
    domains.forEach(([, d]) => totalSkills += d.skills.length);

    let html = `
      <div class="stat-grid" style="margin-bottom:1.5rem">
        <div class="stat-card">
          <div class="stat-value">${domains.length}</div>
          <div class="stat-label">Domains</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${totalSkills}</div>
          <div class="stat-label">Skills</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">v${KredoUI.escapeHtml(taxonomy.version || '1.0')}</div>
          <div class="stat-label">Version</div>
        </div>
      </div>`;

    for (const [key, domain] of domains) {
      html += `
        <div class="domain-item" id="domain-${key}">
          <div class="domain-header" onclick="TaxonomyView.toggleDomain('${key}')">
            <h3>${KredoUI.escapeHtml(domain.label)}</h3>
            <div style="display:flex;align-items:center;gap:0.75rem">
              <span class="count">${domain.skills.length} skills</span>
              <span class="arrow">&#9654;</span>
            </div>
          </div>
          <div class="domain-skills">
            ${domain.skills.map(s => `<span class="skill-chip">${KredoUI.escapeHtml(s)}</span>`).join('')}
          </div>
        </div>`;
    }

    container.innerHTML = html;

    // Expand all by default
    domains.forEach(([key]) => {
      document.getElementById('domain-' + key).classList.add('expanded');
    });
  }

  function toggleDomain(key) {
    const el = document.getElementById('domain-' + key);
    if (el) el.classList.toggle('expanded');
  }

  async function addDomain() {
    const domainId = document.getElementById('custom-domain-id').value.trim();
    const label = document.getElementById('custom-domain-label').value.trim();
    if (!domainId || !label) {
      KredoUI.showAlert('Please fill in both Domain ID and Label.', 'error');
      return;
    }
    if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(domainId)) {
      KredoUI.showAlert('Domain ID must be a hyphenated lowercase slug (e.g. "vise-operations").', 'error');
      return;
    }

    const secretKey = KredoStorage.getSecretKey();
    const identity = KredoStorage.getIdentity();
    if (!secretKey || !identity) {
      KredoUI.showAlert('Identity not available. Set up your identity first.', 'error');
      return;
    }

    const btn = document.getElementById('btn-add-domain');
    btn.disabled = true;
    btn.textContent = 'Creating...';

    try {
      const payload = { action: 'create_domain', id: domainId, label: label, pubkey: identity.pubkey };
      const signature = KredoCrypto.sign(payload, secretKey);
      await KredoAPI.createDomain(domainId, label, identity.pubkey, signature);
      KredoUI.showAlert(`Domain "${label}" created successfully!`, 'success');
      document.getElementById('custom-domain-id').value = '';
      document.getElementById('custom-domain-label').value = '';
      // Refresh the view
      const taxonomy = await KredoAPI.getTaxonomy();
      renderTree(taxonomy);
      populateDomainSelect(taxonomy);
    } catch (err) {
      KredoUI.showAlert(err.message || 'Failed to create domain', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Add Domain';
    }
  }

  async function addSkill() {
    const domain = document.getElementById('custom-skill-domain').value;
    const skillId = document.getElementById('custom-skill-id').value.trim();
    if (!domain || !skillId) {
      KredoUI.showAlert('Please select a domain and fill in the Skill ID.', 'error');
      return;
    }
    if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(skillId)) {
      KredoUI.showAlert('Skill ID must be a hyphenated lowercase slug (e.g. "chain-orchestration").', 'error');
      return;
    }

    const secretKey = KredoStorage.getSecretKey();
    const identity = KredoStorage.getIdentity();
    if (!secretKey || !identity) {
      KredoUI.showAlert('Identity not available. Set up your identity first.', 'error');
      return;
    }

    const btn = document.getElementById('btn-add-skill');
    btn.disabled = true;
    btn.textContent = 'Creating...';

    try {
      const payload = { action: 'create_skill', domain: domain, id: skillId, pubkey: identity.pubkey };
      const signature = KredoCrypto.sign(payload, secretKey);
      await KredoAPI.createSkill(domain, skillId, identity.pubkey, signature);
      KredoUI.showAlert(`Skill "${skillId}" added to ${domain}!`, 'success');
      document.getElementById('custom-skill-id').value = '';
      // Refresh the view
      const taxonomy = await KredoAPI.getTaxonomy();
      renderTree(taxonomy);
    } catch (err) {
      KredoUI.showAlert(err.message || 'Failed to create skill', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Add Skill';
    }
  }

  return { render, toggleDomain, addDomain, addSkill };
})();
