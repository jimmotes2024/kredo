/**
 * Kredo Web App — Discussion view
 *
 * Topic list with comment counts, comment stream per topic, guest + verified posting.
 */

const DiscussView = (() => {
  let currentTopic = null;

  function render() {
    currentTopic = null;
    const container = document.getElementById('view');
    KredoUI.showLoading(container);
    loadTopics();
  }

  async function loadTopics() {
    const container = document.getElementById('view');
    try {
      const topics = await KredoAPI.getDiscussionTopics();
      let html = '<h1 class="page-title">Discussion</h1>';
      html += '<p class="page-subtitle">Talk about trust, reputation, and the Kredo protocol. No install required — just pick a topic.</p>';
      html += '<div class="discuss-topics">';
      for (const t of topics) {
        html += `
          <div class="topic-card" onclick="DiscussView.openTopic('${KredoUI.escapeHtml(t.id)}')">
            <div class="topic-card-header">
              <h3>${KredoUI.escapeHtml(t.label)}</h3>
              <span class="topic-count">${t.comment_count}</span>
            </div>
            <p>${KredoUI.escapeHtml(t.description)}</p>
          </div>`;
      }
      html += '</div>';
      container.innerHTML = html;
    } catch (err) {
      container.innerHTML = `<div class="empty-state">Failed to load topics: ${KredoUI.escapeHtml(err.message)}</div>`;
    }
  }

  async function openTopic(topicId) {
    currentTopic = topicId;
    const container = document.getElementById('view');
    KredoUI.showLoading(container);
    try {
      const data = await KredoAPI.getTopicComments(topicId);
      renderTopic(data);
    } catch (err) {
      container.innerHTML = `<div class="empty-state">Failed to load comments: ${KredoUI.escapeHtml(err.message)}</div>`;
    }
  }

  function renderTopic(data) {
    const container = document.getElementById('view');
    const identity = KredoStorage.getIdentity();
    const hasIdentity = !!identity;

    let html = `<div class="discuss-topic-view">`;
    html += `<button class="btn btn-sm" onclick="DiscussView.render()" style="margin-bottom:1rem">&larr; All Topics</button>`;
    html += `<h1 class="page-title">${KredoUI.escapeHtml(getTopicLabel(data.topic))}</h1>`;
    html += `<p class="page-subtitle">${data.total} comment${data.total !== 1 ? 's' : ''}</p>`;

    // Comment form
    html += `<div class="card comment-form-card">
      <div class="card-header">Post a Comment</div>
      <div class="form-group">
        <label for="comment-name">Display Name</label>
        <input type="text" id="comment-name" name="author_name" maxlength="100"
          value="${hasIdentity ? KredoUI.escapeHtml(identity.name) : ''}"
          placeholder="Your name" />
      </div>
      <div class="form-group">
        <label for="comment-body">Comment</label>
        <textarea id="comment-body" name="body" maxlength="2000" rows="3"
          placeholder="Share your thoughts..."></textarea>
        <div class="hint"><span id="char-count">0</span>/2000</div>
      </div>`;

    if (hasIdentity) {
      html += `<div class="form-group" style="display:flex;align-items:center;gap:0.5rem">
        <input type="checkbox" id="comment-sign" checked />
        <label for="comment-sign" style="margin:0;cursor:pointer">Sign with my identity <span class="badge badge-verified">verified</span></label>
      </div>`;
    }

    html += `<button class="btn btn-primary" id="comment-submit" onclick="DiscussView.submitComment()">Post Comment</button>
    </div>`;

    // Comments list
    html += '<div class="comment-list">';
    if (data.comments.length === 0) {
      html += '<div class="empty-state">No comments yet. Be the first!</div>';
    } else {
      for (const c of data.comments) {
        html += renderComment(c);
      }
    }
    html += '</div></div>';

    container.innerHTML = html;

    // Wire up char counter
    const bodyEl = document.getElementById('comment-body');
    if (bodyEl) {
      bodyEl.addEventListener('input', () => {
        const counter = document.getElementById('char-count');
        if (counter) counter.textContent = bodyEl.value.length;
      });
    }
  }

  function renderComment(c) {
    const verified = c.is_verified;
    const badge = verified
      ? '<span class="badge badge-verified">verified</span>'
      : '<span class="badge badge-guest">guest</span>';
    const authorKey = verified && c.author_pubkey
      ? ` <span class="comment-pubkey">${KredoUI.escapeHtml(KredoUI.shortKey(c.author_pubkey))}</span>`
      : '';
    const time = KredoUI.timeAgo(c.created_at);

    return `<div class="comment">
      <div class="comment-header">
        <span class="comment-author">${KredoUI.escapeHtml(c.author_name)}</span>
        ${badge}${authorKey}
        <span class="comment-time">${KredoUI.escapeHtml(time)}</span>
      </div>
      <div class="comment-body">${KredoUI.escapeHtml(c.body)}</div>
    </div>`;
  }

  function getTopicLabel(topicId) {
    const labels = {
      introductions: 'Introductions',
      'protocol-design': 'Protocol Design',
      'attack-vectors': 'Attack Vectors',
      'feature-requests': 'Feature Requests',
      general: 'General',
    };
    return labels[topicId] || topicId;
  }

  async function submitComment() {
    const nameEl = document.getElementById('comment-name');
    const bodyEl = document.getElementById('comment-body');
    const signEl = document.getElementById('comment-sign');
    const submitBtn = document.getElementById('comment-submit');

    const authorName = (nameEl?.value || '').trim();
    const body = (bodyEl?.value || '').trim();

    if (!authorName) { KredoUI.showWarning('Please enter a display name.'); return; }
    if (!body) { KredoUI.showWarning('Please enter a comment.'); return; }
    if (body.length > 2000) { KredoUI.showWarning('Comment must be 2000 characters or fewer.'); return; }

    const wantSign = signEl?.checked && KredoStorage.hasIdentity();
    const commentData = { author_name: authorName, body };

    if (wantSign) {
      const identity = KredoStorage.getIdentity();
      let secretKey;
      if (KredoStorage.isEncrypted()) {
        const passphrase = await KredoUI.requestPassphrase('Enter passphrase to sign comment');
        if (passphrase === null) return;
        secretKey = await KredoStorage.getSecretKey(passphrase);
      } else {
        secretKey = await KredoStorage.getSecretKey();
      }
      if (!secretKey) {
        KredoUI.showError('Could not access signing key. Posting as guest.');
      } else {
        commentData.author_pubkey = identity.pubkey;
        const signPayload = {
          topic: currentTopic,
          author_pubkey: identity.pubkey,
          body: body,
        };
        commentData.signature = KredoCrypto.sign(signPayload, secretKey);
      }
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Posting...';

    try {
      await KredoAPI.postDiscussionComment(currentTopic, commentData);
      KredoUI.showSuccess('Comment posted!');
      openTopic(currentTopic);
    } catch (err) {
      KredoUI.showError(err.message || 'Failed to post comment.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Post Comment';
    }
  }

  return { render, openTopic, submitComment };
})();
