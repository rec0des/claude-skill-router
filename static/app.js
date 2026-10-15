const $ = (s) => document.querySelector(s);
const state = { result: null, selected: new Set(), focused: null, filter: 'selected', query: '', advice: null };
const nf = new Intl.NumberFormat('en-US');
const fmt = (n) => nf.format(Math.round(n || 0));
const esc = (v = '') => String(v).replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const icon = (id) => `<svg class="i"><use href="#i-${id}"/></svg>`;
const SOURCE = { global: 'local', command: 'command', project: 'project', plugin: 'plugin' };

function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove('show'), 2400);
}

async function api(path, body) {
  const res = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skill-Router': '1' }, body: JSON.stringify(body) } : undefined);
  const data = await res.json().catch(() => ({ error: 'Invalid response from the local server.' }));
  if (!res.ok || data.error) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

try { $('#project').value = localStorage.getItem('sr-project') || ''; } catch { /* no storage: the field just starts empty */ }
const payload = () => ({ project: $('#project').value.trim(), task: $('#task').value.trim(), selected: [...state.selected] });
const skillByName = (name) => state.result?.skills.find((s) => s.invoke === name);
const adviceTag = (name) => {
  const a = state.advice;
  if (!a) return '';
  if (a.picks.some((p) => p.skill === name)) return '<span class="tag claude">Claude</span>';
  if (a.avoid.some((p) => p.skill === name)) return '<span class="tag avoid">avoid</span>';
  return '';
};

function visible(s) {
  const f = state.filter;
  if (f === 'selected' && !state.selected.has(s.invoke)) return false;
  if (f === 'recommended' && s.tier === 'excluded') return false;
  if (f === 'plugin' && s.source !== 'plugin') return false;
  if (f === 'local' && s.source === 'plugin') return false;
  if (!state.query) return true;
  return `${s.invoke} ${s.description}`.toLowerCase().includes(state.query);
}

function renderList() {
  const r = state.result;
  if (!r) return;
  const rows = r.skills.filter(visible);
  const list = $('#skills');
  if (!rows.length) {
    const msg = state.filter === 'selected'
      ? '<p><strong>No active skills.</strong></p><p>This task does not call for specific skills; it is better handled without them. You can still turn one on under Suggested.</p>'
      : '<p><strong>No results.</strong></p><p>Change the filter or the search.</p>';
    list.innerHTML = `<div class="empty">${msg}</div>`;
  } else {
    list.innerHTML = rows.slice(0, 250).map((s) => {
      const on = state.selected.has(s.invoke);
      return `<div class="row ${on ? 'sel' : ''} ${state.focused === s.invoke ? 'focus' : ''}" data-name="${esc(s.invoke)}">
        <input type="checkbox" ${on ? 'checked' : ''} aria-label="Enable ${esc(s.invoke)}">
        <div class="row-main"><div class="row-title"><strong>${esc(s.invoke)}</strong><span class="tag">${SOURCE[s.source] || esc(s.source)}</span>${adviceTag(s.invoke)}</div><p>${esc(s.reasons[0])}</p></div>
        <div class="score"><span>${s.score}</span><div class="bar"><i style="width:${s.score}%"></i></div></div>
        <div class="tokens">${fmt(s.tokens)} tokens</div>
      </div>`;
    }).join('');
  }
  $('#listCount').textContent = `${fmt(rows.length)} of ${fmt(r.skills.length)}`;
  $('#nSelected').textContent = state.selected.size;
}

function renderCost() {
  const chosen = [...state.selected].map(skillByName).filter(Boolean);
  const bodies = chosen.reduce((a, s) => a + s.tokens, 0);
  const manifest = 60 + chosen.reduce((a, s) => a + (s.invoke.length + Math.min(160, s.description.length)) / 4, 0);
  $('#selectionCount').textContent = chosen.length ? `${chosen.length} active ${chosen.length === 1 ? 'skill' : 'skills'}` : 'No active skills';
  $('#costLine').textContent = chosen.length
    ? `Normal session: ~${fmt(manifest)} tokens of manifest · Open Claude (strict mode): ~${fmt(bodies)} tokens of skill bodies`
    : 'No skill cost in this session.';
  ['save', 'copy', 'launch'].forEach((id) => { $('#' + id).disabled = !chosen.length; });
}

function renderDetail() {
  const s = skillByName(state.focused);
  $('#detailEmpty').hidden = !!s;
  $('#detail').hidden = !s;
  if (!s) return;
  $('#detailName').textContent = s.name;
  $('#detailSource').textContent = SOURCE[s.source] || s.source;
  $('#detailDesc').textContent = s.description;
  $('#detailScore').textContent = `${s.score}/99`;
  $('#detailTokens').textContent = `${fmt(s.tokens)} tokens`;
  $('#detailInvoke').textContent = s.source === 'plugin' ? s.invoke : `/${s.invoke}`;
  const advice = state.advice && [...state.advice.picks, ...state.advice.avoid].find((p) => p.skill === s.invoke);
  const reasons = advice ? [`Claude: ${advice.why}`, ...s.reasons] : s.reasons;
  $('#detailReasons').innerHTML = reasons.map((x) => `<li>${esc(x)}</li>`).join('');
}

function renderSignals() {
  const r = state.result;
  $('#signals').hidden = !r;
  if (!r) return;
  const laya = r.evidence.find((x) => x.startsWith('Laya:'));
  $('#sigTask').textContent = r.categories.join(', ') || 'none';
  $('#sigStack').textContent = r.evidence.filter((x) => !x.startsWith('Laya:') && !x.startsWith('Read ')).join(', ') || (r.project_categories || []).join(', ') || 'no manifests';
  // The classifier is only consulted when keywords alone cannot decide the task area.
  $('#sigLaya').textContent = laya ? laya.replace('Laya: ', '') : state.laya ? 'not needed' : 'stopped';
}

function render() { renderList(); renderCost(); renderDetail(); }

function setFilter(filter) {
  state.filter = filter;
  document.querySelectorAll('[data-filter]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.filter === filter)));
  renderList();
}

function toggle(name) {
  state.selected.has(name) ? state.selected.delete(name) : state.selected.add(name);
  state.focused = name;
  render();
}

$('#skills').addEventListener('click', (e) => {
  const row = e.target.closest('.row');
  if (!row) return;
  if (e.target.matches('input[type=checkbox]')) { toggle(row.dataset.name); return; }
  state.focused = row.dataset.name;
  renderList(); renderDetail();
});

document.querySelectorAll('[data-filter]').forEach((b) => { b.onclick = () => setFilter(b.dataset.filter); });
$('#search').addEventListener('input', (e) => {
  state.query = e.target.value.trim().toLowerCase();
  if (state.query && state.filter === 'selected') setFilter('all'); else renderList();
});

function busy(btn, on, label) {
  btn.disabled = on;
  btn.classList.toggle('busy', on);
  if (label) btn.querySelector('span').textContent = label;
}

async function analyze() {
  const btn = $('#analyze');
  if (!$('#project').value.trim()) { toast('Enter the project folder.'); $('#project').focus(); return; }
  try { localStorage.setItem('sr-project', $('#project').value.trim()); } catch { /* no storage: it just won't be remembered */ }
  busy(btn, true, 'Analyzing…');
  $('#skills').innerHTML = '<div class="skeleton"></div>'.repeat(6);
  try {
    const data = await api('/api/analyze', payload());
    state.result = data;
    state.selected = new Set(data.selected);
    state.focused = data.selected[0] || null;
    state.advice = null;
    $('#advisorBody').innerHTML = '';
    $('#consult').disabled = false;
    $('#search').disabled = false;
    $('#crumb').hidden = false;
    $('#crumbName').textContent = data.project;
    renderSignals();
    setFilter(data.selected.length ? 'selected' : 'recommended');
    render();
  } catch (err) {
    $('#skills').innerHTML = `<div class="empty"><p class="error">${esc(err.message)}</p></div>`;
  } finally {
    busy(btn, false, 'Analyze');
  }
}

$('#taskForm').addEventListener('submit', (e) => { e.preventDefault(); analyze(); });
$('#task').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); analyze(); } });

function renderAdvice() {
  const a = state.advice;
  const body = $('#advisorBody');
  if (!a) { body.innerHTML = ''; return; }
  const routerSet = new Set(a.router);
  const item = (p, kind) => `<li>${icon(kind === 'pick' ? 'check' : 'minus')}<div><b>${esc(p.skill)}${kind === 'pick' && !routerSet.has(p.skill) ? '<em>new</em>' : ''}</b><span>${esc(p.why)}</span></div></li>`;
  const cost = a.cost_usd != null ? ` · US$ ${a.cost_usd.toFixed(4)}` : '';
  const same = a.picks.length && a.picks.every((p) => state.selected.has(p.skill)) && state.selected.size === a.picks.length;
  body.innerHTML = `<div class="verdict">
    <p class="summary">${esc(a.summary)}</p>
    <div class="verdict-actions">${a.picks.length ? `<button class="btn primary" id="applyAdvice" ${same ? 'disabled' : ''}>${icon('check')}<span>${same ? 'Selection applied' : 'Apply picks'}</span></button>` : ''}<span class="meta">${esc(a.model)} · ${a.seconds} s · ${fmt(a.tokens)} tokens${cost}</span></div>
    ${a.picks.length ? `<p class="picks-label">Use</p><ul class="picks">${a.picks.map((p) => item(p, 'pick')).join('')}</ul>` : '<p class="muted">Claude does not recommend any skill for this task.</p>'}
    ${a.avoid.length ? `<p class="picks-label">Avoid</p><ul class="picks avoid">${a.avoid.map((p) => item(p, 'avoid')).join('')}</ul>` : ''}
  </div>`;
  const apply = $('#applyAdvice');
  if (apply) apply.onclick = applyAdvice;
}

async function consult() {
  const btn = $('#consult');
  busy(btn, true, 'Asking…');
  $('#advisorBody').innerHTML = '<div class="thinking" aria-label="Waiting for the answer"><i></i><i></i><i></i></div>';
  try {
    state.advice = await api('/api/consult', payload());
    renderAdvice(); render();
  } catch (err) {
    $('#advisorBody').innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    busy(btn, false, state.advice ? 'Ask again' : 'Ask Claude');
  }
}

async function applyAdvice() {
  const a = state.advice;
  state.selected = new Set(a.picks.map((p) => p.skill));
  state.focused = a.picks[0]?.skill || null;
  setFilter('selected');
  render(); renderAdvice();
  try {
    await api('/api/feedback', { project: state.result.project, up: a.picks.map((p) => p.skill), down: a.avoid.map((p) => p.skill) });
    toast('Selection applied. The router will weigh these picks for this project.');
  } catch { toast('Selection applied, but the feedback could not be recorded.'); }
}

$('#consult').onclick = consult;

$('#save').onclick = async () => {
  try { await api('/api/profile', { ...payload(), result: state.result }); toast('Profile saved.'); } catch (e) { toast(e.message); }
};
$('#copy').onclick = async () => {
  try {
    const d = await api('/api/command', payload());
    // PowerShell single quotes expand neither $() nor variables; a ' is escaped by doubling it.
    const literal = (v) => `'${String(v).replace(/'/g, "''")}'`;
    await navigator.clipboard.writeText(`$env:SKILL_ROUTER_MODE='launcher'; Set-Location -LiteralPath ${literal(payload().project)}; ${d.command}`);
    toast('Command copied.');
  } catch (e) { toast(e.message); }
};
$('#launch').onclick = async () => {
  const btn = $('#launch');
  busy(btn, true);
  try { await api('/api/launch', payload()); toast('Claude opened in a new terminal.'); } catch (e) { toast(e.message); } finally { busy(btn, false); }
};

(async () => {
  try {
    const d = await api('/api/status');
    const src = d.sources;
    const local = (src.global || 0) + (src.command || 0) + (src.project || 0);
    $('#statusLine').textContent = `${fmt(d.skills)} skills · ${fmt(local)} local · ${fmt(src.plugin || 0)} from plugins · classifier ${d.laya ? 'on' : 'stopped'}`;
    $('#layaDot').className = `dot ${d.laya ? 'on' : 'off'}`;
    state.laya = Boolean(d.laya);
    showCliUpdate(d.claude_cli);
    if (!d.claude_cli || Date.now() / 1000 - d.claude_cli.at > 86400) api('/api/claude-cli', {}).then(showCliUpdate).catch(() => {});
    if (state.result) renderSignals();
  } catch {
    $('#statusLine').textContent = 'Local server unavailable';
    $('#layaDot').className = 'dot off';
  }
})();

function showCliUpdate(info) {
  const btn = $('#cliUpdate');
  btn.hidden = !info?.outdated;
  if (!info?.outdated) return;
  $('#cliUpdateLabel').textContent = `Update Claude Code to ${info.latest}`;
  btn.title = `Installed ${info.installed} · the ${info.channel} channel has ${info.latest}. Runs the official claude update.`;
}

$('#cliUpdate').onclick = async () => {
  const btn = $('#cliUpdate');
  busy(btn, true, 'Updating Claude Code…');
  try {
    const r = await api('/api/claude-cli', { op: 'update' });
    showCliUpdate(r.status);
    toast(r.ok && !r.status?.outdated ? `Claude Code ${r.status?.installed || ''} installed. Restart open sessions to use it.` : r.message);
  } catch (e) { $('#cliUpdateLabel').textContent = 'Update Claude Code'; toast(e.message); } finally {
    btn.disabled = false;
    btn.classList.remove('busy');
  }
};
