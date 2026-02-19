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
    'attest':    { view: 'attest',   label: 'Attest',    module: () => AttestView },
    'browse':    { view: 'browse',   label: 'Browse',    module: () => BrowseView },
    'verify':    { view: 'verify',   label: 'Verify',    module: () => VerifyView },
    'taxonomy':  { view: 'taxonomy', label: 'Taxonomy',  module: () => TaxonomyView },
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
      statusEl.innerHTML = '<span class="dot offline"></span>No identity';
      statusEl.title = 'Create an identity to get started';
    }
  }

  async function checkAPIHealth() {
    const statusEl = document.getElementById('api-status');
    if (!statusEl) return;
    try {
      const data = await KredoAPI.health();
      statusEl.innerHTML = `<span class="dot"></span>API v${KredoUI.escapeHtml(data.version)}`;
      statusEl.title = 'Connected to api.aikredo.com';
    } catch {
      statusEl.innerHTML = '<span class="dot offline"></span>API offline';
      statusEl.title = 'Cannot reach api.aikredo.com';
    }
  }

  function init() {
    window.addEventListener('hashchange', route);

    // If user has identity and lands on setup, redirect to dashboard
    const hash = getHash();
    if ((!hash || hash === 'setup') && KredoStorage.hasIdentity()) {
      navigate('dashboard');
      return;
    }

    // If user has no identity and lands on non-setup page, redirect to setup
    if (hash && hash !== 'setup' && !KredoStorage.hasIdentity()) {
      navigate('setup');
      return;
    }

    updateIdentityStatus();
    checkAPIHealth();
    route();
  }

  return { init, navigate, updateIdentityStatus, routes, getHashParam };
})();

// Boot on DOM ready
document.addEventListener('DOMContentLoaded', KredoApp.init);
