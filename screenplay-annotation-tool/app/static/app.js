const $ = (id) => document.getElementById(id);
const state = {user:null, actors:[], films:[], film:null, segments:[], selected:null, tasks:{}, taskMeta:{}, queue:null, review:null, llmReady:false, focusTask:'all', visibleIds:[]};
const descKinds = ['Visual action', 'Scene setting', 'Sound cue', 'Speaker identification'];
const descGloss = {'Visual action':'کنش تصویری', 'Scene setting':'موقعیت صحنه', 'Sound cue':'نشانهٔ صوتی', 'Speaker identification':'تشخیص گوینده'};
const kindFa = {dialogue:'دیالوگ', character_event:'رویداد شخصیت', description:'توضیح'};
const surveyFa = {listed:'فهرست شده', missing:'پیدا نشد', unverified:'بررسی‌نشده'};

async function api(path, options={}) {
  const response = await fetch(path, {credentials:'same-origin', ...options});
  if (!response.ok) {
    let detail = 'Request failed';
    try { detail = (await response.json()).error || detail; } catch (_) {}
    throw Error(detail);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}
function json(method, path, value) { return api(path, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(value)}); }
function notify(message, error=false) {
  const t = $('toast'); t.textContent = message; t.className = error ? 'error' : ''; t.style.display = 'block';
  clearTimeout(notify.timer); notify.timer = setTimeout(() => t.style.display = 'none', 5000);
}
function fmt(ms) {
  ms = Math.max(0, Number(ms) || 0);
  const h = Math.floor(ms / 3600000), m = Math.floor(ms % 3600000 / 60000), s = Math.floor(ms % 60000 / 1000), z = ms % 1000;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(z).padStart(3,'0')}`;
}
function tag(name, text, cls='') { const el = document.createElement(name); el.textContent = text; if (cls) el.className = cls; return el; }
function clear(el) { el.replaceChildren(); }
function roleReviewer() { return state.user?.role === 'reviewer'; }
function selected() { return state.segments.find(s => s.id === state.selected); }
function textField(id, label, value, rows=2) {
  const wrap = tag('label', label); const area = document.createElement('textarea');
  area.id = id; area.rows = rows; area.value = value || ''; wrap.append(area); return wrap;
}
function tasksFor(kind) {
  return Object.keys(state.taskMeta).filter(key => state.taskMeta[key].applies_to.includes(kind));
}
function taskDone(seg, key) {
  const item = seg.my_annotation?.[key];
  return !!(item && item.label && String(item.rationale || '').trim().length >= 8);
}

async function bootstrap() {
  const b = await api('/api/bootstrap');
  state.tasks = b.tasks; state.taskMeta = b.task_meta; state.actors = b.actors; state.llmReady = b.llm_ready;
  const saved = localStorage.getItem('film-annotator-actor');
  const name = state.actors.some(a => a.username === saved) ? saved : (b.user?.username || 'reviewer');
  if (!b.user || b.user.username !== name) await actAs(name);
  else { state.user = b.user; await showWorkspace(); }
}
async function actAs(username) {
  const result = await json('POST', '/api/act-as', {username});
  state.user = result.user;
  localStorage.setItem('film-annotator-actor', username);
  await showWorkspace();
}
async function showWorkspace() {
  const select = $('actor-select'); clear(select);
  for (const actor of state.actors) {
    const option = document.createElement('option');
    option.value = actor.username; option.textContent = actor.label; select.append(option);
  }
  select.value = state.user.username;
  $('role-hint').textContent = roleReviewer()
    ? 'متن، ترجمه و داوری با بازبین است.'
    : 'برچسب‌های شما برای هویت دیگر دیده نمی‌شود.';
  $('reviewer-tools').classList.toggle('hidden', !roleReviewer());
  $('editor').classList.toggle('hidden', !roleReviewer());
  $('read-only-record').classList.toggle('hidden', roleReviewer());
  $('annotation-area').classList.toggle('hidden', roleReviewer());
  $('review-area').classList.toggle('hidden', !roleReviewer());
  $('add-description').classList.toggle('hidden', !roleReviewer());
  $('translate').disabled = !state.llmReady;
  $('translate').title = state.llmReady ? 'پیش‌نویس برای بازبینی انسان' : 'OPENAI_API_KEY و ANNOTATOR_LLM_MODEL را تنظیم کنید';
  await loadFilms();
}
async function loadFilms() {
  state.films = await api('/api/films');
  const select = $('film-select'), prior = state.film?.id; clear(select);
  for (const film of state.films) {
    const option = document.createElement('option'); option.value = film.id; option.textContent = film.title; select.append(option);
  }
  const chosen = state.films.find(f => f.id === prior) || state.films[0];
  if (chosen) { select.value = chosen.id; await chooseFilm(chosen.id); }
}
function renderSurvey(film) {
  const host = $('survey'); clear(host);
  const survey = film?.subtitle_survey;
  if (!survey) return;
  const chips = tag('div', '', 'survey-chips');
  for (const [key, title] of [['fa','فارسی'], ['en','انگلیسی'], ['sdh','ناشنوایان']]) {
    const value = survey[key] || 'unverified';
    chips.append(tag('span', `${title}: ${surveyFa[value] || value}`, `chip ${value}`));
  }
  host.append(chips, tag('p', survey.note_fa || ''));
}
async function chooseFilm(id) {
  state.film = state.films.find(f => f.id === Number(id)); state.selected = null; state.queue = null;
  $('review-banner').classList.add('hidden');
  $('film-title').textContent = state.film?.title || 'فیلم';
  $('release-note').textContent = state.film?.release_note || 'شناسهٔ نسخه هنوز ثبت نشده.';
  renderSurvey(state.film);
  $('sdh-summary').textContent = `وضعیت SDH در پروژه: ${state.film?.sdh_status || 'unknown'}${state.film?.sdh_note ? ' · ' + state.film.sdh_note : ''}`;
  const trackHost = $('track-list'); clear(trackHost);
  const tracks = await api(`/api/tracks?film_id=${id}`);
  if (tracks.length) {
    trackHost.append(tag('strong', 'ترک‌های واردشده'));
    for (const t of tracks) trackHost.append(tag('div', `${t.kind.toUpperCase()} · ${t.filename} · ${t.cue_count} cue`));
  }
  $('video').pause(); $('video').removeAttribute('src'); $('video').load();
  if (state.film?.media_name) { $('video').src = `/api/films/${id}/media`; $('media-note').textContent = 'ویدئوی محلی وصل است'; }
  else $('media-note').textContent = 'برای بافت تصویری و صوتی، ویدئو را وصل کنید';
  $('detail').classList.add('hidden'); $('empty-detail').classList.remove('hidden');
  await loadSegments();
}
async function loadSegments(keepSelection=false) {
  if (!state.film) return;
  const prior = state.selected;
  state.segments = await api(`/api/segments?film_id=${state.film.id}`);
  if (!keepSelection) state.selected = null;
  else state.selected = prior;
  renderList();
  if (state.selected && selected()) await showDetail(state.selected, false);
  if (roleReviewer()) loadStats();
  else renderProgress();
}
function renderProgress() {
  const host = $('stats'); clear(host);
  const dialogues = state.segments.filter(s => s.kind === 'dialogue');
  const done = dialogues.filter(s => s.my_complete).length;
  const descriptions = state.segments.filter(s => s.kind === 'description').length;
  host.append(tag('div', `${done} از ${dialogues.length} دیالوگ کامل`));
  host.append(tag('div', `${descriptions} توضیح صحنه یا صدا`));
}
function passesGap(seg) {
  const gap = $('gap-filter').value;
  if (gap === 'mine-missing') {
    if (seg.kind === 'description') return !seg.my_complete;
    return tasksFor(seg.kind).filter(key => state.taskMeta[key].required_on.includes(seg.kind)).some(key => !taskDone(seg, key));
  }
  if (gap === 'focus-missing' && state.focusTask !== 'all') {
    if (!tasksFor(seg.kind).includes(state.focusTask)) return false;
    return !taskDone(seg, state.focusTask);
  }
  return true;
}
function renderList() {
  const host = $('record-list'); clear(host);
  const term = $('search').value.trim().toLocaleLowerCase();
  const kind = $('kind-filter').value;
  let list = state.segments.filter(s => (kind === 'all' || s.kind === kind) && passesGap(s) &&
    (!term || [s.text_fa, s.text_en, s.speaker, s.note].join(' ').toLocaleLowerCase().includes(term)));
  if (state.queue) list = list.filter(s => state.queue.some(q => q.segment_id === s.id && !q.done));
  state.visibleIds = list.map(s => s.id);
  $('record-count').textContent = `${list.length} از ${state.segments.length} رکورد`;
  if (!list.length) { host.append(tag('p', 'رکوردی با این صافی نیست. با هویت بازبین، SRT فارسی را وارد کنید.', 'muted')); return; }
  for (const seg of list) {
    const row = document.createElement('button');
    row.className = 'record-row' + (seg.id === state.selected ? ' active' : '');
    row.type = 'button'; row.onclick = () => showDetail(seg.id, true);
    const top = tag('div', '', 'row-top');
    top.append(tag('span', `${fmt(seg.start_ms)} → ${fmt(seg.end_ms)}`, 'row-time'));
    top.append(tag('span', kindFa[seg.kind] || seg.kind, 'row-kind'));
    row.append(top);
    row.append(tag('div', seg.text_fa || seg.speaker || '[هنوز متن فارسی ندارد]', 'row-fa'));
    if (seg.text_en) row.append(tag('div', seg.text_en, 'row-en'));
    const badges = tag('div', '', 'row-badges');
    if (roleReviewer()) {
      badges.append(tag('span', seg.adjudicated ? 'نهایی' : `${seg.annotation_count}/2 کامل`, 'mini-badge' + (seg.annotation_count < 2 ? ' pending' : '')));
      if (seg.kind === 'dialogue' && seg.translation_status !== 'approved') badges.append(tag('span', seg.translation_status, 'mini-badge pending'));
    } else badges.append(tag('span', seg.my_complete ? 'ثبت شد' : 'ناقص', 'mini-badge' + (seg.my_complete ? '' : ' pending')));
    const dots = tag('span', '', 'task-dots');
    for (const key of tasksFor(seg.kind).filter(key => state.taskMeta[key].required_on.includes(seg.kind))) {
      const dot = tag('span', '', 'dot' + (taskDone(seg, key) ? ' on' : ''));
      dot.title = state.taskMeta[key].title_fa; dots.append(dot);
    }
    badges.append(dots); row.append(badges); host.append(row);
  }
  const active = host.querySelector('.active');
  if (active) active.scrollIntoView({block:'nearest'});
}
function renderTaskSwitch(kind) {
  const host = $('task-switch'); clear(host);
  if (kind === 'description') { host.append(tag('span', 'این رکورد توضیح است، نه دیالوگ.', 'muted')); return; }
  const all = tag('button', 'همهٔ تسک‌ها', 'task-tab' + (state.focusTask === 'all' ? ' active' : ''));
  all.type = 'button'; all.onclick = () => { state.focusTask = 'all'; showDetail(state.selected, false); };
  host.append(all);
  for (const key of tasksFor(kind)) {
    const meta = state.taskMeta[key];
    const button = tag('button', `${meta.number}. ${meta.title_fa}`, 'task-tab' + (state.focusTask === key ? ' active' : ''));
    button.type = 'button';
    button.onclick = () => { state.focusTask = key; showDetail(state.selected, false); };
    host.append(button);
  }
}
async function showDetail(id, seek=true) {
  state.selected = id; const seg = selected(); if (!seg) return;
  $('detail').classList.remove('hidden'); $('empty-detail').classList.add('hidden');
  $('kind-pill').textContent = kindFa[seg.kind] || seg.kind;
  $('detail-title').textContent = `رکورد #${id}`;
  const status = roleReviewer()
    ? (seg.adjudicated ? 'داوری شده' : `${seg.annotation_count}/2 برچسب کامل · نسخهٔ ${seg.version}`)
    : (seg.my_complete ? `برچسب شما کامل است · نسخهٔ ${seg.version}` : `برچسب ناقص · نسخهٔ ${seg.version}`);
  $('detail-status').textContent = status;
  if (seek && $('video').src) $('video').currentTime = seg.start_ms / 1000;
  renderTaskSwitch(seg.kind);
  if (roleReviewer()) {
    $('start-ms').value = seg.start_ms; $('end-ms').value = seg.end_ms; $('speaker').value = seg.speaker;
    $('text-fa').value = seg.text_fa; $('text-en').value = seg.text_en; $('translation-status').value = seg.translation_status;
    $('record-note').value = seg.note;
    await loadReview(seg);
  } else {
    $('readonly-time').textContent = `${fmt(seg.start_ms)} → ${fmt(seg.end_ms)}`;
    $('readonly-speaker').textContent = seg.speaker || 'گوینده نامشخص';
    $('readonly-fa').textContent = seg.text_fa || '[متن فارسی نیست]';
    $('readonly-en').textContent = seg.text_en || '[ترجمه هنوز تأیید نشده]';
    $('readonly-note').textContent = seg.note || '';
    const locked = seg.kind === 'dialogue' && seg.translation_status !== 'approved';
    $('translation-lock').classList.toggle('hidden', !locked);
    $('translation-lock').textContent = locked ? 'بازبین باید ترجمهٔ انگلیسی را تأیید کند تا برچسب دیالوگ ثبت شود.' : '';
    buildForm($('task-fields'), seg.kind, seg.my_annotation || {});
    $('save-annotation').disabled = locked;
    $('save-next').disabled = locked;
  }
  renderList();
}
function choiceField(host, key, labels, value) {
  const meta = state.taskMeta[key];
  const box = tag('div', '', 'task-card' + (state.focusTask === key ? ' focused' : ''));
  box.dataset.key = key;
  const title = tag('div', `${meta.number}. ${meta.title_fa}`, 'task-title');
  title.append(tag('small', `  ${meta.title_en}`));
  box.append(title);
  box.append(tag('p', meta.help_fa, 'task-help'));
  if (meta.required_on && !meta.required_on.includes(selected()?.kind)) box.append(tag('p', 'روی دیالوگ اختیاری است؛ برای رویداد شخصیت لازم است.', 'task-help'));
  const choices = tag('div', '', 'choice-grid');
  labels.forEach(label => {
    const wrap = tag('label', ''); const radio = document.createElement('input');
    radio.type = 'radio'; radio.name = `${host.id}-${key}`; radio.value = label; radio.checked = value?.label === label;
    const copy = tag('span', '', 'choice-copy'); copy.append(tag('b', label), tag('span', meta.gloss?.[label] || '', 'gloss'));
    wrap.append(radio, copy); choices.append(wrap);
  });
  box.append(choices);
  const evidenceRow = tag('div', '', 'evidence-row');
  const evidenceLabel = tag('label', 'عبارت شاهد (اختیاری)');
  const evidence = document.createElement('input'); evidence.id = `${host.id}-${key}-evidence`; evidence.value = value?.evidence || '';
  evidence.placeholder = 'نقل قول یا زمان'; evidenceLabel.append(evidence);
  const useSelection = tag('button', 'متن انتخاب‌شده', 'quiet'); useSelection.type = 'button';
  useSelection.onclick = () => {
    let selectedText = window.getSelection()?.toString().trim();
    if (!selectedText && roleReviewer()) {
      const input = $('text-fa'); selectedText = input.value.slice(input.selectionStart, input.selectionEnd).trim();
    }
    if (selectedText) evidence.value = selectedText;
    else notify('ابتدا عبارتی از متن فارسی را انتخاب کنید.', true);
  };
  evidenceRow.append(evidenceLabel, useSelection); box.append(evidenceRow);
  const rationale = textField(`${host.id}-${key}-rationale`, 'دلیل این برچسب (الزامی)', value?.rationale || '', 3);
  rationale.querySelector('textarea').placeholder = 'به واژه، لحن، یا کنش صحنه استناد کنید.';
  box.append(rationale); host.append(box);
}
function buildForm(host, kind, values) {
  clear(host);
  if (kind === 'description') {
    const box = tag('div', '', 'task-card'); box.dataset.description = '1';
    box.append(tag('div', 'نوع توضیح', 'task-title'));
    box.append(tag('p', 'فقط اطلاعاتی را بنویسید که بدون تصویر یا شنیدن صدا، کنش یا گوینده گم می‌شود.', 'task-help'));
    const select = document.createElement('select'); select.id = `${host.id}-description-kind`;
    select.append(new Option('نوع را انتخاب کنید', ''));
    for (const k of descKinds) select.append(new Option(`${k} · ${descGloss[k]}`, k));
    select.value = values.description_kind || ''; box.append(select);
    const fa = textField(`${host.id}-description-fa`, 'توضیح فارسی', values.description_fa || '', 3);
    fa.querySelector('textarea').dir = 'rtl'; box.append(fa);
    box.append(textField(`${host.id}-description-en`, 'English description', values.description_en || '', 3));
    box.append(textField(`${host.id}-description-rationale`, 'چرا این بافت لازم است؟', values.rationale || '', 2));
    host.append(box); return;
  }
  for (const key of tasksFor(kind)) choiceField(host, key, state.tasks[key], values[key]);
  if (state.focusTask !== 'all') {
    const card = host.querySelector(`[data-key="${state.focusTask}"]`);
    if (card) card.scrollIntoView({block:'nearest'});
  }
}
function collectForm(host, kind) {
  if (kind === 'description') return {
    description_kind: $(`${host.id}-description-kind`).value,
    description_fa: $(`${host.id}-description-fa`).value.trim(),
    description_en: $(`${host.id}-description-en`).value.trim(),
    rationale: $(`${host.id}-description-rationale`).value.trim()
  };
  const payload = {};
  for (const card of host.querySelectorAll('[data-key]')) {
    const key = card.dataset.key; const radio = card.querySelector('input:checked');
    if (radio) payload[key] = {label:radio.value, evidence:$(`${host.id}-${key}-evidence`).value.trim(), rationale:$(`${host.id}-${key}-rationale`).value.trim()};
  }
  return payload;
}
async function loadReview(seg) {
  state.review = await api(`/api/segments/${seg.id}/review`);
  const host = $('independent-annotations'); clear(host);
  for (const ann of state.review.annotations) {
    const box = tag('div', '', 'review-copy');
    box.append(tag('strong', `${ann.username}${ann.segment_version === seg.version ? '' : ' · نسخهٔ قدیمی'}`));
    const lines = [];
    for (const [key, item] of Object.entries(ann.payload)) {
      const title = state.taskMeta[key]?.title_fa || key;
      if (item && typeof item === 'object' && 'label' in item) lines.push(`${title}: ${item.label}${item.evidence ? ' · ' + item.evidence : ''}\n  ${item.rationale || ''}`);
      else lines.push(`${title}: ${item}`);
    }
    box.append(tag('pre', lines.join('\n') || 'برچسبی نیست')); host.append(box);
  }
  if (!state.review.annotations.length) host.append(tag('p', 'هنوز دو حاشیه‌نویس کامل نکرده‌اند.', 'muted'));
  const current = state.review.adjudication || state.review.annotations.find(a => a.segment_version === seg.version)?.payload || {};
  buildForm($('review-fields'), seg.kind, current);
  $('save-adjudication').disabled = seg.annotation_count < 2;
}
async function loadStats() {
  if (!state.film) return;
  const s = await api(`/api/stats?film_id=${state.film.id}`);
  const host = $('stats'); clear(host);
  host.append(tag('div', `${s.segments} رکورد · ${s.double_annotated} دوبار کامل`));
  for (const [key, v] of Object.entries(s.agreement)) host.append(tag('div', `${state.taskMeta[key]?.title_fa || key}: κ ${v.kappa ?? 'n/a'} (n=${v.n})`));
}
async function upload(file, mode, kind) {
  if (!file || !state.film) return;
  notify(`در حال ورود ${file.name}…`);
  const response = await api(`/api/films/${state.film.id}/${mode}`, {method:'PUT', headers:{'X-Filename':encodeURIComponent(file.name), ...(kind ? {'X-Track-Kind':kind} : {})}, body:file});
  notify(mode === 'track' ? `${response.cues} cue وارد شد${response.description_candidates != null ? `؛ ${response.description_candidates} نامزد توضیح` : ''}` : 'ویدئو وصل شد');
  await loadFilms();
}
async function createSegment(kind, start, end) {
  if (!state.film) return;
  const begin = start ?? Math.round(($('video').currentTime || 0) * 1000);
  const finish = end ?? begin + (kind === 'character_event' ? 10000 : 3000);
  const result = await json('POST', '/api/segments', {film_id:state.film.id, kind, start_ms:begin, end_ms:finish});
  await loadSegments(); await showDetail(result.id, false);
}
async function saveRecord() {
  const seg = selected(); if (!seg) return;
  if (seg.annotation_count && !confirm('این رکورد برچسب دارد. ذخیره نسخه را عوض می‌کند و برچسب‌ها باید دوباره ثبت شوند. ادامه می‌دهید؟')) return;
  const payload = {version:seg.version, kind:seg.kind, start_ms:Number($('start-ms').value), end_ms:Number($('end-ms').value),
    speaker:$('speaker').value.trim(), text_fa:$('text-fa').value.trim(), text_en:$('text-en').value.trim(),
    translation_status:$('translation-status').value, note:$('record-note').value.trim()};
  await json('POST', `/api/segments/${seg.id}`, payload); notify('متن و ترجمه ذخیره شد.'); await loadSegments(true);
}
async function saveAnnotation(advance=false) {
  const seg = selected(); if (!seg) return;
  const before = state.visibleIds.slice(); const index = before.indexOf(seg.id);
  await json('POST', `/api/segments/${seg.id}/annotate`, {version:seg.version, payload:collectForm($('task-fields'), seg.kind)});
  notify('برچسب شما ثبت شد.');
  await loadSegments(true);
  if (!advance) return;
  const after = state.visibleIds;
  let next = before[index + 1];
  if (!after.includes(next)) next = after.find(id => before.indexOf(id) > index) || after[0];
  if (next) await showDetail(next, true);
}
async function saveAdjudication() {
  const seg = selected(); if (!seg) return;
  await json('POST', `/api/segments/${seg.id}/adjudicate`, {version:seg.version, payload:collectForm($('review-fields'), seg.kind)});
  notify('داوری ذخیره شد.'); await loadSegments(true);
}
async function loadQueue() {
  state.queue = await api(`/api/review-queue?film_id=${state.film.id}`);
  $('review-banner').classList.remove('hidden'); renderList();
  notify(`${state.queue.filter(q => !q.done).length} رکورد در صف بازبینی است.`);
}
async function downloadExport(raw) {
  const response = await api(`/api/export?film_id=${state.film.id}&raw=${raw ? '1' : '0'}`);
  const blob = await response.blob(); const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = `${state.film.slug}-${raw ? 'all' : 'gold'}.jsonl`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadSrt(lang) {
  const response = await api(`/api/export-srt?film_id=${state.film.id}&lang=${lang}`);
  const blob = await response.blob(); const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = `${state.film.slug}.${lang}.srt`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function stepRecord(delta) {
  const ids = state.visibleIds; const index = ids.indexOf(state.selected);
  const next = ids[index + delta]; if (next) showDetail(next, true);
}
async function safe(fn) { try { await fn(); } catch (e) { notify(e.message || String(e), true); } }

$('actor-select').onchange = () => safe(() => actAs($('actor-select').value));
$('film-select').onchange = () => safe(() => chooseFilm($('film-select').value));
$('search').oninput = renderList; $('kind-filter').onchange = renderList; $('gap-filter').onchange = renderList;
$('prev-record').onclick = () => stepRecord(-1); $('next-record').onclick = () => stepRecord(1);
$('seek-start').onclick = () => { const s = selected(); if (s) $('video').currentTime = s.start_ms / 1000; };
$('seek-prev').onclick = () => $('video').currentTime = Math.max(0, $('video').currentTime - 3);
$('seek-next').onclick = () => $('video').currentTime += 3;
$('video').ontimeupdate = () => $('player-time').textContent = fmt(Math.round($('video').currentTime * 1000));
$('start-from-video').onclick = () => $('start-ms').value = Math.round($('video').currentTime * 1000);
$('end-from-video').onclick = () => $('end-ms').value = Math.round($('video').currentTime * 1000);
$('save-record').onclick = () => safe(saveRecord);
$('split-record').onclick = () => safe(async () => {
  const seg = selected(); if (!seg) return; const cut = Math.round($('video').currentTime * 1000);
  const result = await json('POST', `/api/segments/${seg.id}/split`, {cut_ms:cut});
  notify('رکورد تقسیم شد.'); await loadSegments(); await showDetail(result.new_id, false);
});
$('merge-record').onclick = () => safe(async () => {
  const seg = selected(); if (!seg) return;
  if (!confirm('این رکورد با رکورد هم‌نوع بعدی ادغام شود؟')) return;
  await json('POST', `/api/segments/${seg.id}/merge-next`, {}); notify('ادغام شد.'); await loadSegments(true);
});
$('save-annotation').onclick = () => safe(() => saveAnnotation(false));
$('save-next').onclick = () => safe(() => saveAnnotation(true));
$('save-adjudication').onclick = () => safe(saveAdjudication);
$('translate').onclick = () => safe(async () => {
  const seg = selected(); if (!seg) return;
  if ($('text-fa').value.trim() !== seg.text_fa.trim()) { notify('اول فارسی اصلاح‌شده را ذخیره کنید.', true); return; }
  if (!confirm('متن فارسی این نوبت و نوبت‌های نزدیک به API ترجمه فرستاده شود؟')) return;
  $('translate').disabled = true;
  try { await json('POST', `/api/segments/${seg.id}/translate`, {}); notify('پیش‌نویس ذخیره شد. آن را با فیلم تطبیق دهید.'); await loadSegments(true); }
  finally { $('translate').disabled = !state.llmReady; }
});
$('new-dialogue').onclick = () => safe(() => createSegment('dialogue'));
$('new-character').onclick = () => safe(() => createSegment('character_event'));
$('new-description').onclick = () => safe(() => createSegment('description'));
$('add-description').onclick = () => safe(() => {
  const start = Math.max(0, Math.round(($('video').currentTime || 0) * 1000) - 500);
  return createSegment('description', start, start + 4000);
});
$('add-film').onclick = () => safe(async () => {
  const title = prompt('نام فیلم:'); if (!title) return;
  const release_note = prompt('شناسهٔ نسخه / نرخ فریم (اختیاری):') || '';
  const result = await json('POST', '/api/films', {title, release_note});
  await loadFilms(); $('film-select').value = result.id; await chooseFilm(result.id);
});
$('review-mode').onclick = () => safe(loadQueue);
$('show-all').onclick = () => { state.queue = null; $('review-banner').classList.add('hidden'); renderList(); };
$('export-gold').onclick = () => safe(() => downloadExport(false));
$('export-all').onclick = () => safe(() => downloadExport(true));
$('export-fa-srt').onclick = () => safe(() => downloadSrt('fa'));
$('export-en-srt').onclick = () => safe(() => downloadSrt('en'));
$('edit-release').onclick = () => safe(async () => {
  const note = prompt('شناسهٔ نسخه، نرخ فریم و منبع:', state.film.release_note || ''); if (note === null) return;
  await json('POST', `/api/films/${state.film.id}/release`, {release_note:note}); await loadFilms();
});
$('review-sdh').onclick = () => safe(async () => {
  const choices = ['unknown', 'candidate', 'verified', 'rejected', 'unavailable'];
  const status = prompt(`وضعیت SDH فارسی (${choices.join(', ')}):`, state.film.sdh_status || 'unknown'); if (status === null) return;
  const note = prompt('مدرک: منبع زیرنویس، نسخهٔ فیلم، پوشش گفتار و صداها:', state.film.sdh_note || ''); if (note === null) return;
  await json('POST', `/api/films/${state.film.id}/sdh`, {status:status.trim(), note:note.trim()});
  notify('وضعیت SDH ذخیره شد.'); await loadFilms();
});
$('add-user').onclick = () => safe(async () => {
  const username = prompt('نام کاربری حاشیه‌نویس:'); if (!username) return;
  const password = prompt('گذرواژهٔ موقت (حداقل ۱۰ نویسه):'); if (!password) return;
  await json('POST', '/api/users', {username, password, role:'annotator'}); notify(`حاشیه‌نویس ${username} ساخته شد.`);
  const b = await api('/api/bootstrap'); state.actors = b.actors; await showWorkspace();
});
for (const [id, mode, kind] of [['media-file','media',null], ['fa-file','track','fa'], ['en-file','track','en'], ['sdh-file','track','sdh']]) {
  $(id).onchange = () => safe(async () => { const file = $(id).files[0]; if (file) await upload(file, mode, kind); $(id).value = ''; });
}
document.addEventListener('keydown', (event) => {
  if (event.altKey || event.metaKey || event.ctrlKey) return;
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName);
  if (typing) return;
  if (event.key === 'ArrowDown') { event.preventDefault(); stepRecord(1); }
  if (event.key === 'ArrowUp') { event.preventDefault(); stepRecord(-1); }
});
safe(bootstrap);
