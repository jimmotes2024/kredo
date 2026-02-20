/**
 * Kredo Web App — Discovery API client
 *
 * All calls to https://api.aikredo.com
 */

const KredoAPI = (() => {
  const BASE = 'https://api.aikredo.com';

  async function request(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) {
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(BASE + path, opts);
    } catch (err) {
      const networkErr = new Error('Network error: could not reach Kredo API');
      networkErr.status = 0;
      networkErr.cause = err;
      throw networkErr;
    }

    const contentType = res.headers.get('content-type') || '';
    let data = {};
    try {
      if (contentType.includes('application/json')) {
        data = await res.json();
      } else {
        const raw = await res.text();
        if (raw) data = { detail: raw };
      }
    } catch {
      data = {};
    }

    if (!res.ok) {
      const msg = data.detail || data.error || JSON.stringify(data);
      const err = new Error(msg);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // --- Health ---

  function health() {
    return request('GET', '/health');
  }

  // --- Registration ---

  function register(pubkey, name, type) {
    return request('POST', '/register', { pubkey, name, type });
  }

  function updateRegistration(pubkey, name, type, signature) {
    return request('POST', '/register/update', { pubkey, name, type, signature });
  }

  function listAgents(limit = 50, offset = 0) {
    return request('GET', `/agents?limit=${limit}&offset=${offset}`);
  }

  function getAgent(pubkey) {
    return request('GET', `/agents/${encodeURIComponent(pubkey)}`);
  }

  // --- Attestations ---

  function submitAttestation(attestation) {
    return request('POST', '/attestations', attestation);
  }

  function getAttestation(id) {
    return request('GET', `/attestations/${encodeURIComponent(id)}`);
  }

  // --- Verification ---

  function verifyDocument(document) {
    return request('POST', '/verify', document);
  }

  // --- Search ---

  function search(params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') {
        qs.set(k, v);
      }
    }
    const query = qs.toString();
    return request('GET', '/search' + (query ? '?' + query : ''));
  }

  // --- Taxonomy ---

  let _taxonomyCache = null;

  async function getTaxonomy() {
    if (_taxonomyCache) return _taxonomyCache;
    _taxonomyCache = await request('GET', '/taxonomy');
    return _taxonomyCache;
  }

  function getTaxonomyDomain(domain) {
    return request('GET', `/taxonomy/${encodeURIComponent(domain)}`);
  }

  function createDomain(id, label, pubkey, signature) {
    _taxonomyCache = null;
    return request('POST', '/taxonomy/domains', { id, label, pubkey, signature });
  }

  function createSkill(domain, id, pubkey, signature) {
    _taxonomyCache = null;
    return request('POST', `/taxonomy/domains/${encodeURIComponent(domain)}/skills`, { id, pubkey, signature });
  }

  function deleteDomain(domain, pubkey, signature) {
    _taxonomyCache = null;
    return request('DELETE', `/taxonomy/domains/${encodeURIComponent(domain)}`, { pubkey, signature });
  }

  function deleteSkill(domain, skill, pubkey, signature) {
    _taxonomyCache = null;
    return request('DELETE', `/taxonomy/domains/${encodeURIComponent(domain)}/skills/${encodeURIComponent(skill)}`, { pubkey, signature });
  }

  // --- Profiles ---

  function getProfile(pubkey) {
    return request('GET', `/agents/${encodeURIComponent(pubkey)}/profile`);
  }

  // --- Trust ---

  function whoAttested(pubkey) {
    return request('GET', `/trust/who-attested/${encodeURIComponent(pubkey)}`);
  }

  function attestedBy(pubkey) {
    return request('GET', `/trust/attested-by/${encodeURIComponent(pubkey)}`);
  }

  function trustAnalysis(pubkey) {
    return request('GET', `/trust/analysis/${encodeURIComponent(pubkey)}`);
  }

  function trustRings() {
    return request('GET', '/trust/rings');
  }

  function networkHealth() {
    return request('GET', '/trust/network-health');
  }

  // --- Ownership / Accountability ---

  function ownershipClaim(claim_id, agent_pubkey, human_pubkey, signature) {
    const payload = { agent_pubkey, human_pubkey, signature };
    if (claim_id) payload.claim_id = claim_id;
    return request('POST', '/ownership/claim', payload);
  }

  function ownershipConfirm(claim_id, human_pubkey, signature, contact_email) {
    const payload = { claim_id, human_pubkey, signature };
    if (contact_email) payload.contact_email = contact_email;
    return request('POST', '/ownership/confirm', payload);
  }

  function ownershipRevoke(claim_id, revoker_pubkey, reason, signature) {
    return request('POST', '/ownership/revoke', { claim_id, revoker_pubkey, reason, signature });
  }

  function ownershipForAgent(agent_pubkey, include_history = true) {
    return request(
      'GET',
      `/ownership/agent/${encodeURIComponent(agent_pubkey)}?include_history=${include_history ? 'true' : 'false'}`
    );
  }

  // --- Integrity ---

  function setIntegrityBaseline(baseline_id, agent_pubkey, owner_pubkey, file_hashes, signature) {
    const payload = { agent_pubkey, owner_pubkey, file_hashes, signature };
    if (baseline_id) payload.baseline_id = baseline_id;
    return request('POST', '/integrity/baseline/set', payload);
  }

  function integrityCheck(agent_pubkey, file_hashes, signature) {
    return request('POST', '/integrity/check', { agent_pubkey, file_hashes, signature });
  }

  function integrityStatus(agent_pubkey) {
    return request('GET', `/integrity/status/${encodeURIComponent(agent_pubkey)}`);
  }

  // --- Risk signals ---

  function sourceAnomalies(params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') {
        qs.set(k, String(v));
      }
    }
    const query = qs.toString();
    return request('GET', '/risk/source-anomalies' + (query ? '?' + query : ''));
  }

  // --- Revocations / Disputes ---

  function revoke(revocation) {
    return request('POST', '/revoke', revocation);
  }

  function dispute(disputeDoc) {
    return request('POST', '/dispute', disputeDoc);
  }

  return {
    health,
    register,
    updateRegistration,
    listAgents,
    getAgent,
    submitAttestation,
    getAttestation,
    verifyDocument,
    search,
    getTaxonomy,
    getTaxonomyDomain,
    createDomain,
    createSkill,
    deleteDomain,
    deleteSkill,
    getProfile,
    whoAttested,
    attestedBy,
    trustAnalysis,
    trustRings,
    networkHealth,
    ownershipClaim,
    ownershipConfirm,
    ownershipRevoke,
    ownershipForAgent,
    setIntegrityBaseline,
    integrityCheck,
    integrityStatus,
    sourceAnomalies,
    revoke,
    dispute,
  };
})();
