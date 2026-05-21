// Bookmaker chip toggle + localStorage persistence.
function initChips() {
  const stored = JSON.parse(localStorage.getItem('bookmakers') || '{}');
  document.querySelectorAll('.chip[data-bookmaker]').forEach(c => {
    const bm = c.dataset.bookmaker;
    const on = stored[bm] !== false;  // default on
    c.classList.toggle('on', on);
    document.body.classList.toggle(`hide-${bm}`, !on);
    c.addEventListener('click', () => {
      const nowOn = !c.classList.contains('on');
      c.classList.toggle('on', nowOn);
      document.body.classList.toggle(`hide-${bm}`, !nowOn);
      stored[bm] = nowOn;
      localStorage.setItem('bookmakers', JSON.stringify(stored));
    });
  });
}

// Tab switching via HTMX programmatic request.
function initTabs() {
  document.querySelectorAll('.tab[data-status]').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      window.htmx.ajax('GET', `/events?status=${t.dataset.status}`, '#events-list');
    });
  });
}

// Card open/close — update the list's ?open= URL param then re-request.
window.toggleEvent = function(eventId) {
  const list = document.getElementById('events-list');
  if (!list) return;
  const current = list.dataset.open || '';
  const ids = new Set(current.split(',').filter(Boolean));
  if (ids.has(eventId)) ids.delete(eventId);
  else ids.add(eventId);
  const status = list.dataset.status || 'live';
  const openParam = Array.from(ids).join(',');
  const url = `/events?status=${status}${openParam ? '&open=' + openParam : ''}`;
  window.htmx.ajax('GET', url, '#events-list');
};

document.addEventListener('DOMContentLoaded', () => {
  initChips();
  initTabs();
});
