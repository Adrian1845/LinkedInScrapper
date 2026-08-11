const api = typeof browser !== 'undefined' ? browser : chrome;

async function render() {
  const stored = await api.storage.local.get('jobs');
  const jobs = Object.values(stored.jobs ?? {});

  document.getElementById('count').textContent = `${jobs.length} saved offers`;

  const list = document.getElementById('list');
  list.innerHTML = '';

  jobs
    .sort((a, b) => new Date(b.scannedAt) - new Date(a.scannedAt))
    .forEach(job => {
      const el = document.createElement('div');
      el.className = 'job';
      el.innerHTML = `
        <b>${job.title ?? '(no title)'}</b>
        <span>${job.company ?? '?'} — ${job.location ?? '?'}</span>
        <span>${job.modality ?? ''} · ${job.postedRaw ?? ''}${job.salary ? ' · ' + job.salary : ''}</span>
        ${job.jd ? `<span title="${job.jd.replace(/"/g, '&quot;')}">${job.jd.slice(0, 80)}…</span>` : ''}
      `;
      list.appendChild(el);
    });
}

document.getElementById('export').addEventListener('click', async () => {
  const stored = await api.storage.local.get('jobs');
  const blob = new Blob([JSON.stringify(stored.jobs ?? {}, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  await api.downloads.download({ url, filename: 'linkedin_jobs.json' });
});

document.getElementById('clear').addEventListener('click', async () => {
  await api.storage.local.remove('jobs');
  render();
});

render();
