"""Pure pricing core for ClickHouse 1UP/2UP reconstruction. No IO.

true_proba is already fair, so there is NO devig here (unlike the CSV
deriver). The 1X2 triple is renormalized to sum 1, and the engines' required
1X2 decimal odds are synthesized from those probabilities with a flat 2%
margin (brand-neutral) — offered `price` is intentionally NOT used.
"""
from __future__ import annotations

import functools
from datetime import datetime
from typing import Optional

from .constants import (CAP_MARGIN, STALE_GOOD_SEC, STALE_BAD_SEC,
                        DRIFT_GOOD, DRIFT_BAD)
from .constants import (MARKET_1X2, MARKET_OU_TOTAL, MARKET_OU_HOME,
                        MARKET_OU_AWAY, MARKET_NEXT_GOAL,
                        SEL_HOME, SEL_DRAW, SEL_AWAY,
                        SEL_OVER, SEL_NG_HOME, SEL_NG_AWAY)
from odds_scraper.pricer import engine_v3, engine_v4


def renormalize_1x2(p_home: float, p_draw: float, p_away: float):
    """Return (home, draw, away) scaled to sum 1, plus drift = raw_sum - 1."""
    s = p_home + p_draw + p_away
    if s <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return p_home / s, p_draw / s, p_away / s, s - 1.0


def cap_odds_from_prob(prob: float, margin: float = CAP_MARGIN) -> Optional[float]:
    """Synthetic 1X2 source odds for the engine cap: fair odds with a flat
    margin baked in. Returns None for a non-priceable probability."""
    implied = prob * (1.0 + margin)
    if implied <= 0:
        return None
    return 1.0 / implied


def _band(value: float, good: float, bad: float) -> float:
    """1.0 at/below `good`, 0.0 at/above `bad`, linear in between."""
    if value <= good:
        return 1.0
    if value >= bad:
        return 0.0
    return 1.0 - (value - good) / (bad - good)


def confidence_weight(staleness_seconds: float, renorm_drift: float) -> float:
    """A 0..1 trust weight the sim can multiply by: the freshness band times
    the 1X2-consistency band (drift magnitude; sign ignored). 1.0 = fresh and
    consistent, 0.0 = stale or badly inconsistent."""
    w = _band(staleness_seconds, STALE_GOOD_SEC, STALE_BAD_SEC) * \
        _band(abs(renorm_drift), DRIFT_GOOD, DRIFT_BAD)
    return round(w, 4)


def next_goal_index(home_score: int, away_score: int) -> int:
    """Goal number of the next goal = goals already scored + 1.
    The next-goal market line (handicap/4.0) equals this index."""
    return home_score + away_score + 1


def assemble_engine_kwargs(moment: dict) -> dict:
    """Build the kwargs accepted by every engine's price_early_payout_markets
    from a Moment dict. Renormalizes 1X2, synthesizes cap odds, passes O/U
    over-probabilities and FTTS through unchanged (already fair)."""
    ph, pd, pa, _drift = renormalize_1x2(
        moment["p_home_raw"], moment["p_draw_raw"], moment["p_away_raw"])
    return dict(
        p_home_win=ph, p_draw=pd, p_away_win=pa,
        home_1x2_odds=cap_odds_from_prob(ph),
        draw_1x2_odds=cap_odds_from_prob(pd),
        away_1x2_odds=cap_odds_from_prob(pa),
        total_ou=list(moment["total_ou"]),
        home_ou=list(moment["home_ou"]),
        away_ou=list(moment["away_ou"]),
        ftts_home_prob=moment["ftts_home"],
        ftts_away_prob=moment["ftts_away"],
        score=(int(moment["home_score"]), int(moment["away_score"])),
    )


_dp_cached = None


def install_dp_cache(round_dp: int = 4):
    """Monkeypatch ever_leads_probability in both engines to share one
    lru_cache keyed on rounded (lambda_h, lambda_a, initial_diff). The DP is
    identical across engines. Returns restore().

    Not re-entrant: call restore() before installing again, or the second
    install captures the first wrapper as its base."""
    global _dp_cached
    originals = {
        m: m.ever_leads_probability for m in (engine_v3, engine_v4)
    }
    base = engine_v3.ever_leads_probability

    @functools.lru_cache(maxsize=200_000)
    def _cached(lh: float, la: float, d: int):
        return base(lh, la, d)

    def wrapper(lambda_h, lambda_a, initial_diff):
        return _cached(round(lambda_h, round_dp), round(lambda_a, round_dp), initial_diff)

    _dp_cached = _cached
    for m in originals:
        m.ever_leads_probability = wrapper

    def restore():
        for m, fn in originals.items():
            m.ever_leads_probability = fn

    return restore


def dp_cache_info():
    if _dp_cached is None:
        raise RuntimeError("dp_cache_info() called before install_dp_cache()")
    return _dp_cached.cache_info()


_ENGINES = {"v3": engine_v3, "v4": engine_v4}


def _ev(prob, odds):
    if prob is None or odds is None:
        return None
    return prob * odds - 1.0


def _side_cells(prefix, res, market_key, prob_key_home, prob_key_away):
    m = res[market_key]
    ph, pa = res[prob_key_home], res[prob_key_away]
    oh, oa = m["home_margin"], m["away_margin"]
    return {
        f"{prefix}_home_odds": oh, f"{prefix}_home_prob": ph, f"{prefix}_home_ev": _ev(ph, oh),
        f"{prefix}_away_odds": oa, f"{prefix}_away_prob": pa, f"{prefix}_away_ev": _ev(pa, oa),
    }


DEFAULT_ENGINES = ("v3", "v4")


def price_moment(moment: dict, *, run_ts: str, max_home_lead: int,
                 max_away_lead: int, engines=DEFAULT_ENGINES) -> dict | None:
    """Price one moment with the selected engines (subset of v3/v4). Returns an
    OUTPUT_COLUMNS-shaped dict, or None if the moment carries no priceable
    market (no full 1X2, or no derivable lambda). Cells for engines not in
    `engines` are omitted (written NULL)."""
    ph, pd, pa, drift = renormalize_1x2(
        moment["p_home_raw"], moment["p_draw_raw"], moment["p_away_raw"])
    if not (ph > 0 and pd > 0 and pa > 0):
        return None
    kw = assemble_engine_kwargs(moment)
    kw["max_home_lead"] = max_home_lead
    kw["max_away_lead"] = max_away_lead

    results = {name: _ENGINES[name].price_early_payout_markets(**kw) for name in engines}
    # The first selected engine gates lambda (the DP is identical across engines).
    gate = results[engines[0]]
    if gate["lambda_home"] is None or gate["lambda_away"] is None:
        return None

    row = {
        "run_ts": run_ts, "brand": moment["brand"],
        "event_id": moment["event_id"], "sr_id": moment["sr_id"],
        "event_name": moment["event_name"], "sr_start_time": moment["sr_start_time"],
        "in_play": moment["in_play"], "moment_ts": moment["moment_ts"],
        "home_score": int(moment["home_score"]), "away_score": int(moment["away_score"]),
        "p_home": ph, "p_draw": pd, "p_away": pa,
        "lambda_home": gate["lambda_home"], "lambda_away": gate["lambda_away"],
        "ftts_home": kw["ftts_home_prob"], "ftts_away": kw["ftts_away_prob"],
        "max_input_staleness_seconds": int(moment["max_input_staleness_seconds"]),
        "renorm_drift": round(drift, 6),
        "confidence": confidence_weight(moment["max_input_staleness_seconds"], drift),
    }
    for name, res in results.items():
        row.update(_side_cells(f"{name}_1up", res, "market_1up", "p_home_1", "p_away_1"))
        row.update(_side_cells(f"{name}_2up", res, "market_2up", "p_home_2", "p_away_2"))
    return row


_TS_FMT = "%Y-%m-%d %H:%M:%S"
_OU_FAMILY = {MARKET_OU_TOTAL: "total_ou", MARKET_OU_HOME: "home_ou",
              MARKET_OU_AWAY: "away_ou"}
_META_KEYS = ("event_id", "sr_id", "brand", "event_name", "sr_start_time", "in_play")


def _is_next_goal(market_name) -> bool:
    return market_name == MARKET_NEXT_GOAL


def _ts_pair(v):
    """Normalize a timestamp value (str or datetime) to (datetime, str)."""
    if isinstance(v, datetime):
        return v, v.strftime(_TS_FMT)
    s = str(v)[:19]
    return datetime.strptime(s, _TS_FMT), s


def _to_line(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class _CarryState:
    """Latest (true_proba, capture_dt) per market series within one
    (event_id, in_play) block, carried forward across opportunistic
    snapshots — the ClickHouse equivalent of the CSV deriver's MarketState."""
    __slots__ = ("x12", "ou", "ng")

    def __init__(self):
        self.x12 = {}                                              # selection -> (proba, dt)
        self.ou = {"total_ou": {}, "home_ou": {}, "away_ou": {}}   # family -> {line: (proba, dt)}
        self.ng = {}                                               # line -> {selection: (proba, dt)}

    def update(self, market_name, line, selection, proba, dt):
        if market_name == MARKET_1X2 and selection in (SEL_HOME, SEL_DRAW, SEL_AWAY):
            self.x12[selection] = (proba, dt)
        elif market_name in _OU_FAMILY and selection == SEL_OVER and line is not None:
            self.ou[_OU_FAMILY[market_name]][line] = (proba, dt)
        elif _is_next_goal(market_name) and line is not None:
            self.ng.setdefault(line, {})[selection] = (proba, dt)

    def has_full_1x2(self):
        return self.x12.keys() >= {SEL_HOME, SEL_DRAW, SEL_AWAY}


def _emit_moment(state: "_CarryState", meta: dict, score, dt, ts_str,
                 fresh_seconds=None):
    """Build a Moment from carried-forward state, or None if 1X2 incomplete.
    Picks the next-goal line active for the current score and reports the worst
    staleness among the inputs actually used.

    When `fresh_seconds` is set, inputs older than that (relative to the moment
    ts) are excluded: the moment is dropped if any 1X2 leg is too old, and
    stale O/U lines / next-goal are left out — so a rarely-recaptured fringe
    line can't drag the price or the staleness metric."""
    if not state.has_full_1x2():
        return None

    def fresh(capture_dt):
        return fresh_seconds is None or (dt - capture_dt).total_seconds() <= fresh_seconds

    if not all(fresh(state.x12[s][1]) for s in (SEL_HOME, SEL_DRAW, SEL_AWAY)):
        return None   # the 1X2 anchor itself isn't fresh enough

    hs, as_ = score
    active = float(next_goal_index(hs, as_))
    used = [state.x12[s][1] for s in (SEL_HOME, SEL_DRAW, SEL_AWAY)]

    def ou_list(family):
        out = []
        for line, (proba, d) in state.ou[family].items():
            if not fresh(d):
                continue
            out.append((line, proba))
            used.append(d)
        return sorted(out)

    total_ou = ou_list("total_ou")
    home_ou = ou_list("home_ou")
    away_ou = ou_list("away_ou")
    ng_line = state.ng.get(active, {})
    ftts_home = ftts_away = None
    if SEL_NG_HOME in ng_line and SEL_NG_AWAY in ng_line \
            and fresh(ng_line[SEL_NG_HOME][1]) and fresh(ng_line[SEL_NG_AWAY][1]):
        ftts_home, ftts_home_dt = ng_line[SEL_NG_HOME]
        ftts_away, ftts_away_dt = ng_line[SEL_NG_AWAY]
        used.extend((ftts_home_dt, ftts_away_dt))
    stale = int(round(max((dt - min(used)).total_seconds(), 0.0)))
    return {
        **meta, "moment_ts": ts_str,
        "home_score": hs, "away_score": as_,
        "p_home_raw": state.x12[SEL_HOME][0],
        "p_draw_raw": state.x12[SEL_DRAW][0],
        "p_away_raw": state.x12[SEL_AWAY][0],
        "total_ou": total_ou, "home_ou": home_ou, "away_ou": away_ou,
        "ftts_home": ftts_home, "ftts_away": ftts_away,
        "max_input_staleness_seconds": stale,
    }


def moments_from_rows(rows, *, aggregate_brands: bool = False, fresh_seconds=None):
    """Carry-forward alignment. Stream raw selection rows (one per
    market/line/selection/timestamp) and emit one Moment per distinct timestamp
    at which a full 1X2 triple has been seen, using the latest carried value of
    every other series.

    State resets at each (event_id, in_play, score) boundary — plus brand
    unless `aggregate_brands`. **score is in the key so inputs are never mixed
    across a goal** (carrying a 0-0 1X2 into a 2-0 moment would feed the engine
    inconsistent odds); score is component-wise non-decreasing over time so
    score-states stay contiguous in time order.

    `aggregate_brands` pools every brand's captures for an event into one
    denser timeline (valid because true_proba is brand-independent), yielding
    fresher inputs / more moments; emitted rows are tagged brand="ALL". The
    rows must then be ordered (event_id, in_play, odds_timestamp); otherwise
    (brand, event_id, in_play, odds_timestamp).

    A moment is emitted only at a timestamp where a 1X2 was actually captured
    (the freshest carried O/U / next-goal fill it in). This mirrors the sim:
    a 1UP/2UP bet stands in for a 1X2 bet placed at that moment, so we price at
    the 1X2 snapshot times — not at O/U-only timestamps that were never 1X2
    bet moments.

    Each row must have: event_id, sr_id, brand, event_name, sr_start_time,
    in_play, ts, home_score, away_score, market_name, line, selection_name,
    true_proba."""
    cur_block = None
    state = None
    meta = None
    score = (0, 0)
    cur_dt = cur_ts_str = None
    x12_at_cur_ts = False       # did a 1X2 row arrive at the ts we're accumulating?

    for r in rows:
        r_score = (int(r["home_score"]), int(r["away_score"]))
        if aggregate_brands:
            block = (r["event_id"], r["in_play"], r_score)
        else:
            block = (r["brand"], r["event_id"], r["in_play"], r_score)
        dt, ts_str = _ts_pair(r["ts"])
        new_block = block != cur_block
        new_ts = new_block or cur_ts_str is None or ts_str != cur_ts_str

        if new_ts and state is not None and x12_at_cur_ts:
            m = _emit_moment(state, meta, score, cur_dt, cur_ts_str, fresh_seconds)
            if m is not None:
                yield m

        if new_block:
            cur_block = block
            state = _CarryState()
            meta = {k: r[k] for k in _META_KEYS}
            if aggregate_brands:
                meta["brand"] = "ALL"
            score = r_score
        if new_ts:
            cur_dt, cur_ts_str = dt, ts_str
            x12_at_cur_ts = False

        state.update(r["market_name"], _to_line(r["line"]),
                     r["selection_name"], r["true_proba"], dt)
        if r["market_name"] == MARKET_1X2:
            x12_at_cur_ts = True

    if state is not None and x12_at_cur_ts:
        m = _emit_moment(state, meta, score, cur_dt, cur_ts_str, fresh_seconds)
        if m is not None:
            yield m


def run_pricing(moments_iter, *, run_ts: str, engines=DEFAULT_ENGINES):
    """Price a stream of moments with the selected engines, tracking
    per-(brand, event) max lead so live deactivation is history-aware across
    the moments we observed. Moments must be grouped by (brand, event_id)
    contiguously (extraction SQL ORDER BY guarantees it). Yields output rows
    (skips None)."""
    cur_event = object()
    max_h = max_a = 0
    for m in moments_iter:
        if (m["brand"], m["event_id"]) != cur_event:
            cur_event, max_h, max_a = (m["brand"], m["event_id"]), 0, 0
        diff = int(m["home_score"]) - int(m["away_score"])
        max_h = max(max_h, diff)
        max_a = max(max_a, -diff)
        row = price_moment(m, run_ts=run_ts, max_home_lead=max_h,
                           max_away_lead=max_a, engines=engines)
        if row is not None:
            yield row
