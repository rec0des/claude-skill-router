/* Health view: audit and reversible management of skills. Reuses $, esc, fmt, icon, api, toast and busy from app.js. */
const health = { report: null, filter: 'problems', query: '', focused: null, armed: null };
const STATUS = {
  insecure: 'Insecure', risky: 'Risky', obsolete: 'Obsolete', outdated: 'Outdated', review: 'Review', ok: 'OK',
};
const FILTERS = [
  ['problems', 'Issues', (r) => r.status !== 'ok' && !(r.status === 'outdated' && r.source === 'plugin')],
  ['security', 'Security', (r) => r.status === 'insecure' || r.status === 'risky'],
  ['obsolete', 'Obsolete', (r) => r.status === 'obsolete'],
  ['outdated', 'Outdated', (r) => r.status === 'outdated'],
  ['review', 'To review', (r) => r.status === 'review'],
  ['blocked', 'Blocked', (r) => r.blocked],
  ['all', 'All', () => true],
];
const SEV = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' };

function ago(ts) {
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function hvFiltered() {
  const fn = FILTERS.find((f) => f[0] === health.filter)[2];
  return health.report.skills.filter((r) => fn(r) && (!health.query || `${r.invoke} ${r.description}`.toLowerCase().includes(health.query)));
}

function hvRenderFilters() {
  const rows = health.report.skills;
  $('#hvFilters').innerHTML = FILTERS.map(([id, label, fn]) => {
    const n = rows.filter(fn).length;
    return `<button role="tab" data-hf="${id}" aria-selected="${health.filter === id}">${label}${id !== 'all' && n ? ` <b>${n}</b>` : ''}</button>`;
  }).join('');
  document.querySelectorAll('[data-hf]').forEach((b) => { b.onclick = () => { health.filter = b.dataset.hf; hvRender(); }; });
}

function statusChip(r) {
  return r.blocked ? '<span class="status blocked">Blocked</span>' : `<span class="status ${r.status}">${STATUS[r.status]}</span>`;
}

function hvRenderRows() {
  const rows = hvFiltered();
  const box = $('#hvRows');
  if (!rows.length) {
    box.innerHTML = `<div class="empty"><p><strong>${health.filter === 'problems' ? 'No issues found.' : 'Nothing under this filter.'}</strong></p><p>The audit runs by itself once a day when Claude starts.</p></div>`;
    return;
  }
  box.innerHTML = rows.slice(0, 300).map((r) => {
    const issue = r.issues[0];
    return `<div class="hrow ${health.focused === r.invoke ? 'focus' : ''}" data-hs="${esc(r.invoke)}" tabindex="0">
      ${statusChip(r)}
      <div style="min-width:0"><div class="row-title"><strong>${esc(r.invoke)}</strong><span class="tag">${SOURCE[r.source] || esc(r.source)}</span></div><p>${esc(issue ? issue.why : 'No issues.')}</p></div>
      <span class="where">${esc(issue?.where || '')}</span>
    </div>`;
  }).join('');
}

function actionButton(action, label, iconId, target, danger) {
  const armed = health.armed === `${action}:${target}`;
  return `<button class="btn ${armed ? 'confirm' : danger ? 'danger' : ''}" data-act="${action}" data-target="${esc(target)}">${icon(iconId)}<span>${armed ? 'Confirm' : label}</span></button>`;
}

function hvRenderDetail() {
  const r = health.report.skills.find((x) => x.invoke === health.focused);
  const box = $('#hvDetail');
  if (!r) { box.innerHTML = '<p class="muted">Select a skill to see its issues, the evidence and the available actions.</p>'; return; }
  const pluginKey = r.source === 'plugin' ? (health.report.plugins.find((p) => p.plugin === r.invoke.split(':')[0]) || {}).key : null;
  const acts = [];
  acts.push(r.blocked ? actionButton('unblock', 'Unblock', 'check', r.invoke) : actionButton('block', 'Block in router', 'ban', r.invoke));
  if (r.actions.includes('quarantine')) acts.push(actionButton('quarantine', 'Quarantine', 'box', r.invoke, true));
  if (pluginKey && r.actions.includes('disable_plugin')) acts.push(actionButton('disable_plugin', 'Disable plugin', 'ban', pluginKey, true));
  const plugin = pluginKey && health.report.plugins.find((p) => p.key === pluginKey);
  if (plugin?.outdated) acts.push(actionButton('update_plugin', 'Update plugin', 'up', pluginKey));
  if (pluginKey) acts.push(actionButton('uninstall_plugin', 'Uninstall plugin', 'ban', pluginKey, true));
  if (r.protected) acts.push(actionButton('unprotect', 'Remove protection', 'shield', r.invoke));
  box.innerHTML = `
    <div class="detail-head"><h2>${esc(r.invoke)}</h2><div class="btn-row">${r.protected ? '<span class="status protected">Protected</span>' : ''}${statusChip(r)}</div></div>
    <p class="note">${esc(r.description)}</p>
    <ul class="issues">${r.issues.length ? r.issues.map((i) => `<li>
      <span class="sev ${i.severity}">${SEV[i.severity]} · ${esc(i.why)}</span>
      ${i.where ? `<span class="loc">${esc(i.where)}</span>` : ''}
      ${i.evidence ? `<pre>${esc(i.evidence)}</pre>` : ''}
    </li>`).join('') : '<li>No issues detected.</li>'}</ul>
    <div class="hv-actions">${acts.join('')}</div>
    <p class="note">${pluginKey ? 'Plugin skills cannot be deleted one by one: block the skill in the router, or disable or uninstall the whole plugin.' : 'Quarantine removes the skill from Claude and keeps it in the router. To delete it for good, use Delete in the Quarantine section.'}</p>`;
}

function hvRenderPlan() {
  const plan = health.report.cleanup_plan || [];
  const label = { quarantine: 'Quarantine', block: 'Block' };
  $('#hvDetail').innerHTML = `
    <div class="detail-head"><h2>Cleanup</h2><span class="badge">${plan.length} ${plan.length === 1 ? 'skill' : 'skills'}</span></div>
    <p class="note">Obsolete and insecure skills. Local ones go to quarantine (removed from Claude, restorable); plugin skills are blocked in the router. Restored or unblocked skills become protected and are never cleaned up again.</p>
    ${plan.length ? `<ul class="plan">${plan.map((p) => `<li><span class="act ${p.action}">${label[p.action]}</span><div><b>${esc(p.invoke)}</b><span>${esc(p.why)}</span></div></li>`).join('')}</ul>
    <div class="hv-actions">${actionButton('cleanup', 'Apply cleanup', 'box', 'all', true)}</div>` : '<p class="muted">Nothing to clean up.</p>'}`;
}

function hvRenderSide() {
  const plugins = health.report.plugins.filter((p) => p.outdated || p.blocked);
  $('#hvPlugins').innerHTML = plugins.length
    ? plugins.map((p) => `<li><div><b>${esc(p.key)}</b><span>${esc(p.installed || '?')} → ${esc(p.latest || '?')}${p.blocked ? ' · blocklist' : ''}</span></div>${actionButton('update_plugin', 'Update', 'up', p.key)}</li>`).join('')
    : '<li><span>Every plugin matches its marketplace version.</span></li>';
  const q = health.report.quarantine || [];
  $('#hvQuarantine').innerHTML = q.length
    ? q.map((x) => `<li><div><b>${esc(x.invoke)}</b><span>${esc(x.reason || 'manual')} · ${x.days ? `${x.days} days ago` : 'today'}</span></div><div class="btn-row">${actionButton('restore', 'Restore', 'refresh', x.id)}${actionButton('purge', 'Delete', 'ban', x.id, true)}</div></li>`).join('')
    : '<li><span>Empty.</span></li>';
}

function hvRender() {
  if (!health.report) return;
  hvRenderFilters(); hvRenderRows();
  if (health.focused === '__plan') hvRenderPlan(); else hvRenderDetail();
  hvRenderSide();
  const n = (health.report.cleanup_plan || []).length;
  $('#hvClean').disabled = !n;
  $('#hvClean').querySelector('span').textContent = n ? `Clean up (${n})` : 'Nothing to clean';
  $('#hvAuto').checked = !!health.report.auto_cleanup;
}

function hvBadge() {
  const s = health.report.summary;
  const n = (s.insecure || 0) + (s.risky || 0) + (s.obsolete || 0);
  $('#healthBadge').hidden = !n;
  $('#healthBadge').textContent = n;
}

function hvShow() {
  const r = health.report;
  $('#hvMeta').textContent = `${fmt(r.skills.length)} skills · audited ${ago(r.at)} · ${r.seconds} s`;
  if (!health.focused) health.focused = hvFiltered()[0]?.invoke || null;
  hvBadge(); hvRender();
}

async function hvLoad(force = false) {
  const btn = $('#hvRefresh');
  busy(btn, true, force ? 'Auditing…' : null);
  if (force || !health.report) $('#hvMeta').textContent = 'Auditing every skill (up to 30 s)…';
  try {
    health.report = force ? await api('/api/health/refresh', {}) : await api('/api/health');
    hvShow();
  } catch (e) {
    $('#hvRows').innerHTML = `<div class="empty"><p class="error">${esc(e.message)}</p></div>`;
  } finally {
    busy(btn, false, 'Audit now');
  }
}

const NEEDS_CONFIRM = new Set(['quarantine', 'disable_plugin', 'update_plugin', 'uninstall_plugin', 'restore', 'purge', 'cleanup']);
const rerender = () => { if (health.focused === '__plan') hvRenderPlan(); else hvRenderDetail(); hvRenderSide(); };

async function hvAct(button) {
  const { act, target } = button.dataset;
  const key = `${act}:${target}`;
  if (NEEDS_CONFIRM.has(act) && health.armed !== key) {
    health.armed = key;
    rerender();
    setTimeout(() => { if (health.armed === key) { health.armed = null; rerender(); } }, 4000);
    return;
  }
  health.armed = null;
  busy(button, true);
  try {
    if (act === 'cleanup') {
      const res = await api('/api/health/cleanup', { apply: true });
      health.report = res.report;
      health.focused = null;
      toast(`${res.done.length} cleaned up${res.failed.length ? `, ${res.failed.length} failed` : ''}. Restart Claude to apply.`);
      hvShow();
      return;
    }
    const res = await api('/api/health/action', { action: act, target });
    toast(res.message);
    if (act === 'purge' || act === 'restore') health.focused = null;
    // Block/unblock only change local state; plugin and quarantine actions change the inventory and need a new audit.
    await hvLoad(['quarantine', 'restore', 'disable_plugin', 'enable_plugin', 'update_plugin', 'uninstall_plugin'].includes(act));
  } catch (e) {
    toast(e.message);
    hvRender();
  }
}

document.addEventListener('click', (e) => {
  const actBtn = e.target.closest('[data-act]');
  if (actBtn) { hvAct(actBtn); return; }
  const row = e.target.closest('[data-hs]');
  if (row) { health.focused = row.dataset.hs; health.armed = null; hvRenderRows(); hvRenderDetail(); }
});
document.addEventListener('keydown', (e) => {
  const row = e.target.closest?.('[data-hs]');
  if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); row.click(); }
});
$('#hvSearch').addEventListener('input', (e) => { health.query = e.target.value.trim().toLowerCase(); hvRenderRows(); });
$('#hvRefresh').onclick = () => hvLoad(true);
$('#hvClean').onclick = () => { health.focused = '__plan'; health.armed = null; hvRenderRows(); hvRenderPlan(); };
$('#hvAuto').onchange = async (e) => {
  const on = e.target.checked;
  try {
    const res = await api('/api/health/action', { action: 'auto_cleanup', target: on ? 'on' : 'off' });
    health.report.auto_cleanup = on;
    toast(`${res.message}${on ? ' Runs during the daily audit when Claude starts.' : ''}`);
  } catch (err) { e.target.checked = !on; toast(err.message); }
};

function setView(view) {
  document.querySelectorAll('[data-view]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.view === view)));
  $('#selectView').hidden = view !== 'select';
  $('#healthView').hidden = view !== 'health';
  document.body.classList.toggle('view-health', view === 'health');
  if (view === 'health') { if (health.report) hvShow(); else hvLoad(); }
  try { localStorage.setItem('sr-view', view); } catch { /* view kept for this session only */ }
}
document.querySelectorAll('[data-view]').forEach((b) => { b.onclick = () => setView(b.dataset.view); });

(async () => {
  let view = 'select';
  try { view = localStorage.getItem('sr-view') || 'select'; } catch { /* no storage */ }
  setView(view);
  if (view !== 'health') {
    try { health.report = await api('/api/health'); hvBadge(); } catch { /* the badge is optional */ }
  }
})();
