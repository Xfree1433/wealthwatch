// ── WealthWatch app.js ────────────────────────────────────────────────────

// ── Theme toggle ─────────────────────────────────────────────────────────
(function() {
  const saved = localStorage.getItem('ww-theme');
  if (saved === 'light') document.body.classList.add('light');
})();
function toggleTheme() {
  document.body.classList.toggle('light');
  localStorage.setItem('ww-theme', document.body.classList.contains('light') ? 'light' : 'dark');
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = document.body.classList.contains('light') ? '&#9728;' : '&#9790;';
}
// Set correct icon on load
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = document.body.classList.contains('light') ? '&#9728;' : '&#9790;';
});

const todayEl = document.getElementById('today-date');
if (todayEl) {
  todayEl.textContent = new Date().toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'
  });
}

function fmtCurrency(n, showSign = false) {
  const abs = Math.abs(n);
  const str = abs.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
  if (showSign && n < 0) return '−' + str;
  return str;
}

function fmtDate(d) {
  if (!d) return '—';
  const dt = new Date(d + 'T00:00:00');
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtShortDate(d) {
  if (!d) return '—';
  const dt = new Date(d + 'T00:00:00');
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

async function apiFetch(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

function showLoading(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div> Loading…</div>';
}

function showError(el, msg = 'Failed to load data.') {
  el.innerHTML = `<div class="loading" style="color:var(--red)">${msg}</div>`;
}

function progressColor(pct) {
  if (pct >= 100) return 'var(--red)';
  if (pct >= 80)  return '#e08a5c';
  if (pct >= 60)  return 'var(--accent)';
  return 'var(--green)';
}

function acctLabel(type) {
  return { checking: 'Checking', savings: 'Savings', credit: 'Credit',
    investment: 'Investment', real_estate: 'Real Estate', loan: 'Loan' }[type] || type;
}

// ── Modal helpers ────────────────────────────────────────────────────────
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function modalShell(id, title, bodyHtml, footerHtml) {
  return `<div class="modal-overlay" id="${id}" onclick="if(event.target===this)closeModal('${id}')">
    <div class="modal">
      <div class="modal-header">
        <span class="modal-title">${title}</span>
        <button class="modal-close" onclick="closeModal('${id}')">&times;</button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-footer">${footerHtml}</div>
    </div>
  </div>`;
}

async function apiPost(url, data) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.error || `API error ${res.status}`); }
  return res.json();
}

async function apiPut(url, data) {
  const res = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.error || `API error ${res.status}`); }
  return res.json();
}

async function apiDelete(url) {
  const res = await fetch(url, { method: 'DELETE' });
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.error || `API error ${res.status}`); }
  return res.json();
}

function confirmDelete(itemName, onConfirm) {
  const id = 'confirm-delete-modal';
  let el = document.getElementById(id);
  if (el) el.remove();
  const html = modalShell(id, 'Confirm Delete',
    `<p class="confirm-text">Are you sure you want to delete <strong>${itemName}</strong>? This cannot be undone.</p>`,
    `<button class="btn btn-secondary" onclick="closeModal('${id}')">Cancel</button>
     <button class="btn btn-danger" id="confirm-delete-btn">Delete</button>`
  );
  document.body.insertAdjacentHTML('beforeend', html);
  openModal(id);
  document.getElementById('confirm-delete-btn').onclick = async () => {
    closeModal(id);
    await onConfirm();
  };
}

function todayISO() { return new Date().toISOString().slice(0, 10); }

// ── XSS helpers ──────────────────────────────────────────────────────────
function escHtml(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function escAttr(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Toast notifications ──────────────────────────────────────────────
(function() {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
})();

function toast(msg, type = 'success', duration = 3000) {
  const icons = { success: '&#10003;', error: '&#10007;', info: '&#8505;' };
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span>${escHtml(msg)}</span>`;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 200); }, duration);
}

// ── Pagination helper ────────────────────────────────────────────────
function paginate(items, page, perPage) {
  const total = items.length;
  const pages = Math.ceil(total / perPage) || 1;
  page = Math.max(1, Math.min(page, pages));
  const start = (page - 1) * perPage;
  return { data: items.slice(start, start + perPage), page, pages, total, start };
}

function paginationHtml(p, onPageFn) {
  if (p.pages <= 1) return '';
  let html = '<div class="pagination">';
  html += `<button class="page-btn" onclick="${onPageFn}(${p.page - 1})" ${p.page <= 1 ? 'disabled' : ''}>&laquo;</button>`;
  const maxBtns = 7;
  let startP = Math.max(1, p.page - Math.floor(maxBtns / 2));
  let endP = Math.min(p.pages, startP + maxBtns - 1);
  if (endP - startP < maxBtns - 1) startP = Math.max(1, endP - maxBtns + 1);
  for (let i = startP; i <= endP; i++) {
    html += `<button class="page-btn ${i === p.page ? 'active' : ''}" onclick="${onPageFn}(${i})">${i}</button>`;
  }
  html += `<button class="page-btn" onclick="${onPageFn}(${p.page + 1})" ${p.page >= p.pages ? 'disabled' : ''}>&raquo;</button>`;
  html += `<span class="page-info">${p.start + 1}–${Math.min(p.start + p.data.length, p.total)} of ${p.total}</span>`;
  html += '</div>';
  return html;
}

// ── Sorting helper ───────────────────────────────────────────────────
function sortBy(arr, key, dir) {
  return [...arr].sort((a, b) => {
    let va = a[key], vb = b[key];
    if (va == null) va = '';
    if (vb == null) vb = '';
    if (typeof va === 'number' && typeof vb === 'number') return dir === 'asc' ? va - vb : vb - va;
    va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
    if (va < vb) return dir === 'asc' ? -1 : 1;
    if (va > vb) return dir === 'asc' ? 1 : -1;
    return 0;
  });
}

function sortHeader(label, key, currentKey, currentDir, onSortFn) {
  const active = currentKey === key;
  const arrow = active ? (currentDir === 'asc' ? '&#9650;' : '&#9660;') : '&#9650;';
  const nextDir = active && currentDir === 'asc' ? 'desc' : 'asc';
  return `<th class="sortable ${active ? 'sorted' : ''}" onclick="${onSortFn}('${key}','${nextDir}')">${label}<span class="sort-arrow">${arrow}</span></th>`;
}
