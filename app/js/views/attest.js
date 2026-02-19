/**
 * Attest View — Guided attestation form (mirrors kredo attest -i)
 *
 * 11-step flow: type → subject → domain → skill → proficiency → evidence →
 * artifacts → outcome → review → sign → result
 */

const AttestView = (() => {
  let state = {};
  let taxonomy = null;
  let agents = [];
  let lastSignedAttestation = null;

  const ATTEST_TYPES = [
    { value: 'skill_attestation', label: 'Skill Attestation', desc: 'Vouch for a specific technical or professional skill' },
    { value: 'intellectual_contribution', label: 'Intellectual Contribution', desc: 'Recognize a contribution to knowledge, research, or ideas' },
    { value: 'community_contribution', label: 'Community Contribution', desc: 'Acknowledge community building, mentoring, or collaboration' },
  ];

  function resetState() {
    state = {
      step: 1,
      type: null,
      subject: null,
      subjectName: '',
      domain: null,
      skill: null,
      proficiency: 3,
      evidence: '',
      artifacts: '',
      outcome: '',
      interactionDate: '',
    };
  }

  async function render() {
    const identity = KredoStorage.getIdentity();
    if (!identity) {
      KredoUI.renderView('<div class="empty-state">No identity found. <a href="#/setup">Create one</a> first.</div>');
      return;
    }

    resetState();

    // Preload taxonomy and agents
    try {
      [taxonomy, agents] = await Promise.all([
        KredoAPI.getTaxonomy(),
        KredoAPI.listAgents(200).then(r => r.agents).catch(() => []),
      ]);
    } catch (err) {
      taxonomy = null;
    }

    renderStep();
  }

  function renderStep() {
    const totalSteps = 10;
    const stepsHtml = Array.from({ length: totalSteps }, (_, i) => {
      const n = i + 1;
      const cls = n < state.step ? 'step done' : (n === state.step ? 'step active' : 'step');
      return `<div class="${cls}">${n}</div>`;
    }).join('');

    let content = '';
    switch (state.step) {
      case 1: content = renderTypeStep(); break;
      case 2: content = renderSubjectStep(); break;
      case 3: content = renderDomainStep(); break;
      case 4: content = renderSkillStep(); break;
      case 5: content = renderProficiencyStep(); break;
      case 6: content = renderEvidenceStep(); break;
      case 7: content = renderArtifactsStep(); break;
      case 8: content = renderOutcomeStep(); break;
      case 9: content = renderReviewStep(); break;
      case 10: content = renderSignStep(); break;
    }

    KredoUI.renderView(`
      <h1 class="page-title">Create Attestation</h1>
      <div class="steps">${stepsHtml}</div>
      ${content}
    `);
  }

  // --- Step renderers ---

  function renderTypeStep() {
    return `
      <div class="card">
        <div class="card-header">Step 1: Attestation Type</div>
        <div class="option-grid">
          ${ATTEST_TYPES.map(t => `
            <div class="option-card${state.type === t.value ? ' selected' : ''}"
                 onclick="AttestView.selectType('${t.value}')">
              <h4>${t.label}</h4>
              <p>${t.desc}</p>
            </div>
          `).join('')}
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="AttestView.nextStep()" ${!state.type ? 'disabled' : ''}>Next</button>
        </div>
      </div>`;
  }

  function renderSubjectStep() {
    const identity = KredoStorage.getIdentity();
    const filteredAgents = agents.filter(a => a.pubkey !== identity.pubkey);

    return `
      <div class="card">
        <div class="card-header">Step 2: Who are you attesting?</div>
        ${filteredAgents.length > 0 ? `
          <div style="margin-bottom:1rem">
            <label style="font-size:0.85rem;color:var(--text-muted);margin-bottom:0.5rem;display:block">Select from known agents:</label>
            ${KredoUI.buildTable(
              [
                { label: '', render: row => `<input type="radio" name="subject" value="${row.pubkey}" ${state.subject === row.pubkey ? 'checked' : ''} onchange="AttestView.selectSubject('${row.pubkey}', '${KredoUI.escapeHtml(row.name || '')}')">` },
                { label: 'Name', render: row => KredoUI.escapeHtml(row.name || '(unnamed)') },
                { label: 'Type', render: row => KredoUI.typeBadge(row.type) },
                { label: 'Key', render: row => KredoUI.shortKey(row.pubkey) },
              ],
              filteredAgents,
              { className: 'clickable' }
            )}
          </div>
          <div style="text-align:center;color:var(--text-dim);margin:0.75rem 0">— or —</div>
        ` : ''}
        <div class="form-group">
          <label for="subject-pubkey">Paste public key</label>
          <input type="text" id="subject-pubkey" placeholder="ed25519:..." value="${state.subject && !filteredAgents.find(a => a.pubkey === state.subject) ? state.subject : ''}"
                 oninput="AttestView.selectSubject(this.value, '')">
        </div>
        <div class="btn-group">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()" ${!state.subject ? 'disabled' : ''}>Next</button>
        </div>
      </div>`;
  }

  function renderDomainStep() {
    if (!taxonomy || !taxonomy.domains) {
      return `<div class="card"><div class="card-header">Step 3: Domain</div><p style="color:var(--text-muted)">Could not load taxonomy. Please try again.</p><button class="btn" onclick="AttestView.prevStep()">Back</button></div>`;
    }

    const domains = Object.entries(taxonomy.domains);
    return `
      <div class="card">
        <div class="card-header">Step 3: Skill Domain</div>
        <div class="option-grid">
          ${domains.map(([key, d]) => `
            <div class="option-card${state.domain === key ? ' selected' : ''}"
                 onclick="AttestView.selectDomain('${key}')">
              <h4>${KredoUI.escapeHtml(d.label)}</h4>
              <p>${d.skills.length} skills</p>
            </div>
          `).join('')}
        </div>
        <div class="btn-group">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()" ${!state.domain ? 'disabled' : ''}>Next</button>
        </div>
      </div>`;
  }

  function renderSkillStep() {
    const domain = taxonomy?.domains?.[state.domain];
    if (!domain) return '<div class="card">No domain selected</div>';

    return `
      <div class="card">
        <div class="card-header">Step 4: Specific Skill (${KredoUI.escapeHtml(domain.label)})</div>
        <div class="option-grid">
          ${domain.skills.map(s => `
            <div class="option-card${state.skill === s ? ' selected' : ''}"
                 onclick="AttestView.selectSkill('${s}')">
              <h4>${KredoUI.escapeHtml(s)}</h4>
            </div>
          `).join('')}
        </div>
        <div class="btn-group">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()" ${!state.skill ? 'disabled' : ''}>Next</button>
        </div>
      </div>`;
  }

  function renderProficiencyStep() {
    return `
      <div class="card">
        <div class="card-header">Step 5: Proficiency Level</div>
        <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:1rem">How skilled is this entity at ${KredoUI.escapeHtml(state.skill)}?</p>
        <div class="prof-selector">
          ${[1,2,3,4,5].map(n => `
            <div class="prof-option${state.proficiency === n ? ' selected' : ''}"
                 onclick="AttestView.selectProficiency(${n})">
              <span class="level">${n}</span>
              <span class="name">${KredoUI.PROFICIENCY_LABELS[n]}</span>
            </div>
          `).join('')}
        </div>
        <div class="btn-group" style="margin-top:1rem">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()">Next</button>
        </div>
      </div>`;
  }

  function renderEvidenceStep() {
    return `
      <div class="card">
        <div class="card-header">Step 6: Evidence</div>
        <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:0.75rem">Describe what you observed. Be specific — evidence quality is scored automatically.</p>
        <div class="form-group">
          <label for="evidence-text">What did you observe?</label>
          <textarea id="evidence-text" rows="5" placeholder="e.g., Correctly triaged 3 P1 incidents during the March outage, identifying root cause within 15 minutes each time..."
                    oninput="AttestView.updateEvidence(this.value)">${KredoUI.escapeHtml(state.evidence)}</textarea>
          <div class="hint" id="evidence-count">${state.evidence.length} characters</div>
        </div>
        <div class="btn-group">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()" ${!state.evidence.trim() ? 'disabled' : ''}>Next</button>
        </div>
      </div>`;
  }

  function renderArtifactsStep() {
    return `
      <div class="card">
        <div class="card-header">Step 7: Artifacts (optional)</div>
        <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:0.75rem">Links, log IDs, or references that support your attestation.</p>
        <div class="form-group">
          <label for="artifacts-text">Artifacts</label>
          <input type="text" id="artifacts-text" placeholder="https://example.com/report, LOG-1234, ..."
                 value="${KredoUI.escapeHtml(state.artifacts)}"
                 oninput="AttestView.updateArtifacts(this.value)">
          <div class="hint">Comma-separated. URLs, ticket IDs, chain IDs, etc.</div>
        </div>
        <div class="btn-group">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()">Next</button>
        </div>
      </div>`;
  }

  function renderOutcomeStep() {
    return `
      <div class="card">
        <div class="card-header">Step 8: Outcome (optional)</div>
        <div class="form-group">
          <label for="outcome-text">What was the result?</label>
          <textarea id="outcome-text" rows="3" placeholder="e.g., All incidents resolved within SLA, zero data loss..."
                    oninput="AttestView.updateOutcome(this.value)">${KredoUI.escapeHtml(state.outcome)}</textarea>
        </div>
        <div class="form-group">
          <label for="interaction-date">Interaction date (optional)</label>
          <input type="date" id="interaction-date" value="${state.interactionDate}"
                 oninput="AttestView.updateDate(this.value)">
        </div>
        <div class="btn-group">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-primary" onclick="AttestView.nextStep()">Next</button>
        </div>
      </div>`;
  }

  function renderReviewStep() {
    const identity = KredoStorage.getIdentity();
    const subjectAgent = agents.find(a => a.pubkey === state.subject);
    const subjectDisplay = state.subjectName || (subjectAgent ? subjectAgent.name : KredoUI.shortKey(state.subject));
    const domain = taxonomy?.domains?.[state.domain];

    return `
      <div class="card">
        <div class="card-header">Step 9: Review</div>
        <p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:1rem">Review your attestation before signing.</p>
        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">Type</div>
            <div class="review-val">${KredoUI.attestationTypeBadge(state.type)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">From</div>
            <div class="review-val">${KredoUI.escapeHtml(identity.name)} ${KredoUI.typeBadge(identity.type)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">To</div>
            <div class="review-val">${KredoUI.escapeHtml(subjectDisplay)} — ${KredoUI.shortKey(state.subject)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Domain</div>
            <div class="review-val">${KredoUI.escapeHtml(domain ? domain.label : state.domain)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Skill</div>
            <div class="review-val">${KredoUI.escapeHtml(state.skill)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Proficiency</div>
            <div class="review-val">${KredoUI.proficiencyBadge(state.proficiency)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Evidence</div>
            <div class="review-val">${KredoUI.escapeHtml(state.evidence)}</div>
          </div>
          ${state.artifacts ? `<div class="review-row"><div class="review-key">Artifacts</div><div class="review-val">${KredoUI.escapeHtml(state.artifacts)}</div></div>` : ''}
          ${state.outcome ? `<div class="review-row"><div class="review-key">Outcome</div><div class="review-val">${KredoUI.escapeHtml(state.outcome)}</div></div>` : ''}
          ${state.interactionDate ? `<div class="review-row"><div class="review-key">Interaction</div><div class="review-val">${state.interactionDate}</div></div>` : ''}
        </div>
        <div class="btn-group" style="margin-top:1rem">
          <button class="btn" onclick="AttestView.prevStep()">Back</button>
          <button class="btn btn-success" onclick="AttestView.signAndSubmit()">Sign &amp; Submit</button>
        </div>
      </div>`;
  }

  function renderSignStep() {
    // This is shown after successful submission
    return ''; // handled by signAndSubmit
  }

  // --- Actions ---

  function selectType(type) {
    state.type = type;
    renderStep();
  }

  function selectSubject(pubkey, name) {
    state.subject = pubkey;
    state.subjectName = name;
  }

  function selectDomain(domain) {
    state.domain = domain;
    state.skill = null; // Reset skill when domain changes
    renderStep();
  }

  function selectSkill(skill) {
    state.skill = skill;
    renderStep();
  }

  function selectProficiency(level) {
    state.proficiency = level;
    renderStep();
  }

  function updateEvidence(val) {
    state.evidence = val;
    const counter = document.getElementById('evidence-count');
    if (counter) counter.textContent = val.length + ' characters';
  }

  function updateArtifacts(val) { state.artifacts = val; }
  function updateOutcome(val) { state.outcome = val; }
  function updateDate(val) { state.interactionDate = val; }

  function nextStep() {
    // Validation
    if (state.step === 2 && !state.subject) {
      KredoUI.showError('Please select or enter a subject');
      return;
    }
    if (state.step === 2) {
      // Validate pubkey format
      if (!state.subject.startsWith('ed25519:') || state.subject.replace('ed25519:', '').length !== 64) {
        KredoUI.showError('Invalid public key. Must be ed25519: followed by 64 hex characters.');
        return;
      }
    }
    state.step++;
    renderStep();
  }

  function prevStep() {
    if (state.step > 1) {
      state.step--;
      renderStep();
    }
  }

  async function signAndSubmit() {
    const identity = KredoStorage.getIdentity();
    if (!identity) {
      KredoUI.showError('No identity found');
      return;
    }

    // Get secret key
    let secretKey;
    if (KredoStorage.isEncrypted()) {
      const passphrase = prompt('Enter your passphrase to sign:');
      if (!passphrase) return;
      secretKey = await KredoStorage.getSecretKey(passphrase);
      if (!secretKey) {
        KredoUI.showError('Wrong passphrase');
        return;
      }
    } else {
      secretKey = await KredoStorage.getSecretKey();
    }

    const now = new Date();
    const expires = new Date(now);
    expires.setFullYear(expires.getFullYear() + 1);

    const artifacts = state.artifacts
      ? state.artifacts.split(',').map(s => s.trim()).filter(Boolean)
      : [];

    const subjectAgent = agents.find(a => a.pubkey === state.subject);

    // Build attestation object (pre-sign, matches Python model_dump output)
    const attestation = {
      kredo: '1.0',
      id: KredoCrypto.uuid4(),
      type: state.type,
      subject: {
        pubkey: state.subject,
        name: state.subjectName || (subjectAgent ? subjectAgent.name : ''),
      },
      attestor: {
        pubkey: identity.pubkey,
        name: identity.name,
        type: identity.type,
      },
      skill: {
        domain: state.domain,
        specific: state.skill,
        proficiency: state.proficiency,
      },
      evidence: {
        context: state.evidence,
        artifacts: artifacts,
        outcome: state.outcome || '',
      },
      issued: KredoCrypto.formatDate(now),
      expires: KredoCrypto.formatDate(expires),
    };

    // Add optional fields
    if (state.interactionDate) {
      attestation.evidence.interaction_date = state.interactionDate + 'T00:00:00Z';
    }

    // Sign
    const signed = KredoCrypto.signAttestation(attestation, secretKey);
    lastSignedAttestation = signed;

    // Submit
    state.step = 10;
    KredoUI.renderView(`
      <h1 class="page-title">Create Attestation</h1>
      <div class="loading"><div class="spinner"></div><span>Signing and submitting...</span></div>
    `);

    try {
      const result = await KredoAPI.submitAttestation(signed);
      renderResult(signed, result);
    } catch (err) {
      KredoUI.renderView(`
        <h1 class="page-title">Submission Failed</h1>
        <div class="card" style="border-color:var(--red)">
          <div class="card-header" style="color:var(--red)">Error</div>
          <p>${KredoUI.escapeHtml(err.message)}</p>
          <div style="margin-top:1rem">
            <div class="card-header">Signed Attestation (local copy)</div>
            <div class="json-display">${KredoUI.escapeHtml(JSON.stringify(signed, null, 2))}</div>
          </div>
          <div class="btn-group" style="margin-top:1rem">
            <button class="btn" onclick="AttestView.render()">Start Over</button>
            <button class="btn" onclick="AttestView.copyLastAttestation()">Copy JSON</button>
          </div>
        </div>
      `);
    }
  }

  function renderResult(attestation, result) {
    const score = result.evidence_score || {};
    KredoUI.renderView(`
      <h1 class="page-title">Attestation Submitted</h1>
      <div class="card" style="border-color:var(--green)">
        <div class="card-header" style="color:var(--green)">Success</div>
        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">ID</div>
            <div class="review-val" style="font-family:var(--mono);font-size:0.85rem">${KredoUI.escapeHtml(result.id)}</div>
          </div>
          <div class="review-row">
            <div class="review-key">Status</div>
            <div class="review-val" style="color:var(--green)">Accepted</div>
          </div>
        </div>

        ${score.composite != null ? `
          <div style="margin-top:1rem">
            <div class="card-header">Evidence Quality Score</div>
            ${KredoUI.scoreBar(score.composite, 'Composite')}
            ${score.specificity != null ? KredoUI.scoreBar(score.specificity, 'Specificity') : ''}
            ${score.verifiability != null ? KredoUI.scoreBar(score.verifiability, 'Verifiability') : ''}
            ${score.relevance != null ? KredoUI.scoreBar(score.relevance, 'Relevance') : ''}
            ${score.recency != null ? KredoUI.scoreBar(score.recency, 'Recency') : ''}
          </div>
        ` : ''}

        <details style="margin-top:1rem">
          <summary style="cursor:pointer;color:var(--text-muted);font-size:0.85rem">View full attestation JSON</summary>
          <div class="json-display" style="margin-top:0.5rem">${KredoUI.escapeHtml(JSON.stringify(attestation, null, 2))}</div>
        </details>

        <div class="btn-group" style="margin-top:1rem">
          <button class="btn btn-primary" onclick="AttestView.render()">Create Another</button>
          <button class="btn" onclick="AttestView.copyLastAttestation()">Copy JSON</button>
          <a href="#/dashboard" class="btn">View Dashboard</a>
        </div>
      </div>
    `);
  }

  function copyLastAttestation() {
    if (lastSignedAttestation) {
      KredoUI.copyToClipboard(JSON.stringify(lastSignedAttestation, null, 2), 'Attestation JSON');
    }
  }

  return {
    render,
    selectType,
    selectSubject,
    selectDomain,
    selectSkill,
    selectProficiency,
    updateEvidence,
    updateArtifacts,
    updateOutcome,
    updateDate,
    nextStep,
    prevStep,
    signAndSubmit,
    copyLastAttestation,
  };
})();
