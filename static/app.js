'use strict';
/* Face Blur - phone UI. Plain JS, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const app = $('#app');
const CHUNK = 8 * 1024 * 1024;
const STATE_LABEL = {
  uploading: 'מעלה', queued_analyze: 'ממתין לניתוח', analyzing: 'מנתח', ready: 'בחר פרצופים',
  queued_render: 'בתור לעיבוד', rendering: 'מעבד', done: 'מוכן', error: 'שגיאה', cancelled: 'בוטל',
};
const ACTIVE = new Set(['queued_analyze', 'analyzing', 'queued_render', 'rendering']);
const BLUR_TYPES = [['gaussian', 'רגיל'], ['oval', 'אובלי'], ['pixelate', 'מוזאיקה']];

const uploads = {};     // jobId -> live upload in this tab
const selection = {};   // jobId -> {faces:Set, blurType, strength}
const editMode = {};    // jobId -> true while re-choosing faces of a finished job
const eta = {};         // jobId -> {state, t0, p0}
const prepared = {};    // jobId -> File ready for the share sheet
let pollTimer = null, viewToken = 0, afterLogin = '#/new', lastJob = null;

/* ------------------------------------------------------------ helpers */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ltr = s => `\u2066${s}\u2069`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function fmtSize(b) {
  if (!b && b !== 0) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return ltr(`${b.toFixed(b < 10 && i > 0 ? 1 : 0)} ${u[i]}`);
}
function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return ltr(h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`);
}
function fmtEta(sec) {
  if (!isFinite(sec) || sec <= 0) return '';
  if (sec < 60) return 'פחות מדקה';
  if (sec < 3600) return `כ-${Math.round(sec / 60)} דק'`;
  return `כ-${(sec / 3600).toFixed(1)} שעות`;
}
const rtf = new Intl.RelativeTimeFormat('he', { numeric: 'auto' });
function ago(ts) {
  const d = (ts * 1000 - Date.now()) / 1000;
  const a = Math.abs(d);
  if (a < 60) return 'עכשיו';
  if (a < 3600) return rtf.format(Math.round(d / 60), 'minute');
  if (a < 86400) return rtf.format(Math.round(d / 3600), 'hour');
  return rtf.format(Math.round(d / 86400), 'day');
}
let toastTimer;
function toast(msg, ms = 2600) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}
function bar(p, indet) {
  return `<div class="bar${indet ? ' indet' : ''}"><i style="width:${Math.round((p || 0) * 100)}%"></i></div>`;
}
function stateBadge(s) { return `<span class="badge-state ${esc(s)}">${esc(STATE_LABEL[s] || s)}</span>`; }

/* ---------------------------------------------------------------- api */
async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: { 'X-FaceBlur': '1' }, credentials: 'same-origin' };
  if (opts.json !== undefined) { o.body = JSON.stringify(opts.json); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (_) { /* not json */ }
  if (r.status === 401 && path !== '/api/login') { goLogin(); const e = new Error('נדרשת התחברות'); e.status = 401; throw e; }
  if (!r.ok) { const e = new Error((data && data.error) || `שגיאה ${r.status}`); e.status = r.status; e.data = data; throw e; }
  return data;
}
function goLogin() {
  if (location.hash === '#/login') return;
  afterLogin = location.hash || '#/new';
  viewToken++;  // drop whatever view was loading
  clearTimeout(pollTimer);
  location.hash = '#/login';
}

/* ------------------------------------------------------------- router */
function setTab(name) {
  const tab = { job: 'jobs', library: 'new' }[name] || name || 'new';
  document.querySelectorAll('.tabbar a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
}
function route(keepScroll) {
  clearTimeout(pollTimer);
  const token = ++viewToken;
  const [name, arg] = (location.hash || '#/new').slice(2).split('/');
  document.body.classList.toggle('login', name === 'login');
  setTab(name);
  if (keepScroll !== true) window.scrollTo(0, 0);
  switch (name) {
    case 'login': return viewLogin();
    case 'jobs': return viewJobs(token);
    case 'job': return viewJob(arg, token, true);
    case 'library': return viewLibrary(token);
    case 'status': return viewStatus(token);
    default: return viewNew(token);
  }
}
function poll(token, fn, ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => { if (token === viewToken) fn(); }, ms);
}
window.addEventListener('hashchange', route);
document.addEventListener('visibilitychange', () => { if (!document.hidden && location.hash !== '#/login') { refreshStatus(); route(true); } });

/* -------------------------------------------------------------- login */
function viewLogin() {
  app.innerHTML = `
  <div class="card" style="margin-top:6vh">
    <h1>🔒 כניסה</h1>
    <p class="sub">הסיסמה שהוגדרה בהתקנה על ה-iMac</p>
    <form id="loginForm">
      <label class="field"><span>סיסמה</span>
        <input type="password" id="pw" autocomplete="current-password" required></label>
      <div id="loginErr"></div>
      <button class="btn primary block" type="submit">כניסה</button>
    </form>
  </div>`;
  $('#pw').focus();
  $('#loginForm').onsubmit = async e => {
    e.preventDefault();
    const btn = e.target.querySelector('button'); btn.disabled = true;
    try {
      await api('/api/login', { method: 'POST', json: { password: $('#pw').value } });
      location.hash = afterLogin && afterLogin !== '#/login' ? afterLogin : '#/new';
      refreshStatus();
    } catch (err) {
      $('#loginErr').innerHTML = `<div class="notice err">${esc(err.message)}</div>`;
      btn.disabled = false;
    }
  };
}

/* ---------------------------------------------------------------- new */
function viewNew(token) {
  const live = Object.entries(uploads);
  app.innerHTML = `
  <h1>טשטוש פרצופים</h1>
  <p class="sub">הווידאו מעובד על ה-iMac. אחרי שההעלאה מסתיימת אפשר לסגור את הטלפון — העיבוד ממשיך.</p>
  <label class="big-choice" for="pick">
    <span class="em">📱</span>
    <div><b>העלאה מהטלפון</b><span>סרטון מהגלריה או מהקבצים</span></div>
  </label>
  <input type="file" id="pick" accept="video/*" style="position:absolute;opacity:0;width:1px;height:1px;pointer-events:none">
  <a class="big-choice" href="#/library">
    <span class="em">🖥️</span>
    <div><b>קובץ שכבר נמצא ב-iMac</b><span>מתיקיית Inbox — בלי להעלות כלום</span></div>
  </a>
  ${live.length ? `<h2 style="margin-top:22px">העלאות פעילות</h2>${live.map(([id, u]) => `
    <a class="card job" href="#/job/${id}">
      <div class="head"><div class="name">${esc(u.name)}</div>${stateBadge('uploading')}</div>
      ${bar(u.sent / u.total)}<div class="meta">${ltr(Math.floor(u.sent / u.total * 100) + '%')} · ${fmtSize(u.total)}</div>
    </a>`).join('')}` : ''}`;
  $('#pick').onchange = e => { const f = e.target.files[0]; e.target.value = ''; if (f) startUpload(f); };
  if (live.length) poll(token, () => viewNew(token), 1000);
}

/* ------------------------------------------------------------ uploads */
async function startUpload(file, jobId) {
  try {
    if (!jobId) {
      const r = await api('/api/uploads', { method: 'POST', json: { name: file.name, size: file.size } });
      jobId = r.job.id;
    }
    uploads[jobId] = { file, name: file.name, sent: 0, total: file.size, note: '', samples: [] };
    const h = '#/job/' + jobId;
    if (location.hash === h) viewJob(jobId, viewToken, true); else location.hash = h;
    runUpload(jobId);
  } catch (e) { toast(e.message, 4000); }
}

function putChunk(url, blob, onProgress) {
  return new Promise(resolve => {
    const x = new XMLHttpRequest();
    x.open('PUT', url);
    x.setRequestHeader('X-FaceBlur', '1');
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = e => onProgress(e.loaded);
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch (_) { } resolve({ status: x.status, data: d }); };
    x.onerror = x.ontimeout = () => resolve({ status: 0, data: {} });
    x.timeout = 5 * 60 * 1000;
    x.send(blob);
  });
}

async function wakeLock() {
  try { return await navigator.wakeLock.request('screen'); } catch (_) { return null; }
}

async function runUpload(id) {
  const u = uploads[id];
  let lock = await wakeLock();
  const relock = async () => { if (!document.hidden && uploads[id]) lock = await wakeLock(); };
  document.addEventListener('visibilitychange', relock);
  const finish = () => { document.removeEventListener('visibilitychange', relock); try { lock && lock.release(); } catch (_) { } };

  let offset = 0, attempt = 0;
  try { offset = (await api('/api/jobs/' + id)).job.uploaded || 0; } catch (_) { }
  u.sent = offset;
  while (true) {
    if (u.aborted) return finish();
    if (offset >= u.total) {
      const r = await fetch(`/api/uploads/${id}/complete`, { method: 'POST', headers: { 'X-FaceBlur': '1' } }).catch(() => null);
      if (r && r.ok) break;
      if (r && r.status === 409) { const d = await r.json().catch(() => ({})); if (typeof d.size === 'number') { offset = d.size; continue; } }
      if (r && r.status === 401) { u.note = 'נדרשת התחברות מחדש'; updateUploadUI(id); finish(); return goLogin(); }
      if (r && [400, 404].includes(r.status)) { u.failed = 'ההעלאה נכשלה'; updateUploadUI(id); finish(); return; }
      attempt++;
    } else {
      const end = Math.min(offset + CHUNK, u.total);
      const r = await putChunk(`/api/uploads/${id}?offset=${offset}`, u.file.slice(offset, end), loaded => {
        u.sent = offset + loaded; trackSpeed(u); updateUploadUI(id);
      });
      if (u.aborted) return finish();
      if (r.status === 200) { offset = r.data.size; u.sent = offset; attempt = 0; u.note = ''; updateUploadUI(id); continue; }
      if (r.status === 409 && typeof r.data.size === 'number') { offset = r.data.size; u.sent = offset; continue; }
      if (r.status === 401) { u.note = 'נדרשת התחברות מחדש'; updateUploadUI(id); finish(); return goLogin(); }
      if ([400, 404, 409, 413, 507].includes(r.status)) {
        u.failed = r.data.error || `שגיאה ${r.status}`; updateUploadUI(id); finish(); return;
      }
      attempt++;
    }
    const wait = Math.min(30, 2 ** Math.min(attempt, 5));
    u.note = `החיבור נקטע — מנסה שוב בעוד ${wait} שניות`;
    updateUploadUI(id);
    await sleep(wait * 1000);
  }
  finish();
  delete uploads[id];
  toast('ההעלאה הסתיימה ✓');
  if (location.hash === '#/job/' + id) viewJob(id, viewToken, false);
}

function trackSpeed(u) {
  const now = Date.now();
  u.samples.push([now, u.sent]);
  while (u.samples.length > 2 && now - u.samples[0][0] > 8000) u.samples.shift();
}
function uploadStats(u) {
  const s = u.samples;
  if (s.length < 2) return '';
  const [t0, b0] = s[0], [t1, b1] = s[s.length - 1];
  const bps = (b1 - b0) / Math.max(0.001, (t1 - t0) / 1000);
  if (bps <= 0) return '';
  return `${fmtSize(bps)}/s · נותרו ${fmtEta((u.total - u.sent) / bps) || 'שניות'}`;
}
function updateUploadUI(id) {
  if (location.hash !== '#/job/' + id) return;
  const u = uploads[id]; if (!u) return;
  const el = $('#upload'); if (!el) return viewJob(id, viewToken, false);
  const p = u.sent / u.total;
  el.querySelector('.bar>i').style.width = `${Math.round(p * 100)}%`;
  $('#upPct').textContent = ltr(`${Math.floor(p * 100)}%`);
  $('#upMeta').textContent = `${fmtSize(u.sent)} מתוך ${fmtSize(u.total)}${uploadStats(u) ? ' · ' + uploadStats(u) : ''}`;
  $('#upNote').innerHTML = u.failed ? `<div class="notice err">${esc(u.failed)}</div>`
    : u.note ? `<div class="notice info">${esc(u.note)}</div>` : '';
}

/* --------------------------------------------------------------- jobs */
async function viewJobs(token) {
  let list;
  try { list = (await api('/api/jobs')).jobs; } catch (e) {
    if (token !== viewToken) return;
    app.innerHTML = e.status === undefined ? offlineHtml() : `<div class="notice err">${esc(e.message)}</div>`;
    return poll(token, () => viewJobs(token), 5000);
  }
  if (token !== viewToken) return;
  app.innerHTML = `<h1>עבודות</h1><p class="sub">כל מה שנשלח ל-iMac. העיבוד ממשיך גם כשהטלפון סגור.</p>` + (list.length ? list.map(j => {
    const u = uploads[j.id];
    const p = j.state === 'uploading' ? (u ? u.sent / u.total : (j.uploaded || 0) / (j.expected_size || 1)) : j.progress;
    const showBar = j.state === 'uploading' || ACTIVE.has(j.state);
    let meta = ago(j.created_at);
    if (j.state === 'done') meta += ` · ${fmtSize(j.output_size)}`;
    if (j.state === 'ready') meta += ` · ${j.faces.length} פרצופים`;
    if (j.queue_position > 1) meta += ` · מקום ${j.queue_position} בתור`;
    if (j.state === 'uploading' && !u) meta += ' · ההעלאה נקטעה';
    if (j.source === 'library') meta += ' · מה-iMac';
    return `<a class="card job" href="#/job/${j.id}">
      <div class="head"><div class="name">${esc(j.name)}</div>${stateBadge(j.state)}</div>
      ${showBar ? bar(p, j.state.startsWith('queued')) : ''}
      <div class="meta">${esc(meta)}${showBar && !j.state.startsWith('queued') ? ` · ${ltr(Math.floor(p * 100) + '%')}` : ''}</div>
    </a>`;
  }).join('') : `<div class="empty"><span class="em">🎬</span>עדיין אין עבודות.<br><a href="#/new">התחל עבודה חדשה</a></div>`);
  const busy = list.some(j => ACTIVE.has(j.state) || j.state === 'uploading');
  poll(token, () => viewJobs(token), busy ? 2500 : 10000);
}

function offlineHtml() {
  return `<div class="empty"><span class="em">📡</span><b>אין חיבור ל-iMac</b><br>
    <span class="small">בדוק שהטלפון מחובר לאינטרנט ושאפליקציית Tailscale פועלת (VPN מחובר).<br>אם זה נמשך — ייתכן שה-iMac כבוי או ישן.</span></div>`;
}

/* ---------------------------------------------------------------- job */
async function viewJob(id, token, first) {
  let job;
  try { job = (await api('/api/jobs/' + id)).job; } catch (e) {
    if (token !== viewToken) return;
    if (e.status === 404) { app.innerHTML = `<div class="empty"><span class="em">🤷</span>העבודה לא נמצאה<br><a href="#/jobs">לכל העבודות</a></div>`; return; }
    if (e.status === undefined) app.innerHTML = offlineHtml();
    return poll(token, () => viewJob(id, token, false), 4000);
  }
  if (token !== viewToken) return;

  const prev = lastJob && lastJob.id === id ? lastJob : null;
  lastJob = job;
  const sameView = prev && !first && prev.state === job.state && !!uploads[id] === !!prev._live && !!editMode[id] === !!prev._edit;
  job._live = !!uploads[id]; job._edit = !!editMode[id];

  if (sameView && ACTIVE.has(job.state)) { updateProgress(job); }
  else if (!sameView) { renderJob(job); if (prev) refreshStatus(); }

  if (ACTIVE.has(job.state)) poll(token, () => viewJob(id, token, false), 1500);
  else if (job.state === 'uploading' && !uploads[id]) poll(token, () => viewJob(id, token, false), 5000);
}

function jobHeader(job) {
  const i = job.info || {};
  const bits = [];
  if (i.duration) bits.push(fmtDur(i.duration));
  if (i.width) bits.push(ltr(`${i.width}×${i.height}`));
  if (job.expected_size) bits.push(fmtSize(job.expected_size));
  if (job.source === 'library') bits.push('מה-iMac');
  return `<a href="#/jobs" class="small" style="text-decoration:none">→ כל העבודות</a>
    <div class="job" style="margin:8px 0 14px">
      <div class="head"><h1 class="name" style="font-size:20px">${esc(job.name)}</h1>${stateBadge(job.state)}</div>
      <div class="meta">${esc(bits.join(' · '))}</div>
    </div>`;
}

function renderJob(job) {
  const id = job.id;
  let body = '';
  const s = job.state;
  if (s === 'uploading') body = uploads[id] ? uploadingHtml(job) : stalledUploadHtml(job);
  else if (ACTIVE.has(s)) body = progressHtml(job);
  else if (s === 'ready' || (editMode[id] && job.faces.length)) body = facesHtml(job);
  else if (s === 'done') body = doneHtml(job);
  else body = errorHtml(job);
  app.innerHTML = jobHeader(job) + body;
  wireJob(job);
  if (uploads[id]) updateUploadUI(id);
}

function uploadingHtml(job) {
  return `<div class="card" id="upload">
    <h2>מעלה ל-iMac… <span id="upPct">0%</span></h2>
    ${bar(0)}
    <div class="meta small muted" id="upMeta"></div>
    <div id="upNote" style="margin-top:10px"></div>
    <p class="small muted" style="margin-top:8px">השאר את הדף פתוח עד סוף ההעלאה. אם החיבור נקטע — ההעלאה ממשיכה מאותה נקודה.</p>
  </div>
  <button class="btn danger block" data-act="abort">ביטול העלאה</button>`;
}

function stalledUploadHtml(job) {
  const got = job.uploaded || 0, total = job.expected_size || 1;
  return `<div class="card">
    <h2>ההעלאה נקטעה</h2>
    ${bar(got / total)}
    <p class="small muted">${fmtSize(got)} מתוך ${fmtSize(total)} הגיעו ל-iMac.</p>
    <p style="margin-top:10px">כדי להמשיך מאותה נקודה — בחר שוב את <b>אותו קובץ</b>.</p>
  </div>
  <label class="btn primary block" for="resumePick">המשך העלאה</label>
  <input type="file" id="resumePick" accept="video/*" style="position:absolute;opacity:0;width:1px;height:1px;pointer-events:none">
  <div style="height:10px"></div>
  <button class="btn danger block" data-act="delete">מחק</button>`;
}

function progressHtml(job) {
  const queued = job.state.startsWith('queued');
  const title = job.state === 'analyzing' || job.state === 'queued_analyze' ? 'מחפש פרצופים בווידאו' : 'מטשטש את הפרצופים שנבחרו';
  return `<div class="card" id="prog">
    <h2>${title}</h2>
    ${bar(job.progress, queued)}
    <div class="meta small muted" id="progText">${progressText(job)}</div>
  </div>
  <div class="notice info">אפשר לסגור את הטלפון — העבודה ממשיכה ב-iMac. חזור לכאן מאוחר יותר.</div>
  <button class="btn ghost block" data-act="cancel">עצור</button>`;
}

function progressText(job) {
  if (job.state.startsWith('queued')) return job.queue_position > 1 ? `ממתין — מקום ${job.queue_position} בתור` : 'ממתין להתחלה…';
  let t = ltr(`${Math.floor(job.progress * 100)}%`);
  if (job.message) t += ` · ${job.message}`;
  const e = eta[job.id];
  if (!e || e.state !== job.state || job.progress < e.p0) eta[job.id] = { state: job.state, t0: Date.now(), p0: job.progress };
  else if (job.progress - e.p0 > 0.02) {
    const rate = (job.progress - e.p0) / ((Date.now() - e.t0) / 1000);
    const rem = fmtEta((1 - job.progress) / rate);
    if (rem) t += ` · נותרו ${rem}`;
  }
  return esc(t);
}

function updateProgress(job) {
  const el = $('#prog'); if (!el) return renderJob(job);
  el.querySelector('.bar>i').style.width = `${Math.round(job.progress * 100)}%`;
  $('#progText').innerHTML = progressText(job);
}

function facesHtml(job) {
  const id = job.id;
  if (!job.faces.length) {
    return `<div class="card"><h2>לא נמצאו פרצופים 🤔</h2>
      <p class="muted">הזיהוי לא מצא פרצופים בווידאו. זה קורה כשהפרצופים קטנים מאוד, מטושטשים או מצולמים מהצד.</p></div>
      <button class="btn danger block" data-act="delete">מחק</button>`;
  }
  const sel = selection[id] || (selection[id] = {
    faces: new Set((job.settings && job.settings.faces) || []),
    blurType: (job.settings && job.settings.blur_type) || 'gaussian',
    strength: (job.settings && job.settings.blur_strength) || 151,
  });
  return `<h2>בחר את הפרצופים לטשטוש</h2>
  <p class="small muted">אם אותו אדם מופיע ביותר מכרטיס אחד — בחר את כולם.</p>
  <div class="row" style="margin:10px 0 0">
    <button class="btn ghost" data-act="all">בחר הכל</button>
    <button class="btn ghost" data-act="none">נקה</button>
  </div>
  <div class="faces">${job.faces.map(f => `
    <button type="button" class="face${sel.faces.has(f.id) ? ' on' : ''}" data-face="${f.id}" aria-pressed="${sel.faces.has(f.id)}">
      ${f.thumb ? `<img loading="lazy" src="/api/jobs/${id}/faces/${f.id}.jpg" alt="פרצוף ${f.id}">` : '<img alt="">'}
      <span class="check">${sel.faces.has(f.id) ? '✓' : ''}</span>
      <span class="cap">${f.appearances} הופעות</span>
    </button>`).join('')}</div>
  <div class="card">
    <label class="field"><span>סוג טשטוש</span>
      <div class="seg" id="seg">${BLUR_TYPES.map(([k, v]) => `<button type="button" data-type="${k}" class="${sel.blurType === k ? 'on' : ''}">${v}</button>`).join('')}</div>
    </label>
    <label class="field" style="margin-bottom:0"><span>עוצמה: <b id="strengthVal">${sel.strength}</b></span>
      <input type="range" id="strength" min="51" max="251" step="20" value="${sel.strength}">
      <div class="row small muted" style="justify-content:space-between"><span>חלש</span><span>חזק</span></div>
    </label>
  </div>
  <div class="sticky-action">
    <button class="btn primary block" data-act="render" id="renderBtn"></button>
    ${editMode[id] ? '<div style="height:8px"></div><button class="btn ghost block" data-act="exitEdit">ביטול</button>' : ''}
  </div>`;
}

function doneHtml(job) {
  const canShare = window.isSecureContext && navigator.share && navigator.canShare;
  return `<div class="card">
    <video controls playsinline preload="metadata" src="/api/jobs/${job.id}/video"></video>
    <p class="small muted" style="margin-top:8px">${esc(job.output_name)} · ${fmtSize(job.output_size)} · נשמר גם ב-iMac בתיקייה Outputs</p>
  </div>
  <div class="stack">
    ${canShare ? `<button class="btn primary block" data-act="share" id="shareBtn">${prepared[job.id] ? '📲 שמור לתמונות / שתף' : '📲 שמור לטלפון'}</button>` : ''}
    <a class="btn ${canShare ? 'ghost' : 'primary'} block" href="/api/jobs/${job.id}/download" download>⬇️ הורד קובץ</a>
    <button class="btn ghost block" data-act="edit">✏️ שנה בחירת פרצופים / טשטוש</button>
    <button class="btn danger block" data-act="delete">מחק מהרשימה</button>
  </div>`;
}

function errorHtml(job) {
  const msg = job.state === 'cancelled' ? 'העבודה בוטלה.' : (job.error || 'משהו השתבש');
  return `<div class="notice err">${esc(msg)}</div>
  <div class="stack">
    <button class="btn primary block" data-act="retry">נסה שוב</button>
    <button class="btn danger block" data-act="delete">מחק</button>
  </div>`;
}

function updateRenderBtn(job) {
  const b = $('#renderBtn'); if (!b) return;
  const n = selection[job.id].faces.size;
  b.disabled = n === 0;
  b.textContent = n ? `🎬 טשטש ${n === 1 ? 'פרצוף אחד' : n + ' פרצופים'}` : 'בחר פרצוף אחד לפחות';
}

function wireJob(job) {
  const id = job.id;
  const sel = selection[id];
  if (sel) {
    updateRenderBtn(job);
    app.querySelectorAll('.face').forEach(el => el.onclick = () => {
      const f = +el.dataset.face;
      sel.faces.has(f) ? sel.faces.delete(f) : sel.faces.add(f);
      const on = sel.faces.has(f);
      el.classList.toggle('on', on); el.setAttribute('aria-pressed', on);
      el.querySelector('.check').textContent = on ? '✓' : '';
      updateRenderBtn(job);
    });
    app.querySelectorAll('#seg button').forEach(b => b.onclick = () => {
      sel.blurType = b.dataset.type;
      app.querySelectorAll('#seg button').forEach(x => x.classList.toggle('on', x === b));
    });
    const r = $('#strength');
    if (r) r.oninput = () => { sel.strength = +r.value; $('#strengthVal').textContent = r.value; };
  }
  const rp = $('#resumePick');
  if (rp) rp.onchange = e => {
    const f = e.target.files[0]; e.target.value = '';
    if (!f) return;
    if (f.size !== job.expected_size) return toast('זה לא אותו קובץ (הגודל שונה)', 4000);
    startUpload(f, id);
  };

  app.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
    const act = b.dataset.act;
    try {
      if (act === 'all' || act === 'none') {
        sel.faces = new Set(act === 'all' ? job.faces.map(f => f.id) : []);
        return renderJob(job);
      }
      if (act === 'render') {
        b.disabled = true;
        await api(`/api/jobs/${id}/render`, { method: 'POST', json: { faces: [...sel.faces], blur_type: sel.blurType, blur_strength: sel.strength } });
        delete editMode[id]; delete prepared[id];
        return viewJob(id, viewToken, true);
      }
      if (act === 'cancel') { await api(`/api/jobs/${id}/cancel`, { method: 'POST' }); return viewJob(id, viewToken, true); }
      if (act === 'retry') { await api(`/api/jobs/${id}/retry`, { method: 'POST' }); return viewJob(id, viewToken, true); }
      if (act === 'edit') { editMode[id] = true; return viewJob(id, viewToken, true); }
      if (act === 'exitEdit') { delete editMode[id]; return viewJob(id, viewToken, true); }
      if (act === 'abort' || act === 'delete') {
        const q = act === 'abort' ? 'לבטל את ההעלאה?' : (job.state === 'done' ? 'למחוק מהרשימה? הסרטון המעובד יישאר ב-iMac בתיקייה Outputs.' : 'למחוק את העבודה?');
        if (!confirm(q)) return;
        if (uploads[id]) { uploads[id].aborted = true; delete uploads[id]; }
        await api(`/api/jobs/${id}`, { method: 'DELETE' });
        location.hash = '#/jobs';
        return;
      }
      if (act === 'share') return share(job, b);
    } catch (e) { toast(e.message, 4000); b.disabled = false; }
  });
}

async function share(job, btn) {
  let file = prepared[job.id];
  if (!file) {
    btn.disabled = true;
    try {
      const blob = await new Promise((resolve, reject) => {
        const x = new XMLHttpRequest();
        x.open('GET', `/api/jobs/${job.id}/download`);
        x.responseType = 'blob';
        x.onprogress = e => { btn.textContent = `מכין… ${e.total ? Math.floor(e.loaded / e.total * 100) + '%' : fmtSize(e.loaded)}`; };
        x.onload = () => x.status === 200 ? resolve(x.response) : reject(new Error('ההורדה נכשלה'));
        x.onerror = () => reject(new Error('ההורדה נכשלה'));
        x.send();
      });
      file = prepared[job.id] = new File([blob], job.output_name || 'video.mp4', { type: 'video/mp4' });
    } catch (e) { toast(e.message, 4000); btn.disabled = false; btn.textContent = '📲 שמור לטלפון'; return; }
    btn.disabled = false;
    btn.textContent = '📲 שמור לתמונות / שתף';
  }
  if (!navigator.canShare({ files: [file] })) { toast('השיתוף לא נתמך — השתמש ב"הורד קובץ"', 4000); return; }
  try { await navigator.share({ files: [file] }); }
  catch (e) {
    // iOS needs a fresh tap after a long download - the button is ready now.
    if (e.name === 'NotAllowedError') toast('הקובץ מוכן — לחץ שוב לשמירה');
  }
}

/* ------------------------------------------------------------ library */
async function viewLibrary(token) {
  let data;
  try { data = await api('/api/library'); } catch (e) {
    if (token === viewToken) app.innerHTML = e.status === undefined ? offlineHtml() : `<div class="notice err">${esc(e.message)}</div>`;
    return;
  }
  if (token !== viewToken) return;
  const files = data.files;
  app.innerHTML = `<a href="#/new" class="small" style="text-decoration:none">→ חזרה</a>
    <h1 style="margin-top:8px">קבצים ב-iMac</h1>
    <p class="sub">סרטונים מתיקיית <code>${esc(data.roots[0])}</code>. העיבוד נעשה ישירות מה-iMac, בלי להעלות מהטלפון.</p>
    ${files.length > 8 ? '<input type="text" id="filter" placeholder="חיפוש…" style="margin-bottom:12px">' : ''}
    <div id="libList">${files.length ? files.map(f => `
      <div class="card lib-item" data-name="${esc(f.name.toLowerCase())}">
        <div style="min-width:0"><div class="nm">${esc(f.name)}</div>
          <div class="meta small muted">${esc(f.folder)} · ${fmtSize(f.size)} · ${ago(f.mtime)}</div></div>
        <button class="btn primary" data-ref="${esc(f.ref)}">טשטש</button>
      </div>`).join('') : `<div class="empty"><span class="em">📂</span>אין סרטונים בתיקייה.<br>
        <span class="small">על ה-iMac: גרור סרטונים לתיקייה <code>FaceBlur/Inbox</code> שבתיקיית הבית.</span></div>`}</div>`;
  const filter = $('#filter');
  if (filter) filter.oninput = () => {
    const q = filter.value.trim().toLowerCase();
    app.querySelectorAll('.lib-item').forEach(el => { el.hidden = q && !el.dataset.name.includes(q); });
  };
  app.querySelectorAll('[data-ref]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api('/api/library/use', { method: 'POST', json: { ref: b.dataset.ref } });
      location.hash = '#/job/' + r.job.id;
    } catch (e) { toast(e.message, 4000); b.disabled = false; }
  });
}

/* ------------------------------------------------------------- status */
async function viewStatus(token) {
  let s;
  try { s = await api('/api/status'); } catch (e) {
    if (token === viewToken) app.innerHTML = e.status === undefined ? offlineHtml() : `<div class="notice err">${esc(e.message)}</div>`;
    return poll(token, () => viewStatus(token), 5000);
  }
  if (token !== viewToken) return;
  const used = 1 - s.disk_free / s.disk_total;
  const lowDisk = s.disk_free < 20 * 1024 ** 3;
  app.innerHTML = `<h1>🖥️ ${esc(s.host)}</h1>
    <p class="sub">ה-iMac מחובר ועובד.</p>
    ${!s.ffmpeg ? '<div class="notice err">ffmpeg לא נמצא — הסרטונים ייצאו בלי אודיו. הרץ שוב את ההתקנה.</div>' : ''}
    ${lowDisk ? '<div class="notice err">נשאר מעט מקום בדיסק.</div>' : ''}
    <div class="card">
      <h2>דיסק</h2>
      ${bar(used)}
      <div class="small muted">${fmtSize(s.disk_free)} פנויים מתוך ${fmtSize(s.disk_total)}</div>
    </div>
    <div class="card"><dl class="kv">
      <dt>בניתוח</dt><dd>${s.analyzing}</dd>
      <dt>בעיבוד / בתור</dt><dd>${s.rendering}</dd>
      <dt>סה"כ עבודות</dt><dd>${s.jobs}</dd>
      <dt>עומס מעבד</dt><dd>${s.load != null ? `${s.load.toFixed(1)} / ${s.cpus} ליבות` : '—'}</dd>
      <dt>פועל ברציפות</dt><dd>${fmtEta(s.uptime).replace('כ-', '') || 'פחות מדקה'}</dd>
      <dt>גרסה</dt><dd>${esc(s.version)}</dd>
      <dt>Inbox</dt><dd><code>${esc(s.inbox)}</code></dd>
      <dt>Outputs</dt><dd><code>${esc(s.outputs)}</code></dd>
    </dl></div>
    <div class="card small">
      <b>טיפ:</b> ב-Safari לחץ על כפתור השיתוף ⬆️ ואז "הוסף למסך הבית" — והאתר ייפתח כמו אפליקציה.
    </div>
    <button class="btn ghost block" id="logout">התנתק</button>`;
  $('#logout').onclick = async () => { await api('/api/logout', { method: 'POST' }).catch(() => { }); goLogin(); };
  poll(token, () => viewStatus(token), 15000);
}

/* --------------------------------------------------------- status pill */
let statusTimer;
async function refreshStatus() {
  clearTimeout(statusTimer);
  const pill = $('#statusPill'), txt = $('#statusText');
  if (location.hash !== '#/login') {
    try {
      const s = await api('/api/status');
      pill.className = 'pill ok'; txt.textContent = s.host;
      const n = s.analyzing + s.rendering, b = $('#activeBadge');
      b.hidden = !n; b.textContent = n;
    } catch (e) {
      if (e.status === undefined) { pill.className = 'pill bad'; txt.textContent = 'אין חיבור ל-iMac'; }
    }
  }
  statusTimer = setTimeout(refreshStatus, 20000);
}

route();
refreshStatus();
