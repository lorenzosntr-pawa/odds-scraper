// =============================================================================
// State (all client-side, persisted via localStorage)
// =============================================================================
//   GLOBAL (one value across all tabs):
//     bookmakers              : {bm: bool}              — chip on/off, hides table columns
//     card_market_collapse    : {group_key: true}       — per-market collapse state on cards
//     expanded_events         : {event_id: true}        — master expand toggle per event
//     live_sort               : "minute_desc" | ...     — LIVE-tab card sort
//
//   PER-TAB (scoped by current status — country_league_filter_live,
//            country_league_filter_upcoming, …):
//     country_league_filter   : {country_id, league_id} — cascading dropdowns
//     search                  : string                  — substring filter on home/away
//     kickoff_window          : "all" | seconds | "date:…"
//
// Per-tab keys so filtering by "England" in UPCOMING doesn't carry into ENDED.
// =============================================================================

const LS = {
  load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
    catch { return fallback; }
  },
  save(key, value) { localStorage.setItem(key, JSON.stringify(value)); },
};

// -----------------------------------------------------------------------------
// Per-tab key scoping
// -----------------------------------------------------------------------------
function currentStatus() {
  const active = document.querySelector('.tab[data-status].active');
  return (active && active.dataset.status) || 'upcoming';
}

function filterKey(name) {
  return `${name}_${currentStatus()}`;
}

// -----------------------------------------------------------------------------
// Bookmaker chips (global across tabs)
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
// Tabs
// -----------------------------------------------------------------------------
// `body.tab-{live,upcoming,ended}` mirrors the active tab so CSS can swap
// out filter groups that only make sense on one tab (e.g. Kickoff hidden
// on LIVE, Sort shown only on LIVE).
function applyBodyTabClass() {
  const status = currentStatus();
  document.body.classList.remove('tab-live', 'tab-upcoming', 'tab-ended');
  document.body.classList.add(`tab-${status}`);
}

function _activateTab(status) {
  // Idempotent: flip the .active class, mirror to body, sync filter
  // controls from this tab's saved state, and fetch the matching
  // fragment. Used by both tab clicks and the popstate handler so
  // back/forward navigation behaves identically to a click.
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  const target = document.querySelector(`.tab[data-status="${status}"]`);
  if (target) target.classList.add('active');
  applyBodyTabClass();
  applyCountryLeagueFromStorage();
  applySearchInputFromStorage();
  applyKickoffControlsFromStorage();
  const stored = LS.load(filterKey('country_league_filter'),
                         {country_id: '', league_id: ''});
  const params = new URLSearchParams({
    status,
    country: stored.country_id || '',
    league:  stored.league_id  || '',
  });
  window.htmx.ajax('GET', `/events?${params.toString()}`,
                   {target: '#events-list', swap: 'outerHTML'});
}

function initTabs() {
  document.querySelectorAll('.tab[data-status]').forEach(t => {
    t.addEventListener('click', () => {
      const status = t.dataset.status;
      // Push the new URL so bookmarks, share-links, and browser
      // back/forward work. Without this the URL stays whatever it was
      // when the page loaded and the back link from event detail
      // (which reads ?status=) lands on the wrong tab.
      const stored = LS.load(filterKey('country_league_filter'),
                             {country_id: '', league_id: ''});
      const urlParams = new URLSearchParams({status});
      if (stored.country_id) urlParams.set('country', stored.country_id);
      if (stored.league_id)  urlParams.set('league',  stored.league_id);
      history.pushState({status}, '', `/?${urlParams.toString()}`);
      _activateTab(status);
    });
  });

  // Back/forward navigates between tabs we pushed above. Re-derive
  // the status from the URL and re-activate; pushState is NOT called
  // again here (popstate already moved history).
  window.addEventListener('popstate', () => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get('status') || 'upcoming';
    _activateTab(status);
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
  // On LIVE tab the kickoff filter is hidden and meaningless — make
  // sure no card stays stuck with `hidden-by-time` from a previous tab.
  if (document.body.classList.contains('tab-live')) {
    document.querySelectorAll('.card.hidden-by-time')
      .forEach(c => c.classList.remove('hidden-by-time'));
    return;
  }
  const win = LS.load(filterKey('kickoff_window'), 'all');
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

function applyKickoffControlsFromStorage() {
  const current = LS.load(filterKey('kickoff_window'), 'all');
  const dateInput = document.getElementById('kickoff-date');
  const chips = Array.from(document.querySelectorAll('.chip.kick[data-window]'));
  const isDate = typeof current === 'string' && current.startsWith('date:');
  if (isDate && dateInput) {
    dateInput.value = current.slice(5);
    chips.forEach(c => c.classList.remove('on'));
  } else {
    chips.forEach(c => c.classList.toggle('on', c.dataset.window === String(current)));
    if (dateInput) dateInput.value = '';
  }
}

function initKickoffFilter() {
  const dateInput = document.getElementById('kickoff-date');
  const chips = Array.from(document.querySelectorAll('.chip.kick[data-window]'));

  // Chip click: clear date input, save under this tab's scoped key.
  chips.forEach(c => {
    c.addEventListener('click', () => {
      chips.forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      if (dateInput) dateInput.value = '';
      LS.save(filterKey('kickoff_window'), c.dataset.window);
      applyKickoffFilter();
    });
  });

  // Date change: clear chips, save tagged string under scoped key.
  if (dateInput) {
    dateInput.addEventListener('input', () => {
      const v = dateInput.value;
      if (!v) {
        chips.forEach(x => x.classList.remove('on'));
        document.querySelector('.chip.kick[data-window="all"]')?.classList.add('on');
        LS.save(filterKey('kickoff_window'), 'all');
      } else {
        chips.forEach(x => x.classList.remove('on'));
        LS.save(filterKey('kickoff_window'), `date:${v}`);
      }
      applyKickoffFilter();
    });
  }

  applyKickoffControlsFromStorage();
}

// -----------------------------------------------------------------------------
// Search bar
// -----------------------------------------------------------------------------
function applySearchFilter() {
  const q = (LS.load(filterKey('search'), '') || '').trim().toLowerCase();
  document.querySelectorAll('.card[data-event-name]').forEach(card => {
    if (!q) {
      card.classList.remove('hidden-by-search');
      return;
    }
    card.classList.toggle('hidden-by-search',
                          !card.dataset.eventName.includes(q));
  });
}

function applySearchInputFromStorage() {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.value = LS.load(filterKey('search'), '') || '';
}

function initSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  applySearchInputFromStorage();
  input.addEventListener('input', () => {
    LS.save(filterKey('search'), input.value);
    applySearchFilter();
  });
}

// -----------------------------------------------------------------------------
// Per-market collapse (each market block in a card) — GLOBAL across tabs
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
// Card-level master expand toggle ("Show N more markets" button) — GLOBAL
// -----------------------------------------------------------------------------
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
// `populateLeagues` is module-scope so applyCountryLeagueFromStorage can
// refill the league dropdown without going through the change-event
// (which would trigger an extra HTMX refresh).
let _populateLeagues = (_country_id) => {};

function applyCountryLeagueFromStorage() {
  const countrySel = document.getElementById('country-select');
  const leagueSel  = document.getElementById('league-select');
  if (!countrySel || !leagueSel) return;
  const stored = LS.load(filterKey('country_league_filter'),
                         {country_id: '', league_id: ''});
  countrySel.value = stored.country_id || '';
  _populateLeagues(stored.country_id || '');
  if (stored.league_id) {
    try {
      if (leagueSel.querySelector(`option[value="${stored.league_id}"]`)) {
        leagueSel.value = stored.league_id;
      } else {
        leagueSel.value = '';
      }
    } catch { leagueSel.value = ''; }
  } else {
    leagueSel.value = '';
  }
}

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

  _populateLeagues = function (country_id) {
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
  };

  function refresh() {
    const country_id = countrySel.value;
    const league_id  = leagueSel.value;
    LS.save(filterKey('country_league_filter'), {country_id, league_id});
    const params = new URLSearchParams({
      status: currentStatus(),
      country: country_id,
      league:  league_id,
    });
    window.htmx.ajax('GET', `/events?${params.toString()}`,
                     {target: '#events-list', swap: 'outerHTML'});
  }

  // Initial paint: load this tab's stored country/league into the dropdowns.
  applyCountryLeagueFromStorage();

  countrySel.addEventListener('change', () => {
    _populateLeagues(countrySel.value);
    refresh();
  });
  leagueSel.addEventListener('change', refresh);

  // On initial page load, the events-list fragment fires its hx-get on its
  // own (hx-trigger="load"). If we have a stored filter, listen for the
  // initial swap to complete, then re-fire with the filter applied. Using
  // setTimeout(0) here would race the initial unfiltered fetch.
  const stored = LS.load(filterKey('country_league_filter'),
                         {country_id: '', league_id: ''});
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
// LIVE-tab card sort. Server returns cards ordered by match minute DESC;
// this overlay lets the user pick minute asc/desc or total-goals
// asc/desc. Runs client-side on the rendered fragment — fine for the
// ~tens of cards in a live tab. Other tabs ignore this (the sort chips
// are hidden via CSS).
// -----------------------------------------------------------------------------
function applyLiveSort() {
  if (!document.body.classList.contains('tab-live')) return;
  const cards = Array.from(document.querySelectorAll('#events-list .card'));
  if (cards.length < 2) return;
  const parent = cards[0].parentNode;
  const sort = LS.load('live_sort', 'minute_desc');
  const minute = c => Number(c.dataset.matchMinute || 0);
  const goals  = c => Number(c.dataset.scoreHome || 0)
                    + Number(c.dataset.scoreAway || 0);
  cards.sort((a, b) => {
    switch (sort) {
      case 'minute_asc':  return minute(a) - minute(b);
      case 'goals_desc':  return goals(b)  - goals(a);
      case 'goals_asc':   return goals(a)  - goals(b);
      case 'minute_desc':
      default:            return minute(b) - minute(a);
    }
  });
  for (const c of cards) parent.appendChild(c);
}

function initSortControl() {
  const chips = Array.from(document.querySelectorAll('.chip.sort[data-sort]'));
  if (!chips.length) return;
  const current = LS.load('live_sort', 'minute_desc');
  chips.forEach(c => c.classList.toggle('on', c.dataset.sort === current));
  chips.forEach(c => {
    c.addEventListener('click', () => {
      chips.forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      LS.save('live_sort', c.dataset.sort);
      applyLiveSort();
    });
  });
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
  applyLiveSort();
}

function initEventDelegates() {
  // Stale-swap guard. Polling (`hx-trigger="every Ns"` on the fragment)
  // races with tab clicks and country/league filter changes — both
  // target #events-list. Without a guard, a slow previous-tab poll can
  // arrive after a new tab's fragment and silently clobber it (the
  // .active tab indicator stays put but content reverts). Server tags
  // every fragment with data-status="…" on its wrapper; if the incoming
  // status doesn't match the currently active tab, drop the swap.
  document.body.addEventListener('htmx:beforeSwap', evt => {
    if (!evt.target || evt.target.id !== 'events-list') return;
    const html = evt.detail && evt.detail.serverResponse;
    if (typeof html !== 'string') return;
    const m = html.match(/data-status="([^"]+)"/);
    if (m && m[1] !== currentStatus()) {
      evt.detail.shouldSwap = false;
    }
  });

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
  applyBodyTabClass();
  initKickoffFilter();
  initSortControl();
  initSearch();
  initCountryLeagueFilter();
  initMarketCollapse();
  initCardExpand();
  initEventDelegates();
  applyAllCardState();
});
