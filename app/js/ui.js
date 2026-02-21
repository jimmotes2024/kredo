/**
 * Kredo Web App — Shared UI helpers
 *
 * Alerts, loading indicators, tables, common DOM patterns.
 */

const KredoUI = (() => {
  // --- Alert system ---

  function showAlert(message, type = 'info', duration = 5000) {
    const container = document.getElementById('alerts');
    if (!container) return;

    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.innerHTML = `
      <span>${escapeHtml(message)}</span>
      <button class="alert-close" onclick="this.parentElement.remove()">&times;</button>
    `;
    container.appendChild(alert);

    if (duration > 0) {
      setTimeout(() => alert.remove(), duration);
    }
  }

  function showSuccess(msg, dur) { showAlert(msg, 'success', dur); }
  function showError(msg, dur) { showAlert(msg, 'error', dur || 8000); }
  function showWarning(msg, dur) { showAlert(msg, 'warning', dur); }
  function showInfo(msg, dur) { showAlert(msg, 'info', dur); }

  // --- Loading indicator ---

  function showLoading(container) {
    if (typeof container === 'string') {
      container = document.getElementById(container);
    }
    if (!container) return;
    const loader = document.createElement('div');
    loader.className = 'loading';
    loader.innerHTML = '<div class="spinner"></div><span>Loading...</span>';
    container.innerHTML = '';
    container.appendChild(loader);
  }

  function hideLoading(container) {
    if (typeof container === 'string') {
      container = document.getElementById(container);
    }
    if (!container) return;
    const loader = container.querySelector('.loading');
    if (loader) loader.remove();
  }

  // --- HTML helpers ---

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function shortKey(pubkey) {
    if (!pubkey) return '';
    const hex = pubkey.replace('ed25519:', '');
    return 'ed25519:' + hex.substring(0, 8) + '...' + hex.substring(hex.length - 4);
  }

  // --- Proficiency display ---

  const PROFICIENCY_LABELS = {
    1: 'Novice',
    2: 'Competent',
    3: 'Proficient',
    4: 'Expert',
    5: 'Authority',
  };

  function proficiencyBar(level, maxWidth) {
    const pct = (level / 5) * 100;
    const label = PROFICIENCY_LABELS[level] || `${level}`;
    return `<div class="proficiency-bar" title="${label} (${level}/5)">
      <div class="proficiency-fill" style="width:${pct}%"></div>
      <span class="proficiency-label">${label}</span>
    </div>`;
  }

  function proficiencyBadge(level) {
    const label = PROFICIENCY_LABELS[level] || `${level}`;
    return `<span class="badge badge-prof-${level}">${label}</span>`;
  }

  // --- Score bar ---

  function scoreBar(value, label) {
    const pct = Math.round(value * 100);
    return `<div class="score-row">
      <span class="score-label">${escapeHtml(label)}</span>
      <div class="score-bar"><div class="score-fill" style="width:${pct}%"></div></div>
      <span class="score-value">${pct}%</span>
    </div>`;
  }

  // --- Tables ---

  function buildTable(columns, rows, options = {}) {
    const { emptyMessage = 'No data', className = '' } = options;
    if (rows.length === 0) {
      return `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
    }
    let html = `<table class="data-table ${className}"><thead><tr>`;
    for (const col of columns) {
      html += `<th>${escapeHtml(col.label)}</th>`;
    }
    html += '</tr></thead><tbody>';
    for (const row of rows) {
      html += '<tr>';
      for (const col of columns) {
        const val = col.render ? col.render(row) : escapeHtml(String(row[col.key] || ''));
        html += `<td>${val}</td>`;
      }
      html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
  }

  // --- Date formatting ---

  function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  }

  function formatDateTime(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  function timeAgo(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now - d;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    return formatDate(dateStr);
  }

  // --- Copy to clipboard ---

  async function copyToClipboard(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      showSuccess(`${label || 'Text'} copied to clipboard`);
    } catch {
      // Fallback
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      showSuccess(`${label || 'Text'} copied to clipboard`);
    }
  }

  // --- Passphrase prompt modal ---

  function requestPassphrase(title = 'Enter passphrase', options = {}) {
    return new Promise((resolve) => {
      const allowBlank = !!options.allowBlank;
      const placeholder = options.placeholder || 'Passphrase';
      const submitLabel = options.submitLabel || 'Continue';
      const cancelLabel = options.cancelLabel || 'Cancel';
      const overlayId = 'kredo-passphrase-overlay';

      const existing = document.getElementById(overlayId);
      if (existing) existing.remove();

      const overlay = document.createElement('div');
      overlay.id = overlayId;
      overlay.style.position = 'fixed';
      overlay.style.inset = '0';
      overlay.style.background = 'rgba(0,0,0,0.55)';
      overlay.style.display = 'flex';
      overlay.style.alignItems = 'center';
      overlay.style.justifyContent = 'center';
      overlay.style.zIndex = '9999';

      const panel = document.createElement('div');
      panel.style.width = 'min(92vw, 420px)';
      panel.style.background = 'var(--bg-card)';
      panel.style.border = '1px solid var(--border)';
      panel.style.borderRadius = 'var(--radius)';
      panel.style.padding = '1rem';
      panel.style.boxShadow = 'var(--shadow)';
      panel.innerHTML = `
        <div style="font-weight:600;margin-bottom:0.75rem">${escapeHtml(title)}</div>
        <input id="kredo-passphrase-input" type="password" autocomplete="current-password" placeholder="${escapeHtml(placeholder)}"
          style="width:100%;padding:0.6rem;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg-input);color:var(--text);" />
        <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.85rem">
          <button id="kredo-passphrase-cancel" class="btn">${escapeHtml(cancelLabel)}</button>
          <button id="kredo-passphrase-submit" class="btn btn-primary">${escapeHtml(submitLabel)}</button>
        </div>
      `;

      overlay.appendChild(panel);
      document.body.appendChild(overlay);

      const input = panel.querySelector('#kredo-passphrase-input');
      const cancelBtn = panel.querySelector('#kredo-passphrase-cancel');
      const submitBtn = panel.querySelector('#kredo-passphrase-submit');

      const close = (value) => {
        overlay.remove();
        resolve(value);
      };

      cancelBtn.addEventListener('click', (event) => {
        event.preventDefault();
        close(null);
      });

      submitBtn.addEventListener('click', (event) => {
        event.preventDefault();
        const value = (input.value || '');
        if (!allowBlank && !value.trim()) {
          showWarning('Passphrase is required.');
          input.focus();
          return;
        }
        close(value);
      });

      overlay.addEventListener('click', (event) => {
        if (event.target === overlay) close(null);
      });

      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          const value = (input.value || '');
          if (!allowBlank && !value.trim()) {
            showWarning('Passphrase is required.');
            return;
          }
          close(value);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          close(null);
        }
      });

      setTimeout(() => input.focus(), 0);
    });
  }

  // --- Render view into container ---

  function renderView(html) {
    const container = document.getElementById('view');
    if (container) container.innerHTML = html;
  }

  // --- Form helpers ---

  function getFormValues(formId) {
    const form = document.getElementById(formId);
    if (!form) return {};
    const data = {};
    const inputs = form.querySelectorAll('input, select, textarea');
    for (const input of inputs) {
      if (input.name) {
        if (input.type === 'checkbox') {
          data[input.name] = input.checked;
        } else if (input.type === 'radio') {
          if (input.checked) data[input.name] = input.value;
        } else {
          data[input.name] = input.value;
        }
      }
    }
    return data;
  }

  // --- Type badge ---

  function typeBadge(type) {
    const cls = type === 'human' ? 'badge-human' : 'badge-agent';
    return `<span class="badge ${cls}">${escapeHtml(type)}</span>`;
  }

  // --- Attestation type label ---

  const TYPE_LABELS = {
    'skill_attestation': 'Skill',
    'intellectual_contribution': 'Intellectual',
    'community_contribution': 'Community',
    'behavioral_warning': 'Warning',
  };

  function attestationTypeBadge(type) {
    const label = TYPE_LABELS[type] || type;
    const cls = type === 'behavioral_warning' ? 'badge-warning' : 'badge-attest';
    return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
  }

  return {
    showAlert,
    showSuccess,
    showError,
    showWarning,
    showInfo,
    showLoading,
    hideLoading,
    escapeHtml,
    shortKey,
    PROFICIENCY_LABELS,
    proficiencyBar,
    proficiencyBadge,
    scoreBar,
    buildTable,
    formatDate,
    formatDateTime,
    timeAgo,
    copyToClipboard,
    requestPassphrase,
    renderView,
    getFormValues,
    typeBadge,
    attestationTypeBadge,
    TYPE_LABELS,
  };
})();
