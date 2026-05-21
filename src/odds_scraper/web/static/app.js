// Bookmaker chip toggle + localStorage persistence.
// The chip's `on` class drives a body class which CSS uses to hide
// matching [data-bookmaker="..."] columns inside #events-list and the
// detail-page history table.
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

// Tab switching via HTMX programmatic request (only present on the
// events-list page, not the detail page).
function initTabs() {
  document.querySelectorAll('.tab[data-status]').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      window.htmx.ajax('GET', `/events?status=${t.dataset.status}`,
                       {target: '#events-list', swap: 'outerHTML'});
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initChips();
  initTabs();
});
