/**
 * Verify View — Paste attestation JSON, verify signature locally and via API
 */

const VerifyView = (() => {

  function render() {
    KredoUI.renderView(`
      <h1 class="page-title">Verify Attestation</h1>
      <p class="page-subtitle">Paste an attestation JSON to verify its Ed25519 signature both locally (in your browser) and against the Discovery API.</p>

      <div class="card">
        <div class="card-header">Attestation JSON</div>
        <div class="form-group">
          <textarea id="verify-json" rows="12" placeholder='Paste attestation JSON here...\n\n{\n  "kredo": "1.0",\n  "id": "...",\n  "type": "skill_attestation",\n  ...\n}'></textarea>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="VerifyView.doVerify()">Verify</button>
          <button class="btn" onclick="document.getElementById('verify-json').value=''">Clear</button>
        </div>
      </div>

      <div id="verify-result"></div>

      <div class="card">
        <div class="card-header">Or look up by ID</div>
        <div class="form-group">
          <label for="verify-id">Attestation ID (UUID)</label>
          <input type="text" id="verify-id" placeholder="e.g. 3f2a1b4c-5d6e-7f8g-9h0i-1j2k3l4m5n6o">
        </div>
        <button class="btn" onclick="VerifyView.lookupAndVerify()">Fetch &amp; Verify</button>
      </div>
    `);
  }

  async function doVerify() {
    const jsonText = document.getElementById('verify-json').value.trim();
    if (!jsonText) {
      KredoUI.showError('Please paste attestation JSON');
      return;
    }

    let doc;
    try {
      doc = JSON.parse(jsonText);
    } catch (err) {
      KredoUI.showError('Invalid JSON: ' + err.message);
      return;
    }

    const resultDiv = document.getElementById('verify-result');
    KredoUI.showLoading(resultDiv);

    // Local verification
    let localValid = false;
    let localError = '';
    try {
      if (doc.attestor && doc.signature) {
        localValid = KredoCrypto.verifyAttestation(doc);
      } else if (doc.signature) {
        localError = 'Cannot verify locally — missing attestor field';
      } else {
        localError = 'No signature found';
      }
    } catch (err) {
      localError = err.message;
    }

    // API verification
    let apiResult = null;
    let apiError = '';
    try {
      apiResult = await KredoAPI.verifyDocument(doc);
    } catch (err) {
      apiError = err.message;
    }

    renderResult(doc, localValid, localError, apiResult, apiError);
  }

  async function lookupAndVerify() {
    const id = document.getElementById('verify-id').value.trim();
    if (!id) {
      KredoUI.showError('Please enter an attestation ID');
      return;
    }

    const resultDiv = document.getElementById('verify-result');
    KredoUI.showLoading(resultDiv);

    try {
      const attestation = await KredoAPI.getAttestation(id);
      document.getElementById('verify-json').value = JSON.stringify(attestation, null, 2);

      // Now verify it
      let localValid = false;
      try {
        localValid = KredoCrypto.verifyAttestation(attestation);
      } catch {}

      let apiResult = null;
      let apiError = '';
      try {
        apiResult = await KredoAPI.verifyDocument(attestation);
      } catch (err) {
        apiError = err.message;
      }

      renderResult(attestation, localValid, '', apiResult, apiError);
    } catch (err) {
      resultDiv.innerHTML = `<div class="card" style="border-color:var(--red)"><p style="padding:1rem">${KredoUI.escapeHtml(err.message)}</p></div>`;
    }
  }

  function renderResult(doc, localValid, localError, apiResult, apiError) {
    const resultDiv = document.getElementById('verify-result');

    const localStatus = localError
      ? `<span style="color:var(--yellow)">${KredoUI.escapeHtml(localError)}</span>`
      : (localValid
        ? '<span style="color:var(--green)">Valid</span>'
        : '<span style="color:var(--red)">Invalid</span>');

    const apiStatus = apiError
      ? `<span style="color:var(--yellow)">${KredoUI.escapeHtml(apiError)}</span>`
      : (apiResult?.valid
        ? '<span style="color:var(--green)">Valid</span>'
        : `<span style="color:var(--red)">Invalid${apiResult?.error ? ': ' + KredoUI.escapeHtml(apiResult.error) : ''}</span>`);

    const overallValid = localValid && apiResult?.valid;
    const borderColor = overallValid ? 'var(--green)' : (localValid || apiResult?.valid ? 'var(--yellow)' : 'var(--red)');

    let html = `
      <div class="card" style="border-color:${borderColor}">
        <div class="card-header" style="color:${borderColor}">
          ${overallValid ? 'Signature Verified' : 'Verification Result'}
        </div>

        <div class="review-panel">
          <div class="review-row">
            <div class="review-key">Local (browser)</div>
            <div class="review-val">${localStatus}</div>
          </div>
          <div class="review-row">
            <div class="review-key">API (server)</div>
            <div class="review-val">${apiStatus}</div>
          </div>`;

    if (apiResult) {
      if (apiResult.type) {
        html += `<div class="review-row"><div class="review-key">Document Type</div><div class="review-val">${KredoUI.escapeHtml(apiResult.type)}</div></div>`;
      }
      if (apiResult.attestation_type) {
        html += `<div class="review-row"><div class="review-key">Attestation Type</div><div class="review-val">${KredoUI.attestationTypeBadge(apiResult.attestation_type)}</div></div>`;
      }
      if (apiResult.subject) {
        html += `<div class="review-row"><div class="review-key">Subject</div><div class="review-val" style="font-family:var(--mono);font-size:0.8rem">${KredoUI.shortKey(apiResult.subject)}</div></div>`;
      }
      if (apiResult.attestor) {
        html += `<div class="review-row"><div class="review-key">Attestor</div><div class="review-val" style="font-family:var(--mono);font-size:0.8rem">${KredoUI.shortKey(apiResult.attestor)}</div></div>`;
      }
      if (apiResult.expired != null) {
        html += `<div class="review-row"><div class="review-key">Expired</div><div class="review-val">${apiResult.expired ? '<span style="color:var(--red)">Yes</span>' : '<span style="color:var(--green)">No</span>'}</div></div>`;
      }
      if (apiResult.evidence_score != null) {
        html += `<div class="review-row"><div class="review-key">Evidence Score</div><div class="review-val">${Math.round(apiResult.evidence_score * 100)}%</div></div>`;
      }
    }

    // Document details
    if (doc.id) {
      html += `<div class="review-row"><div class="review-key">ID</div><div class="review-val" style="font-family:var(--mono);font-size:0.85rem">${KredoUI.escapeHtml(doc.id)}</div></div>`;
    }
    if (doc.issued) {
      html += `<div class="review-row"><div class="review-key">Issued</div><div class="review-val">${KredoUI.formatDateTime(doc.issued)}</div></div>`;
    }
    if (doc.expires) {
      html += `<div class="review-row"><div class="review-key">Expires</div><div class="review-val">${KredoUI.formatDateTime(doc.expires)}</div></div>`;
    }

    html += `</div></div>`;
    resultDiv.innerHTML = html;
  }

  return { render, doVerify, lookupAndVerify };
})();
