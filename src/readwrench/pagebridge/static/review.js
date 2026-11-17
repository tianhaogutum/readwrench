// Embedded into the generated review HTML. No network, PDF parser, or server.
const plan = JSON.parse(document.getElementById('plan').textContent);
const choices = new Map(plan.rows.filter(r => r.original !== null).map(r => [r.original, r.modified]));
const reviewed = new Set();
const $ = id => document.getElementById(id);
const PAGE_SIZE = 20;
let pageIndex = 0;

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function pageInfo(side, number) {
  return plan.pages[side].find(page => page.page === number);
}

function image(side, number) {
  const img = el('img');
  img.src = `assets/${side}-${String(number).padStart(3, '0')}.png`;
  img.alt = `${side} page ${number}`;
  img.loading = 'lazy';
  return img;
}

function panel(side, number) {
  const node = el('div', undefined, 'panel');
  if (number === null) {
    node.append(el('div', 'No counterpart', 'empty'));
    return node;
  }
  node.append(image(side, number));
  const info = el('div');
  info.append(
    el('strong', `${side === 'original' ? 'Original' : 'Revised'} page ${number}`),
    el('p', pageInfo(side, number).text_excerpt || 'No extracted text: inspect the image.', 'excerpt'),
  );
  node.append(info);
  return node;
}

function suggestion(original) {
  return plan.rows.find(row => row.original === original);
}

function needsAttention(original) {
  return suggestion(original).status === 'pending' || choices.get(original) !== suggestion(original).modified;
}

function visibleChoices() {
  const filtered = [...choices].filter(([original]) => !$('pendingOnly').checked || needsAttention(original));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  pageIndex = Math.min(Math.max(pageIndex, 0), pages - 1);
  const start = pageIndex * PAGE_SIZE;
  $('previous').disabled = pageIndex === 0;
  $('next').disabled = pageIndex === pages - 1;
  $('pagination').hidden = filtered.length <= PAGE_SIZE;
  $('pagePosition').textContent = filtered.length ? `${start + 1}-${Math.min(start + PAGE_SIZE, filtered.length)} of ${filtered.length}` : '0 pairs';
  return filtered.slice(start, start + PAGE_SIZE);
}

function unpairedModified() {
  const used = new Set([...choices.values()].filter(value => value !== null));
  return Array.from({length: plan.sources.modified.pages}, (_, i) => i + 1).filter(i => !used.has(i));
}

function validateChoices() {
  const used = [...choices.values()].filter(value => value !== null);
  const duplicates = used.filter((value, index) => used.indexOf(value) !== index);
  if (duplicates.length) {
    throw new Error(`Revised pages assigned more than once: ${[...new Set(duplicates)].join(', ')}. Choose a different page or Deleted.`);
  }
}

function updateSummary() {
  $('status').textContent = `${reviewed.size}/${choices.size} pairs reviewed`;
  $('error').textContent = '';
  try { validateChoices(); } catch (error) { $('error').textContent = error.message; }
  const list = $('insertList');
  list.replaceChildren();
  for (const number of unpairedModified()) {
    const item = el('li');
    item.append(image('modified', number), el('p', `Revised page ${number}`));
    list.append(item);
  }
  if (!list.children.length) list.append(el('li', 'None.'));
}

function draw() {
  const root = $('rows');
  root.replaceChildren();
  for (const [original, modified] of visibleChoices()) {
    const row = suggestion(original);
    const pending = needsAttention(original);
    const card = el('article', undefined, `pair ${pending ? 'pending' : 'suggested'}`);
    const head = el('div', undefined, 'row-head');
    head.append(el('strong', `Original ${original} →`));
    const select = el('select');
    select.setAttribute('aria-label', `Counterpart for original page ${original}`);
    const deleted = el('option', 'Deleted (no counterpart)');
    deleted.value = '';
    select.append(deleted);
    for (let number = 1; number <= plan.sources.modified.pages; number++) {
      const option = el('option', `Revised page ${number}`);
      option.value = String(number);
      select.append(option);
    }
    select.value = modified === null ? '' : String(modified);
    select.addEventListener('change', () => {
      choices.set(original, select.value === '' ? null : Number(select.value));
      reviewed.delete(original);
      $('insertionsReviewed').checked = false;
      draw();
    });
    const badge = el('span', pending ? 'Pending review' : 'Suggested', 'badge');
    const label = el('label');
    const check = el('input');
    check.type = 'checkbox';
    check.checked = reviewed.has(original);
    check.addEventListener('change', () => {
      if (check.checked) reviewed.add(original); else reviewed.delete(original);
      updateSummary();
    });
    label.append(check, document.createTextNode(' Reviewed this pair'));
    head.append(select, badge, label);
    const panels = el('div', undefined, 'panels');
    panels.append(panel('original', original), panel('modified', modified));
    const details = el('div', undefined, 'details');
    details.textContent = `${row.score === null ? 'No suggested match' : `Suggested evidence ${row.score.toFixed(3)}`} · ` +
      `${row.reasons.join(', ') || 'Distinct text and visual match'}` +
      (row.alternatives.length ? ' · Alternatives: ' + row.alternatives.map(a => `${a.modified} (${a.score.toFixed(3)})`).join(', ') : '') +
      (modified !== row.modified ? ' · Manually reassigned; suggested evidence refers to the original suggestion.' : '');
    card.append(head, panels, details);
    root.append(card);
  }
  updateSummary();
}

function buildMapping(approved) {
  validateChoices();
  const reviewer = $('reviewer').value.trim();
  if (approved && (!reviewer || reviewer.length > 120 || reviewed.size !== choices.size || !$('insertionsReviewed').checked)) {
    throw new Error('Enter your name, review every original page, and confirm insertions, deletions and order.');
  }
  const data = structuredClone(plan);
  data.rows = [];
  for (const [original, modified] of choices) {
    data.rows.push({original, modified, confirmed: reviewed.has(original),
      status: approved ? 'approved' : 'draft', reasons: ['human_review']});
  }
  for (const modified of unpairedModified()) {
    data.rows.push({original: null, modified, confirmed: $('insertionsReviewed').checked,
      status: approved ? 'approved' : 'draft', reasons: ['human_review']});
  }
  data.review = approved ? {
    reviewer, reviewed_at: new Date().toISOString(), method: 'local-html',
    approved_map: mapSnapshot(data),
  } : null;
  if (!approved) data.draft = {reviewer, insertions_reviewed: $('insertionsReviewed').checked,
    page_index: pageIndex, filter_pending: $('pendingOnly').checked, snapshot: mapSnapshot(data),
    saved_at: new Date().toISOString()};
  return data;
}

function mapSnapshot(data) {
  return {
    original_sha256: data.sources.original.sha256,
    modified_sha256: data.sources.modified.sha256,
    pairs: data.rows.map(row => [row.original, row.modified]),
  };
}

function snapshotMatches(snapshot, data) {
  return snapshot && typeof snapshot === 'object' &&
    snapshot.original_sha256 === data.sources.original.sha256 &&
    snapshot.modified_sha256 === data.sources.modified.sha256 &&
    Array.isArray(snapshot.pairs) &&
    JSON.stringify(snapshot.pairs) === JSON.stringify(data.rows.map(row => [row.original, row.modified]));
}

function downloadJSON(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2) + '\n'], {type: 'application/json'});
  const link = el('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function restoreMapping(data) {
  if (!data || data.schema !== plan.schema) throw new Error('This file is not a readwrench pdf-pages mapping.');
  for (const side of ['original', 'modified']) {
    if (data.sources?.[side]?.sha256 !== plan.sources[side].sha256 || data.sources?.[side]?.pages !== plan.sources[side].pages) {
      throw new Error('The saved mapping belongs to different source PDFs. Open its original review instead.');
    }
  }
  const counts = {original: plan.sources.original.pages, modified: plan.sources.modified.pages};
  const seen = {original: new Set(), modified: new Set()};
  const restored = new Map(), checked = new Set();
  if (!Array.isArray(data.rows) || !data.rows.length || data.rows.length > counts.original + counts.modified) {
    throw new Error('Saved mapping rows are missing or invalid.');
  }
  for (const row of data.rows) {
    if (!row || (row.original === null && row.modified === null)) throw new Error('A row needs at least one page.');
    for (const side of ['original', 'modified']) {
      const number = row[side];
      if (number === null) continue;
      if (!Number.isInteger(number) || number < 1 || number > counts[side] || seen[side].has(number)) {
        throw new Error(`Invalid or repeated ${side} page in saved mapping.`);
      }
      seen[side].add(number);
    }
    if (row.original !== null) {
      restored.set(row.original, row.modified);
      if (row.confirmed === true) checked.add(row.original);
    }
  }
  if (seen.original.size !== counts.original || seen.modified.size !== counts.modified) {
    throw new Error('Saved mapping does not account for every page.');
  }
  const stateMatches = snapshotMatches(data.review?.approved_map, data) || snapshotMatches(data.draft?.snapshot, data);
  // Validate fully before mutating the current review, so bad imports are harmless.
  choices.clear();
  for (const original of [...restored.keys()].sort((a, b) => a - b)) choices.set(original, restored.get(original));
  reviewed.clear();
  if (stateMatches) for (const original of checked) reviewed.add(original);
  const name = data.draft?.reviewer ?? data.review?.reviewer ?? '';
  $('reviewer').value = typeof name === 'string' ? name.slice(0, 120) : '';
  $('insertionsReviewed').checked = Boolean(stateMatches && (data.draft?.insertions_reviewed === true ||
    (data.review && data.rows.every(row => row.confirmed === true))));
  pageIndex = Number.isInteger(data.draft?.page_index) ? data.draft.page_index : 0;
  $('pendingOnly').checked = data.draft?.filter_pending === true;
  draw();
  if (!stateMatches && (checked.size || data.draft?.insertions_reviewed || data.review)) {
    $('error').textContent = 'Choices restored. Review again because saved checks do not match this page map.';
  }
}

$('pendingOnly').addEventListener('change', () => { pageIndex = 0; draw(); });
$('previous').addEventListener('click', () => { pageIndex--; draw(); });
$('next').addEventListener('click', () => { pageIndex++; draw(); });
$('checkAll').addEventListener('click', () => {
  for (const [original] of visibleChoices()) reviewed.add(original);
  draw();
});
for (const [button, approved, filename] of [
  ['download', true, 'approved-mapping.json'], ['saveDraft', false, 'draft-mapping.json'],
]) {
  $(button).addEventListener('click', () => {
    try { downloadJSON(buildMapping(approved), filename); $('error').textContent = ''; }
    catch (error) { $('error').textContent = error.message; }
  });
}
$('resume').addEventListener('click', () => $('importMapping').click());
$('importMapping').addEventListener('change', async () => {
  try {
    const file = $('importMapping').files[0];
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) throw new Error('Mapping JSON exceeds the 4 MiB limit.');
    restoreMapping(JSON.parse(await file.text()));
  } catch (error) { $('error').textContent = error.message; }
  finally { $('importMapping').value = ''; }
});
draw();
