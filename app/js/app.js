/**
 * Kredo Web App — Router and initialization
 *
 * Hash-based SPA routing. No build step, no framework.
 */

const KredoApp = (() => {
  const routes = {
    '':          { view: 'setup',    label: 'Setup',     module: () => SetupView },
    'setup':     { view: 'setup',    label: 'Setup',     module: () => SetupView },
    'dashboard': { view: 'dashboard',label: 'Dashboard', module: () => DashboardView },
    'governance':{ view: 'governance',label: 'Governance', module: () => GovernanceView },
    'attest':    { view: 'attest',   label: 'Attest',    module: () => AttestView },
    'browse':    { view: 'browse',   label: 'Browse',    module: () => BrowseView },
    'verify':    { view: 'verify',   label: 'Verify',    module: () => VerifyView },
    'taxonomy':  { view: 'taxonomy', label: 'Taxonomy',  module: () => TaxonomyView },
    'discuss':   { view: 'discuss',  label: 'Discuss',   module: () => DiscussView },
  };

  let currentView = null;

  function getHash() {
    return window.location.hash.replace('#/', '').replace('#', '').split('?')[0];
  }

  function getHashParam() {
    // Return the portion after the first slash in the route, e.g. #/browse/ed25519:abc → "ed25519:abc"
    const full = getHash();
    const idx = full.indexOf('/');
    return idx >= 0 ? decodeURIComponent(full.slice(idx + 1)) : null;
  }

  function navigate(hash) {
    window.location.hash = '#/' + hash;
  }

  function route() {
    const hash = getHash();
    const baseRoute = hash.split('/')[0];
    const routeInfo = routes[baseRoute] || routes[hash] || routes[''];

    // Default landing: if identity exists and no explicit route, open dashboard.
    // Keep #/setup accessible for backup/export/reset tasks.
    if (hash === '' && KredoStorage.hasIdentity()) {
      navigate('dashboard');
      return;
    }

    // Update nav active state
    document.querySelectorAll('nav a').forEach(a => {
      const href = a.getAttribute('href').replace('#/', '');
      a.classList.toggle('active', href === routeInfo.view);
    });

    // Render view
    const viewModule = routeInfo.module();
    if (viewModule && viewModule.render) {
      currentView = viewModule;
      viewModule.render();
    }
  }

  function updateIdentityStatus() {
    const statusEl = document.getElementById('identity-status');
    if (!statusEl) return;

    const identity = KredoStorage.getIdentity();
    if (identity) {
      statusEl.innerHTML = `<span class="dot"></span>${KredoUI.escapeHtml(identity.name)}`;
      statusEl.title = identity.pubkey;
    } else {
      statusEl.innerHTML = '<span class="dot offline"></span>Not logged in';
      statusEl.title = 'Load identity from Setup to sign attestations';
    }
  }

  async function checkAPIHealth() {
    const statusEl = document.getElementById('api-status');
    if (!statusEl) return;
    try {
      const data = await KredoAPI.health();
      statusEl.innerHTML = '<span class="dot"></span>API online';
      statusEl.title = `Connected to api.aikredo.com (v${KredoUI.escapeHtml(data.version)})`;
    } catch {
      statusEl.innerHTML = '<span class="dot offline"></span>API offline';
      statusEl.title = 'Cannot reach api.aikredo.com';
    }
  }

  function init() {
    window.addEventListener('hashchange', route);
    updateIdentityStatus();
    checkAPIHealth();
    route();
  }

  return { init, navigate, updateIdentityStatus, routes, getHashParam };
})();

// Boot on DOM ready
document.addEventListener('DOMContentLoaded', KredoApp.init);
