// Firefox uses `browser.*` (native, promise-based), Chrome uses `chrome.*`.
// This shim ensures the exact same code works across both platforms.
const api = typeof browser !== 'undefined' ? browser : chrome;

// Selector for each job card container. Based on the attribute
// "componentkey", which is more stable than hashed classes (_6e41928b, etc.)
// but could change if LinkedIn refactors the component - review if it stops
// finding cards.
const CARD_SELECTOR = 'div[role="button"][componentkey^="job-card-component-ref"]';

// The detail panel is identified by this internal LinkedIn attribute
// (SDUI screen name) - significantly more stable than any CSS class.
const DETAIL_SELECTOR = '[data-sdui-screen="com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"]';

// Synchronous source of truth for deduplication. Prevents race conditions
// when checking "already exists" against storage (which is async) when multiple
// scans trigger almost simultaneously.
const seenIds = new Set();

// Queue that serializes ALL write operations to storage.local, whether from
// listing cards or detail panels. Without this, two near-simultaneous writes
// read storage before the other writes, causing the second write to overwrite
// the first completely.
let writeChain = Promise.resolve();

function withWriteLock(fn) {
  const result = writeChain.then(fn);
  // Chain while silencing errors so a failure does not block the queue
  writeChain = result.catch(err => console.error('[LinkedIn Scanner] Error saving:', err));
  return result;
}

async function loadSeenIdsFromStorage() {
  const stored = await api.storage.local.get('jobs');
  const jobs = stored.jobs ?? {};
  Object.keys(jobs).forEach(id => seenIds.add(id));
}

function extractJobCardData(cardEl) {
  const data = {};

  // Title: 1st attempt via aria-label on the "Dismiss" button, 2nd via aria-hidden span
  const dismissBtn = cardEl.querySelector('button[aria-label^="Dismiss"]');
  if (dismissBtn) {
    data.title = dismissBtn.getAttribute('aria-label')
      .replace(/^Dismiss\s+/, '')
      .replace(/\s+job$/, '');
  } else {
    const titleSpan = cardEl.querySelector('span[aria-hidden="true"]');
    data.title = titleSpan?.childNodes[0]?.textContent?.trim() ?? null;
  }

  // Company + location: by structural position, not by class name
  const textParagraphs = [...cardEl.querySelectorAll('p')]
    .filter(p => p.textContent.trim().length > 0 && !p.querySelector('span[aria-hidden]'));

  data.company = textParagraphs[0]?.textContent?.trim() ?? null;
  const locationRaw = textParagraphs[1]?.textContent?.trim() ?? null;
  data.location = locationRaw;

  const modalityMatch = locationRaw?.match(/\(([^)]+)\)/);
  data.modality = modalityMatch ? modalityMatch[1] : null;

  const dateSpan = [...cardEl.querySelectorAll('span[aria-hidden="true"]')]
    .find(s => /ago$/.test(s.textContent.trim()));
  data.postedRaw = dateSpan?.textContent?.trim() ?? null;

  // Temporary ID for deduplication until we resolve the actual job ID
  // (pending: capture from URL when opening details)
  data.tempId = `${data.title}|${data.company}`;
  data.scannedAt = new Date().toISOString();

  return data;
}

async function saveJob(data) {
  if (!data.title || !data.company) return; // Discard empty/broken extractions

  if (seenIds.has(data.tempId)) return; // Synchronous check, no race condition
  seenIds.add(data.tempId); // Flagged BEFORE await to block concurrent calls

  await withWriteLock(async () => {
    const stored = await api.storage.local.get('jobs');
    const jobs = stored.jobs ?? {};
    jobs[data.tempId] = data;
    await api.storage.local.set({ jobs });
  });
  console.log('[LinkedIn Scanner] New job saved:', data.title, '-', data.company);
}

// Phrases appearing on buttons/links in the detail panel that are NOT
// salary/modality/type "pills" (Easy Apply, Save, Premium...).
// Excluded by string matching rather than trying to isolate the exact container.
const PILL_EXCLUDE = /premium|apply|save|message|show match|dismiss|more options|sign in/i;

function extractJobDetailData(panelEl) {
  const data = {};

  // Title + actual ID: from link pointing to /jobs/view/<id>/
  const titleLink = panelEl.querySelector('a[href*="/jobs/view/"]');
  if (titleLink) {
    data.title = titleLink.textContent.trim();
    const idMatch = titleLink.getAttribute('href').match(/\/jobs\/view\/(\d+)/);
    data.jobId = idMatch ? idMatch[1] : null;
  }

  // Company: link pointing to /company/<slug>/
  const companyLink = panelEl.querySelector('a[href*="/company/"]');
  data.company = companyLink?.textContent.trim() ?? null;

  // Location / date / applicant count: the <p> containing "ago", delimited by "·"
  const metaP = [...panelEl.querySelectorAll('p')].find(p => /\bago\b/.test(p.textContent));
  if (metaP) {
    const parts = metaP.textContent.split('·').map(s => s.trim()).filter(Boolean);
    data.location = parts[0] ?? null;
    data.postedRaw = parts[1] ?? null;
    data.applicantsRaw = parts[2] ?? null;
  }

  // Salary / modality / employment type: "pills" identified by text patterns,
  // not by class (no distinguishing attributes exist between them)
  const pillTexts = [...panelEl.querySelectorAll('a[aria-disabled] span')]
    .map(s => s.textContent.trim())
    .filter(t => t.length > 0 && t.length < 30 && !PILL_EXCLUDE.test(t));

  data.salary = pillTexts.find(t => /eur|usd|gbp|[$€£]\s?\d|\d+k\b/i.test(t)) ?? null;
  data.modality = pillTexts.find(t => /remote|hybrid|on-?site/i.test(t)) ?? null;
  data.employmentType = pillTexts.find(t => /full-time|part-time|contract|internship|temporary/i.test(t)) ?? null;

  // Full JD. The "...more" button is removed (located INSIDE the same container)
  // so it does not end up appended to the text output.
  const jdEl = panelEl.querySelector('[data-testid="expandable-text-box"]');
  let jd = null;
  if (jdEl) {
    const jdClone = jdEl.cloneNode(true);
    jdClone.querySelectorAll('[data-testid="expandable-text-button"]').forEach(btn => btn.remove());
    jd = jdClone.textContent.trim();
  }
  data.jd = jd;

  data.url = data.jobId ? `https://www.linkedin.com/jobs/view/${data.jobId}/` : null;

  data.scannedAt = new Date().toISOString();
  return data;
}

const seenDetailKeys = new Set();

async function saveDetailJob(data) {
  if (!data.title || !data.company) return;

  const key = data.jobId ? `id:${data.jobId}` : `${data.title}|${data.company}`;
  if (seenDetailKeys.has(key)) return;
  seenDetailKeys.add(key);

  await withWriteLock(async () => {
    const stored = await api.storage.local.get('jobs');
    const jobs = stored.jobs ?? {};

    // If a partial listing record existed (without actual ID), replace
    // it with the full object instead of leaving duplicates
    const tempKey = `${data.title}|${data.company}`;
    if (key !== tempKey && jobs[tempKey]) delete jobs[tempKey];

    jobs[key] = data;
    await api.storage.local.set({ jobs });
  });
  console.log('[LinkedIn Scanner] Saved detail:', data.title, '-', data.company, data.salary ? `(${data.salary})` : '');
}

// LinkedIn virtualizes the list (data-component-type="LazyColumn"), meaning
// multiple detail panels can exist in the DOM simultaneously (visible + cached/hidden).
// querySelector would blindly pick the first, which isn't guaranteed to be visible.
// getBoundingClientRect/offsetParent fail here: LinkedIn's detail selector applies
// utility class "display:contents", giving it zero dimensions regardless of visibility.
// Instead, if multiple panels exist, extract from all and select the one with the
// richest dataset (non-empty JD).
function scanDetailPanel() {
  const panels = document.querySelectorAll(DETAIL_SELECTOR);
  if (panels.length === 0) return;

  let best = null;
  panels.forEach(panel => {
    const data = extractJobDetailData(panel);
    if (!best || (data.jd && !best.jd)) best = data;
  });
  if (best) saveDetailJob(best);
}

function scanVisibleCards() {
  document.querySelectorAll(CARD_SELECTOR).forEach(card => {
    const data = extractJobCardData(card);
    saveJob(data);
  });
}

function scanAll() {
  scanVisibleCards();
  scanDetailPanel();
}

function debounce(fn, delayMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delayMs);
  };
}

const debouncedScan = debounce(scanAll, 400);

// Load persisted cache and execute initial scan
loadSeenIdsFromStorage().then(scanAll);

// Rescan when new cards render (infinite scroll) using debouncing:
// rapid mutation bursts (clicks, tooltips) execute once things settle.
const observer = new MutationObserver(() => {
  debouncedScan();
});
observer.observe(document.body, { childList: true, subtree: true });

console.log('[LinkedIn Scanner] Content script active.');