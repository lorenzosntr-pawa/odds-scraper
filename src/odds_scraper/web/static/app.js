// =============================================================================
// State (all client-side, persisted via localStorage)
// =============================================================================
//   bookmakers              : {bm: bool}              — chip on/off, hides table columns
//   card_market_collapse    : {group_key: true}       — per-market collapse state on cards
//   country_league_filter   : {country_id, league_id} — home-page cascading dropdown selection
//   search                  : string                  — substring filter on home/away
//   kickoff_window          : "all" | seconds         — hide events kicking off > now+window
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
function localDateString(date) {
  // Return YYYY-MM-DD in the browser's local timezone. The date picker emits
  // local-time dates, so we compare against the card's kickoff_utc parsed
  // to local components — not its UTC components.
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function applyKickoffFilter() {
  const win = LS.load('kickoff_window', 'all');
  document.querySelectorAll('.card[data-kickoff-utc]').forEach(card => {
    const t = Date.parse(card.dataset.kickoffUtc);
    if (isNaN(t)) {
      // No parseable kickoff (shouldn't happen) — always show
      card.classList.remove('hidden-by-time');
      return;
    }
    if (win === 'all') {
      card.classList.remove('hidden-by-time');
      return;
    }
    if (typeof win === 'string' && win.startsWith('date:')) {
      const targetDate = win.slice(5);
      const cardLocalDate = localDateString(new Date(t));
      card.classList.toggle('hidden-by-time', cardLocalDate !== targetDate);
      return;
    }
    // Numeric seconds window: hide events that kick off AFTER now + win.
    // Live/ended events (kickoff in the past) always pass.
    const cutoffSec = (Date.now() / 1000) + Number(win);
    const ks = t / 1000;
    card.classList.toggle('hidden-by-time', ks > cutoffSec);
  });
}

function initKickoffFilter() {
  const current = LS.load('kickoff_window', 'all');
  const dateInput = document.getElementById('kickoff-date');
  const chips = Array.from(document.querySelectorAll('.chip.kick[data-window]'));

  // Restore stored state on boot.
  const isDate = typeof current === 'string' && current.startsWith('date:');
  if (isDate && dateInput) {
    dateInput.value = current.slice(5);
    chips.forEach(c => c.classList.remove('on'));
  } else {
    chips.forEach(c => c.classList.toggle('on', c.dataset.window === String(current)));
    if (dateInput) dateInput.value = '';
  }

  // Chip click: clear date input, set chip state.
  chips.forEach(c => {
    c.addEventListener('click', () => {
      chips.forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      if (dateInput) dateInput.value = '';
      LS.save('kickoff_window', c.dataset.window);
      applyKickoffFilter();
    });
  });

  // Date change: clear chips, save tagged string.
  if (dateInput) {
    dateInput.addEventListener('input', () => {
      const v = dateInput.value;
      if (!v) {
        // User cleared the date — revert to "All" so something is selected.
        chips.forEach(x => x.classList.remove('on'));
        document.querySelector('.chip.kick[data-window="all"]')?.classList.add('on');
        LS.save('kickoff_window', 'all');
      } else {
        chips.forEach(x => x.classList.remove('on'));
        LS.save('kickoff_window', `date:${v}`);
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
// Per-market collapse (each market block in a card)
// -----------------------------------------------------------------------------
// localStorage shape: {group_key: true} where group_key is the value of
// data-group-key on the .market-block (e.g., "1x2_ft", "next_goal_ft_1.0").
// true = collapsed. Absent = use the EXPANDED_BY_DEFAULT allowlist.
const EXPANDED_BY_DEFAULT = new Set(["1x2_ft", "1x2_1up_ft", "1x2_2up_ft"]);

function applyMarketCollapseState() {
  const stored = LS.load('card_market_collapse', {});
  document.querySelectorAll('.market-block[data-group-key]').forEach(block => {
    const key = block.dataset.groupKey;
    let collapsed;
    if (key in stored) {
      collapsed = !!stored[key];
    } else {
      collapsed = !EXPANDED_BY_DEFAULT.has(key);
    }
    block.classList.toggle('collapsed', collapsed);
  });
}

function initMarketCollapse() {
  document.body.addEventListener('click', evt => {
    const label = evt.target.closest('.market-block .group-label');
    if (!label) return;
    const block = label.closest('.market-block');
    const key = block && block.dataset.groupKey;
    if (!key) return;
    const stored = LS.load('card_market_collapse', {});
    const currentlyCollapsed = block.classList.contains('collapsed');
    stored[key] = !currentlyCollapsed;
    LS.save('card_market_collapse', stored);
    applyMarketCollapseState();
  });
}

// -----------------------------------------------------------------------------
// Card-level master expand toggle ("Show N more markets" button)
// -----------------------------------------------------------------------------
// Hides the .card-extras region (everything past the 1x2 family) behind
// a single button. Persisted per event_id in localStorage so refreshing
// remembers which cards the user opened. Independent of per-market
// collapse — extras can still be individually collapsed when visible.
function applyCardExpandedState() {
  const stored = LS.load('expanded_events', {});
  document.querySelectorAll('.card[data-event-id]').forEach(card => {
    const open = !!stored[card.dataset.eventId];
    card.classList.toggle('expanded', open);
    const btn = card.querySelector('.expand-toggle');
    if (btn) {
      btn.textContent = open ? btn.dataset.expandedLabel
                             : btn.dataset.collapsedLabel;
    }
  });
}

function initCardExpand() {
  document.body.addEventListener('click', evt => {
    const btn = evt.target.closest('.expand-toggle');
    if (!btn) return;
    const card = btn.closest('.card[data-event-id]');
    if (!card) return;
    const id = card.dataset.eventId;
    const stored = LS.load('expanded_events', {});
    if (stored[id]) delete stored[id];
    else stored[id] = true;
    LS.save('expanded_events', stored);
    applyCardExpandedState();
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
  applyMarketCollapseState();
  applyCardExpandedState();
  applyKickoffFilter();
  applySearchFilter();
}

function initEventDelegates() {
  document.body.addEventListener('htmx:afterSwap', evt => {
    if (evt.target && evt.target.id === 'events-list') {
      // After a fragment swap, re-apply per-market collapse + chip/time/search state
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
  initMarketCollapse();
  initCardExpand();
  initEventDelegates();
  applyAllCardState();
});
