// =============================================================================
// State (all client-side, persisted via localStorage)
// =============================================================================
//   bookmakers      : {bm: bool}        — chip on/off, hides table columns
//   expanded_events : {id: true}        — cards with OU lines revealed
//   search          : string            — substring filter on home/away
//   kickoff_window  : "all" | seconds   — hide events kicking off > now+window
// =============================================================================

const LS = {
  load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
    catch { return fallback; }
  },
  save(key, value) { localStorage.setItem(key, JSON.stringify(value)); },
};

// -----------------------------------------------------------------------------
// Bookmaker chips
// -----------------------------------------------------------------------------
function initBookmakerChips() {
  const stored = LS.load('bookmakers', {});
  document.querySelectorAll('.chip[data-bookmaker]').forEach(c => {
    const bm = c.dataset.bookmaker;
    const on = stored[bm] !== false;
    c.classList.toggle('on', on);
    document.body.classList.toggle(`hide-${bm}`, !on);
    c.addEventListener('click', () => {
      const nowOn = !c.classList.contains('on');
      c.classList.toggle('on', nowOn);
      document.body.classList.toggle(`hide-${bm}`, !nowOn);
      stored[bm] = nowOn;
      LS.save('bookmakers', stored);
    });
  });
}

// -----------------------------------------------------------------------------
// Tabs (events-list page only)
// -----------------------------------------------------------------------------
function initTabs() {
  document.querySelectorAll('.tab[data-status]').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      const stored = LS.load('country_league_filter', {country_id: '', league_id: ''});
      const params = new URLSearchParams({
        status:  t.dataset.status,
        country: stored.country_id || '',
        league:  stored.league_id  || '',
      });
      window.htmx.ajax('GET', `/events?${params.toString()}`,
                       {target: '#events-list', swap: 'outerHTML'});
    });
  });
}

// -----------------------------------------------------------------------------
// Kickoff timing filter
// -----------------------------------------------------------------------------
function applyKickoffFilter() {
  const win = LS.load('kickoff_window', 'all');
  const cutoffSec = (win === 'all') ? null
                                    : (Date.now() / 1000) + Number(win);
  document.querySelectorAll('.card[data-kickoff-utc]').forEach(card => {
    if (cutoffSec === null) {
      card.classList.remove('hidden-by-time');
      return;
    }
    const t = Date.parse(card.dataset.kickoffUtc);
    if (isNaN(t)) {
      // No parseable kickoff (shouldn't happen) — always show
      card.classList.remove('hidden-by-time');
      return;
    }
    const ks = t / 1000;
    // Hide only events that kick off AFTER the window. Live/ended events
    // (kickoff in the past) always pass.
    card.classList.toggle('hidden-by-time', ks > cutoffSec);
  });
}

function initKickoffFilter() {
  const current = LS.load('kickoff_window', 'all');
  const customInput = document.getElementById('kickoff-custom-hours');

  // If the stored window is a number that doesn't match any pill,
  // it's a custom hours window — populate the input and clear pills.
  const pillValues = new Set(
    Array.from(document.querySelectorAll('.chip.kick[data-window]'))
         .map(c => c.dataset.window)
  );
  const isCustom = current !== 'all' && !pillValues.has(String(current));

  document.querySelectorAll('.chip.kick[data-window]').forEach(c => {
    c.classList.toggle('on', !isCustom && c.dataset.window === current);
    c.addEventListener('click', () => {
      document.querySelectorAll('.chip.kick').forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      if (customInput) customInput.value = '';
      LS.save('kickoff_window', c.dataset.window);
      applyKickoffFilter();
    });
  });

  if (customInput) {
    if (isCustom) customInput.value = String(Number(current) / 3600);
    customInput.addEventListener('input', () => {
      const hours = parseFloat(customInput.value);
      if (!isFinite(hours) || hours <= 0) {
        // Empty / invalid → revert to "All"
        document.querySelectorAll('.chip.kick').forEach(x => x.classList.remove('on'));
        document.querySelector('.chip.kick[data-window="all"]')?.classList.add('on');
        LS.save('kickoff_window', 'all');
      } else {
        document.querySelectorAll('.chip.kick').forEach(x => x.classList.remove('on'));
        LS.save('kickoff_window', Math.round(hours * 3600));
      }
      applyKickoffFilter();
    });
  }
}

// -----------------------------------------------------------------------------
// Search bar
// -----------------------------------------------------------------------------
function applySearchFilter() {
  const q = (LS.load('search', '') || '').trim().toLowerCase();
  document.querySelectorAll('.card[data-event-name]').forEach(card => {
    if (!q) {
      card.classList.remove('hidden-by-search');
      return;
    }
    card.classList.toggle('hidden-by-search',
                          !card.dataset.eventName.includes(q));
  });
}

function initSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.value = LS.load('search', '') || '';
  input.addEventListener('input', () => {
    LS.save('search', input.value);
    applySearchFilter();
  });
}

// -----------------------------------------------------------------------------
// Per-card "Show OU lines" expand toggle
// -----------------------------------------------------------------------------
function applyExpandedState() {
  const expanded = LS.load('expanded_events', {});
  document.querySelectorAll('.card[data-event-id]').forEach(card => {
    const open = !!expanded[card.dataset.eventId];
    card.classList.toggle('expanded', open);
    const btn = card.querySelector('.expand-toggle');
    if (btn) {
      btn.textContent = open ? btn.dataset.expandedLabel
                             : btn.dataset.collapsedLabel;
    }
  });
}

function initExpandToggles() {
  document.querySelectorAll('.expand-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.card');
      if (!card) return;
      const id = card.dataset.eventId;
      const stored = LS.load('expanded_events', {});
      if (stored[id]) delete stored[id];
      else stored[id] = true;
      LS.save('expanded_events', stored);
      applyExpandedState();
    });
  });
}

// -----------------------------------------------------------------------------
// Country/League cascading dropdowns
// -----------------------------------------------------------------------------
function initCountryLeagueFilter() {
  const countrySel = document.getElementById('country-select');
  const leagueSel  = document.getElementById('league-select');
  if (!countrySel || !leagueSel) return;

  const dataTag = document.getElementById('country-league-index');
  let index = [];
  if (dataTag && dataTag.textContent) {
    try { index = JSON.parse(dataTag.textContent).items || []; }
    catch { index = []; }
  }

  const stored = LS.load('country_league_filter', {country_id: '', league_id: ''});

  function populateLeagues(country_id) {
    leagueSel.innerHTML = '<option value="">All</option>';
    if (!country_id) {
      leagueSel.disabled = true;
      return;
    }
    const country = index.find(c => c.country_id === country_id);
    if (!country) {
      leagueSel.disabled = true;
      return;
    }
    for (const league of country.leagues) {
      const opt = document.createElement('option');
      opt.value = league.league_id;
      opt.textContent = league.league_name;
      leagueSel.appendChild(opt);
    }
    leagueSel.disabled = country.leagues.length === 0;
  }

  function currentStatus() {
    const activeTab = document.querySelector('.tab[data-status].active');
    return (activeTab && activeTab.dataset.status) || 'upcoming';
  }

  function refresh() {
    const country_id = countrySel.value;
    const league_id  = leagueSel.value;
    LS.save('country_league_filter', {country_id, league_id});
    const params = new URLSearchParams({
      status: currentStatus(),
      country: country_id,
      league:  league_id,
    });
    window.htmx.ajax('GET', `/events?${params.toString()}`,
                     {target: '#events-list', swap: 'outerHTML'});
  }

  countrySel.value = stored.country_id || '';
  populateLeagues(stored.country_id || '');
  if (stored.league_id) {
    try {
      if (leagueSel.querySelector(`option[value="${stored.league_id}"]`)) {
        leagueSel.value = stored.league_id;
      }
    } catch { /* malformed stored value — ignore, fall through to "All" */ }
  }

  countrySel.addEventListener('change', () => {
    populateLeagues(countrySel.value);
    refresh();
  });
  leagueSel.addEventListener('change', refresh);

  // On initial page load, the events-list fragment fires its hx-get on its
  // own (hx-trigger="load"). If we have a stored filter, listen for the
  // initial swap to complete, then re-fire with the filter applied. Using
  // setTimeout(0) here would race the initial unfiltered fetch.
  if (stored.country_id || stored.league_id) {
    const onFirstSwap = (evt) => {
      if (evt.target && evt.target.id === 'events-list') {
        document.body.removeEventListener('htmx:afterSwap', onFirstSwap);
        refresh();
      }
    };
    document.body.addEventListener('htmx:afterSwap', onFirstSwap);
  }
}

// -----------------------------------------------------------------------------
// Wire it all together. After every HTMX swap (polling refresh), re-apply
// client-side state because the new card markup is fresh and class-free.
// -----------------------------------------------------------------------------
function applyAllCardState() {
  applyExpandedState();
  applyKickoffFilter();
  applySearchFilter();
}

function initEventDelegates() {
  document.body.addEventListener('htmx:afterSwap', evt => {
    if (evt.target && evt.target.id === 'events-list') {
      // Wire up newly-inserted toggle buttons + reapply chip/time/search state
      initExpandToggles();
      applyAllCardState();
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initBookmakerChips();
  initTabs();
  initKickoffFilter();
  initSearch();
  initCountryLeagueFilter();
  initExpandToggles();
  initEventDelegates();
  applyAllCardState();
});
