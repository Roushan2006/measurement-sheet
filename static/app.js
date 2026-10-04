(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const DRAFT_KEY = 'ms_draft_v2';
  const THEME_KEY = 'ms_theme';
  const state = {meas: [], less: []};
  let nextKey = 1, currentId = null, dirty = false, timer = null, saving = false;

  const FIELDS = [
    {f: 'item_no',   label: 'Item No',   ph: '1',         cls: 'c-no w-no'},
    {f: 'item_name', label: 'Item name', ph: 'Item name', cls: 'c-name'},
    {f: 'length',    label: 'Length',    ph: '0.0',       cls: 'c-l w-dim', dim: true},
    {f: 'width',     label: 'Width',     ph: '0.0',       cls: 'c-w w-dim', dim: true},
    {f: 'height',    label: 'Height',    ph: 'optional',  cls: 'c-h w-dim', dim: true},
    {f: 'qty',       label: 'Qty',       ph: '1',         cls: 'c-q w-qty', num: true},
    {f: 'remark',    label: 'Remark',    ph: 'optional',  cls: 'c-r w-rem'},
  ];

  const DETAIL_KEYS = ['contractor', 'po_no', 'sheet_item_no', 'civil_int', 'rab_no', 'jms_no', 'date'];
  const today = () => new Date().toLocaleDateString('en-CA');

  /* ---------- small helpers ---------- */
  const fmt = n => n.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const hdr = {'Content-Type': 'application/json'};

  function toast(msg, opts = {}) {
    const t = $('toast'), act = $('toastAct');
    $('toastMsg').textContent = msg;
    t.className = 'show' + (opts.bad ? ' bad' : '');
    act.hidden = !opts.action;
    if (opts.action) {
      act.textContent = opts.action.label;
      act.onclick = () => { opts.action.fn(); t.className = ''; };
    }
    clearTimeout(t._h);
    t._h = setTimeout(() => (t.className = ''), opts.action ? 6000 : 2600);
  }

  function errText(d) {
    if (d && typeof d.detail === 'string') return d.detail;
    return 'Please check the values you entered.';
  }

  function setStatus() {
    const s = $('status');
    if (dirty) { s.textContent = '● Unsaved changes'; s.className = 'chip dirty'; }
    else if (currentId) { s.textContent = '✓ Saved'; s.className = 'chip ok'; }
    else { s.textContent = 'New sheet'; s.className = 'chip'; }
  }

  const payload = () => ({
    title: $('title').value,
    ...Object.fromEntries(DETAIL_KEYS.map(k => [k, $('d_' + k).value])),
    measurements: state.meas.map(({k, ...r}) => r),
    less: state.less.map(({k, ...r}) => r),
  });

  /* ---------- draft autosave (local only) ---------- */
  function saveDraft() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({data: payload(), currentId, dirty})); } catch (e) {}
  }
  function clearDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch (e) {} }

  function changed() {
    dirty = true; setStatus(); saveDraft();
    clearTimeout(timer); timer = setTimeout(calc, 250);
  }

  /* ---------- rows ---------- */
  function newRow(kind) {
    const n = state[kind].length + 1;
    return {k: nextKey++, item_no: kind === 'less' ? 'L' + n : String(n), item_name: '', length: '', width: '', height: '', qty: 1, remark: ''};
  }

  function focusNext(body, input) {
    const all = [...body.querySelectorAll('input')];
    const i = all.indexOf(input);
    if (i < all.length - 1) { all[i + 1].focus(); return true; }
    return false;
  }

  function buildRow(kind, row) {
    const tr = document.createElement('tr');
    const body = $(kind);

    FIELDS.forEach(d => {
      const td = document.createElement('td');
      td.className = d.cls; td.dataset.label = d.label;
      const inp = document.createElement('input');
      inp.type = d.num ? 'number' : 'text';
      inp.placeholder = d.ph; inp.value = row[d.f]; inp.dataset.f = d.f;
      inp.setAttribute('aria-label', d.label);
      if (d.num) { inp.min = 1; inp.step = 1; inp.inputMode = 'numeric'; }
      if (d.dim) inp.inputMode = 'decimal';
      inp.addEventListener('focus', () => inp.select());
      inp.addEventListener('input', () => {
        row[d.f] = d.num ? (parseInt(inp.value, 10) || 1) : inp.value;
        changed();
      });
      inp.addEventListener('keydown', e => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        if (!focusNext(body, inp)) addRow(kind);
      });
      td.appendChild(inp); tr.appendChild(td);
    });

    const res = document.createElement('td');
    res.className = 'res w-res'; res.dataset.label = 'Result'; res.textContent = '–';
    tr.appendChild(res);

    const act = document.createElement('td');
    act.className = 'c-act w-act';
    const box = document.createElement('div'); box.className = 'act';
    const dup = document.createElement('button');
    dup.textContent = '⧉'; dup.title = 'Duplicate row'; dup.setAttribute('aria-label', 'Duplicate row');
    dup.onclick = () => duplicateRow(kind, row);
    const rm = document.createElement('button');
    rm.className = 'rm'; rm.textContent = '🗑'; rm.title = 'Delete row'; rm.setAttribute('aria-label', 'Delete row');
    rm.onclick = () => deleteRow(kind, row);
    box.append(dup, rm); act.appendChild(box); tr.appendChild(act);
    return tr;
  }

  function renderRows(kind) {
    const body = $(kind);
    body.innerHTML = '';
    state[kind].forEach(row => body.appendChild(buildRow(kind, row)));
    $(kind === 'meas' ? 'countMeas' : 'countLess').textContent = state[kind].length ? `(${state[kind].length})` : '';
  }

  function addRow(kind) {
    state[kind].push(newRow(kind));
    renderRows(kind);
    const rows = $(kind).children;
    rows[rows.length - 1].querySelector('[data-f=item_name]').focus();
    changed();
  }

  function duplicateRow(kind, row) {
    const copy = {...row, k: nextKey++, item_no: String(state[kind].length + 1)};
    state[kind].splice(state[kind].indexOf(row) + 1, 0, copy);
    renderRows(kind); changed();
  }

  function deleteRow(kind, row) {
    const index = state[kind].indexOf(row);
    state[kind].splice(index, 1);
    if (kind === 'meas' && !state.meas.length) state.meas.push(newRow('meas'));
    renderRows(kind); changed();
    toast('Row deleted', {action: {label: 'Undo', fn: () => {
      if (kind === 'meas' && state.meas.length === 1 && isBlank(state.meas[0])) state.meas = [];
      state[kind].splice(Math.min(index, state[kind].length), 0, row);
      renderRows(kind); changed();
    }}});
  }

  const isBlank = r => !r.item_name && !r.length && !r.width && !r.height && !r.remark;

  /* ---------- calculate (server does the maths) ---------- */
  async function calc() {
    try {
      const r = await fetch('/api/calculate', {method: 'POST', headers: hdr, body: JSON.stringify(payload())});
      if (r.ok) paint(await r.json());
    } catch (e) { /* offline: keep last values */ }
  }

  function paint(ev) {
    [['meas', ev.measurements], ['less', ev.less]].forEach(([kind, list]) => {
      const rows = $(kind).children;
      list.forEach((res, i) => {
        const tr = rows[i]; if (!tr) return;
        const cell = tr.querySelector('.res');
        cell.className = 'res w-res'; cell.removeAttribute('title');
        tr.querySelectorAll('input[data-f=length],input[data-f=width],input[data-f=height]')
          .forEach(x => x.classList.toggle('bad', !!res.error));
        if (res.error) { cell.classList.add('err'); cell.textContent = '⚠ ' + res.error; }
        else if (res.ok) {
          cell.textContent = (kind === 'less' ? '− ' : '') + fmt(res.result) + ' ';
          const u = document.createElement('small'); u.textContent = res.unit; cell.appendChild(u);
          cell.title = [res.l_label, res.w_label, res.h_label].filter(Boolean).join(' × ') + (res.qty > 1 ? ' × ' + res.qty : '');
        } else cell.textContent = '–';
      });
    });

    const box = $('totals'); box.innerHTML = '';
    const units = Object.entries(ev.totals);
    const barNet = $('barNet');
    if (!units.length) {
      box.innerHTML = '<span class="empty">Enter a length and width to see totals.</span>';
      barNet.querySelector('b').textContent = '–';
      return;
    }
    units.forEach(([unit, t]) => {
      const wrap = document.createElement('div');
      wrap.innerHTML = `<div class="unit"></div><div class="tiles">
        <div class="tile t-g"><span>Gross total</span><b></b></div>
        <div class="tile t-l"><span>Less</span><b></b></div>
        <div class="tile t-n"><span>Net total</span><b></b></div></div>`;
      wrap.querySelector('.unit').textContent = unit === 'sq ft' ? 'Area (sq ft)' : 'Volume (cu ft)';
      const b = wrap.querySelectorAll('b');
      b[0].textContent = fmt(t.gross); b[1].textContent = '− ' + fmt(t.less); b[2].textContent = fmt(t.net);
      box.appendChild(wrap);
    });
    barNet.querySelector('b').textContent = units.map(([u, t]) => `${fmt(t.net)} ${u}`).join('  ·  ');
  }

  /* ---------- save / open / history ---------- */
  async function save(asNew = false) {
    if (saving) return;
    saving = true; $('save').disabled = true;
    try {
      const body = JSON.stringify(payload());
      const post = () => fetch('/api/sheets', {method: 'POST', headers: hdr, body});
      let r;
      if (currentId && !asNew) {
        r = await fetch('/api/sheets/' + currentId, {method: 'PUT', headers: hdr, body});
        if (r.status === 404) r = await post();  // old sheet was already removed from the last-10 list
      } else r = await post();
      const d = await r.json().catch(() => null);
      if (!r.ok) return toast(errText(d), {bad: true});
      currentId = d.id; dirty = false; setStatus(); saveDraft();
      toast(asNew ? 'Saved as a new copy ✓' : 'Saved ✓');
      loadHistory();
    } catch (e) {
      toast('Could not reach the server', {bad: true});
    } finally { saving = false; $('save').disabled = false; }
  }

  async function download(url, opts, ext) {
    try {
      const r = await fetch(url, opts);
      if (!r.ok) return toast(errText(await r.json().catch(() => null)), {bad: true});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await r.blob());
      a.download = ($('title').value.trim() || 'measurement_sheet') + '.' + ext;
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { toast('Could not reach the server', {bad: true}); }
  }

  function fill(data) {
    $('title').value = data.title || '';
    DETAIL_KEYS.forEach(k => { $('d_' + k).value = data[k] || ''; });
    state.meas = (data.measurements || []).map(r => ({k: nextKey++, ...r}));
    state.less = (data.less || []).map(r => ({k: nextKey++, ...r}));
    if (!state.meas.length) state.meas.push(newRow('meas'));
    renderRows('meas'); renderRows('less'); calc();
  }

  async function openSheet(id) {
    if (dirty && !confirm('You have unsaved changes. Open another sheet anyway?')) return;
    const r = await fetch('/api/sheets/' + id);
    if (!r.ok) { toast('Could not open that sheet', {bad: true}); return loadHistory(); }
    fill(await r.json());
    currentId = id; dirty = false; setStatus(); saveDraft(); loadHistory();
    window.scrollTo({top: 0, behavior: 'smooth'});
  }

  async function loadHistory() {
    const ul = $('hist');
    let list;
    try {
      const r = await fetch('/api/sheets');
      if (!r.ok) return;
      list = await r.json();
    } catch (e) { return; }
    ul.innerHTML = '';
    if (!list.length) {
      const li = document.createElement('li'); li.className = 'none'; li.textContent = 'No saved sheets yet.';
      ul.appendChild(li); return;
    }
    list.forEach(s => {
      const li = document.createElement('li');
      if (s.id === currentId) li.className = 'active';
      const info = document.createElement('div'); info.className = 'info';
      const name = document.createElement('div'); name.className = 'name'; name.textContent = s.title;
      const meta = document.createElement('div'); meta.className = 'meta';
      meta.textContent = new Date(s.updated_at).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) + ' · ' + s.items + ' items';
      const net = document.createElement('div'); net.className = 'net';
      net.textContent = Object.entries(s.totals).map(([u, t]) => `${fmt(t.net)} ${u}`).join(' · ');
      info.append(name, meta, net);

      const pdf = document.createElement('button'); pdf.textContent = 'PDF'; pdf.title = 'Download PDF';
      pdf.onclick = e => { e.stopPropagation(); window.location = `/api/sheets/${s.id}/pdf`; };
      const rm = document.createElement('button'); rm.className = 'rm'; rm.textContent = '🗑'; rm.title = 'Delete';
      rm.onclick = async e => {
        e.stopPropagation();
        if (!confirm('Delete this saved sheet?')) return;
        await fetch('/api/sheets/' + s.id, {method: 'DELETE'});
        if (currentId === s.id) { currentId = null; setStatus(); }
        loadHistory();
      };
      li.append(info, pdf, rm);
      li.onclick = () => openSheet(s.id);
      ul.appendChild(li);
    });
  }

  function newSheet() {
    if (dirty && !confirm('Discard unsaved changes and start a new sheet?')) return;
    currentId = null; dirty = false;
    state.meas = [newRow('meas')]; state.less = [];
    $('title').value = '';
    DETAIL_KEYS.forEach(k => { $('d_' + k).value = ''; });
    $('d_date').value = today();
    renderRows('meas'); renderRows('less'); calc(); setStatus(); clearDraft(); loadHistory();
  }

  /* ---------- theme ---------- */
  function applyTheme(t) {
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
  }
  function toggleTheme() {
    const dark = document.documentElement.dataset.theme === 'dark' ||
      (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
    const next = dark ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
  }

  /* ---------- wire up ---------- */
  $('addMeas').onclick = () => addRow('meas');
  $('addLess').onclick = () => addRow('less');
  $('save').onclick = () => save(false);
  $('saveCopy').onclick = () => save(true);
  $('pdf').onclick = () => download('/api/pdf', {method: 'POST', headers: hdr, body: JSON.stringify(payload())}, 'pdf');
  $('csv').onclick = () => download('/api/csv', {method: 'POST', headers: hdr, body: JSON.stringify(payload())}, 'csv');
  $('newSheet').onclick = newSheet;
  $('theme').onclick = toggleTheme;
  $('title').addEventListener('input', changed);
  DETAIL_KEYS.forEach(k => $('d_' + k).addEventListener('input', changed));
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(false); }
  });

  /* ---------- start ---------- */
  try { applyTheme(localStorage.getItem(THEME_KEY)); } catch (e) {}

  let restored = false;
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    if (draft && draft.data && (draft.dirty || draft.currentId)) {
      fill(draft.data); currentId = draft.currentId; dirty = !!draft.dirty; restored = true;
    }
  } catch (e) {}
  if (!restored) { state.meas.push(newRow('meas')); renderRows('meas'); renderRows('less'); calc(); }
  if (!$('d_date').value) $('d_date').value = today();
  setStatus(); loadHistory();
  if (restored && dirty) toast('Restored your unsaved draft');
})();