/* New skills (catalog + GitHub), per-project plugin profile and metrics. Uses $, esc, fmt, icon, api, toast, busy, payload. */
const disc = { result: null, staged: null, armed: null, profile: null };

function trustBadge(t) {
  return `<span class="trust ${t < 40 ? 'low' : ''}" title="Trust ${t}/100"><i style="--t:${t}%"></i>${t}</span>`;
}

function discArm(key) {
  if (disc.armed === key) { disc.armed = null; return true; }
  disc.armed = key;
  setTimeout(() => { if (disc.armed === key) { disc.armed = null; discRender(); } }, 4000);
  discRender();
  return false;
}

function discRender() {
  const box = $('#discoverBody');
  const r = disc.result;
  if (!r) { box.innerHTML = ''; return; }
  if (!r.candidates.length) {
    box.innerHTML = '<p class="muted">Nothing relevant in the catalog or on GitHub for this task.</p>';
    return;
  }
  const intro = r.local_top_score >= 45
    ? `<p class="note">You already have local skills with a ${r.local_top_score}/99 match; these are only for comparison.</p>`
    : `<p class="note">No installed skill covers this task well (best match ${r.local_top_score}/99).</p>`;
  box.innerHTML = intro + `<ul class="cands">${r.candidates.map((c, i) => {
    const gh = c.github || {};
    const stars = gh.stars != null ? `<span class="tag">${fmt(gh.stars)} ★</span>` : '';
    const origin = c.reviewed ? '<span class="tag claude">reviewed</span>' : `<span class="tag">${esc(c.origin)}</span>`;
    const installKey = `install:${i}`;
    let action = '';
    if (c.install) {
      action = `<button class="btn ${disc.armed === installKey ? 'confirm' : ''}" data-dinstall="${i}">${icon('up')}<span>${disc.armed === installKey ? 'Confirm' : 'Install in project'}</span></button>`;
    } else if (c.repo && /github\.com/.test(c.repo)) {
      action = `<button class="btn" data-dstage="${i}">${icon('shield')}<span>Audit repository</span></button>`;
    }
    const staged = disc.staged && disc.staged.index === i ? stagedHtml(disc.staged.data) : '';
    return `<li>
      <div class="top"><b>${esc(c.name)}</b>${trustBadge(c.trust)}</div>
      <p>${esc(c.summary.slice(0, 180))}</p>
      <div class="meta">${origin}${stars}${c.trust_notes.length ? `<span class="muted" style="font-size:12px">${esc(c.trust_notes.join(' · '))}</span>` : ''}</div>
      <div class="meta">${action}${/^https:\/\//.test(c.repo || '') ? `<a href="${esc(c.repo)}" target="_blank" rel="noopener noreferrer">View repository</a>` : ''}</div>
      ${staged}
    </li>`;
  }).join('')}</ul>`;
}

const VERDICT = { ok: 'ok', rever: 'review', bloqueada: 'blocked', review: 'review', blocked: 'blocked' };

function stagedHtml(s) {
  if (!s.skills.length) return '<div class="staged"><p class="muted">This repository has no SKILL.md files.</p></div>';
  return `<div class="staged"><p class="note">${esc(s.repo)}: ${s.skills.length} skills audited.</p><ul class="plain">${s.skills.map((k) => {
    const key = `staged:${k.path}`;
    const verdict = VERDICT[k.verdict] || k.verdict;
    const disabled = verdict === 'blocked' || k.exists;
    const risky = verdict === 'review';
    const label = k.exists ? 'Already installed' : verdict === 'blocked' ? 'Blocked'
      : disc.armed === key ? 'Confirm' : risky ? 'Accept risk and install' : 'Install';
    // Evidence is shown before the button: high risk can only be accepted after reading it.
    const evidence = k.findings.length ? `<ul class="issues">${k.findings.map((f) => `<li>
      <span class="sev ${f.severity}">${SEV[f.severity] || esc(f.severity)} · ${esc(f.why)}</span>
      <span class="loc">${esc(f.file)}:${f.line}</span><pre>${esc(f.evidence)}</pre></li>`).join('')}</ul>` : '';
    return `<li style="flex-direction:column;align-items:stretch"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center">
      <div><b class="verdict-${verdict}">${esc(k.name)}</b><span>${esc(verdict)} · ~${fmt(k.tokens)} tokens${k.findings.length ? ` · ${k.findings.length} findings` : ''}</span></div>
      <button class="btn ${disc.armed === key ? 'confirm' : risky ? 'danger' : ''}" data-dskill="${esc(k.path)}" ${disabled ? 'disabled' : ''}>${label}</button></div>${evidence}</li>`;
  }).join('')}</ul></div>`;
}

$('#discover').disabled = false;
$('#discover').onclick = async () => {
  const p = payload();
  if (!p.task) { toast('Describe the task first.'); $('#task').focus(); return; }
  const btn = $('#discover');
  busy(btn, true, 'Searching…');
  $('#discoverBody').innerHTML = '<div class="thinking"><i></i><i></i><i></i></div>';
  try {
    disc.result = await api('/api/discover', { project: p.project, task: p.task });
    disc.staged = null;
    discRender();
  } catch (e) {
    $('#discoverBody').innerHTML = `<p class="error">${esc(e.message)}</p>`;
  } finally { busy(btn, false, 'Search again'); }
};

$('#discoverBody').addEventListener('click', async (e) => {
  const inst = e.target.closest('[data-dinstall]');
  const stage = e.target.closest('[data-dstage]');
  const skill = e.target.closest('[data-dskill]');
  if (inst) {
    const i = Number(inst.dataset.dinstall);
    if (!discArm(`install:${i}`)) return;
    const c = disc.result.candidates[i];
    busy(inst, true);
    try {
      await api('/api/install', { kind: 'plugin', name: c.name, marketplace: c.marketplace, scope: 'project', project: payload().project });
      toast(`${c.name} installed in this project. Restart Claude to load it.`);
    } catch (err) { toast(err.message); } finally { discRender(); }
  } else if (stage) {
    const i = Number(stage.dataset.dstage);
    busy(stage, true, 'Downloading and auditing…');
    try {
      disc.staged = { index: i, data: await api('/api/stage', { url: disc.result.candidates[i].repo }) };
    } catch (err) { toast(err.message); }
    discRender();
  } else if (skill) {
    const path = skill.dataset.dskill;
    const k = disc.staged.data.skills.find((x) => x.path === path);
    if (!discArm(`staged:${path}`)) return;
    busy(skill, true);
    try {
      const res = await api('/api/install', { kind: 'staged', stage: disc.staged.data.id, path, accept_risk: (VERDICT[k.verdict] || k.verdict) === 'review' });
      toast(res.message);
      k.exists = true;
    } catch (err) { toast(err.message); }
    discRender();
  }
});

/* Per-project plugin profile */
function ppRender() {
  const p = disc.profile;
  if (!p) return;
  $('#ppSaved').textContent = p.tokens_saved ? `~${fmt(p.tokens_saved)} tokens per session` : 'optimized';
  const pending = p.disable.filter((r) => !r.disabled_here).length;
  const applied = p.disable.filter((r) => r.disabled_here).length;
  const applyKey = 'pp:apply';
  $('#ppActions').innerHTML = `
    <button class="btn ${disc.armed === applyKey ? 'confirm' : ''}" data-pp="apply" ${pending ? '' : 'disabled'}>${icon('ban')}<span>${disc.armed === applyKey ? 'Confirm' : `Turn off ${pending} here`}</span></button>
    <button class="btn" data-pp="undo" ${applied ? '' : 'disabled'}>${icon('refresh')}<span>Restore (${applied})</span></button>`;
  $('#ppList').innerHTML = p.disable.slice(0, 40).map((r) => `<li><div><b>${esc(r.plugin)}</b><span>${r.skills} skills · ~${fmt(r.listing_tokens)} tokens · ${esc(r.why)}</span></div>${r.disabled_here ? '<span class="status ok">off</span>' : ''}</li>`).join('')
    || '<li><span>No plugins to turn off in this project.</span></li>';
}

async function ppLoad(op = 'plan') {
  try {
    disc.profile = await api('/api/plugin-profile', { project: $('#project').value.trim(), op });
    if (disc.profile.plan) disc.profile = disc.profile.plan;
    ppRender();
  } catch (e) { toast(e.message); }
}

$('#ppActions').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-pp]');
  if (!b) { if (e.target.closest('#ppLoad')) ppLoad(); return; }
  if (b.dataset.pp === 'apply' && disc.armed !== 'pp:apply') { disc.armed = 'pp:apply'; ppRender(); setTimeout(() => { if (disc.armed === 'pp:apply') { disc.armed = null; ppRender(); } }, 4000); return; }
  disc.armed = null;
  busy(b, true);
  await ppLoad(b.dataset.pp);
  toast(b.dataset.pp === 'apply' ? 'Plugins turned off in this project. Restart Claude to apply.' : 'Plugins restored in this project.');
});

/* Metrics */
async function mxLoad() {
  try {
    const m = await api('/api/metrics', {});
    const row = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    const pct = (v) => (v == null ? 'no data' : `${v}%`);
    $('#mxFacts').innerHTML = [
      row('Sessions (30 days)', fmt(m.sessions)),
      row('Selected skills that were used*', pct(m.precision)),
      row('Used skills the router predicted', pct(m.recall)),
      row('Tokens injected per turn', m.avg_injected_tokens == null ? 'no data' : `~${fmt(m.avg_injected_tokens)}`),
      row('Hook latency (median / p95)', m.latency_ms.median == null ? 'no data' : `${m.latency_ms.median} / ${m.latency_ms.p95} ms`),
      m.top_misses.length ? row('Used without being predicted', esc(m.top_misses.map(([n, c]) => `${n} (${c})`).join(', '))) : '',
    ].join('') + '<div><dt>* normal sessions only; in strict mode skills do not go through the Skill tool</dt><dd></dd></div>';
  } catch { $('#mxFacts').innerHTML = '<div><dt>Metrics unavailable</dt><dd></dd></div>'; }
}

document.querySelectorAll('[data-view="health"]').forEach((b) => b.addEventListener('click', mxLoad));
if (!$('#healthView').hidden) mxLoad();
