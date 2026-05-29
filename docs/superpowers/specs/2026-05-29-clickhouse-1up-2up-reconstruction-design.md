# ClickHouse 1UP/2UP reconstruction (`pm_reconstruct` v2) — design

- **Date:** 2026-05-29
- **Status:** approved (pending written-spec review)
- **Supersedes the IO layer of:** `feat/pm-odds-reconstruction` (CSV deriver). The pricing
  logic and engines are reused unchanged.

## 1. Purpose

Produce engine-derived **1UP** and **2UP** fair + margined odds for every betslip
pricing moment in the real backtest dataset, so a downstream simulation can estimate
the NGR/GGR impact of 1UP/2UP products versus plain 1X2 against actually-placed bets.

The previous deriver read a flat CSV export and only handled prematch with a hardcoded
`(0,0)` score. This version:

- reads from **ClickHouse** (the source is too large for a CSV round-trip),
- prices **prematch and live** with the real score,
- runs **V2 + V3 + V4** engines,
- writes results back to **ClickHouse** (`risk_Lorenzo`).

## 2. Data source and sink

### Source
Table: `bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo`

Snapshot log; one row = `(event, market, line, selection)` at a given `odds_timestamp`
and in-play state. Sampled opportunistically from placed-bet betslips, so snapshots are
**not evenly spaced** — alignment must be tolerant of timestamp skew between markets.

Columns consumed:

| Column | Meaning | Role |
|---|---|---|
| `brand` | betpawa country brand | grouping / output |
| `event_id` | betpawa internal event id | primary event key |
| `sr_id` | Sportradar event id | cross-ref / output |
| `sr_start_time` | scheduled kickoff | timeline anchor |
| `event_name` | human label | output |
| `market_name` | which market | family routing |
| `handicap / 4.0` | the line (note `/4.0`, not `/4`, or quarter lines truncate) | O/U line, goal index |
| `selection_name` | Home/Draw/Away or Over/Under or Home/Away/None | outcome routing |
| `in_play` | prematch (false) vs live (true) | critical split |
| `true_proba` | fair model probability | **primary engine input** |
| `price` | offered odds | reference only (NOT an engine input — see §5) |
| `odds_timestamp` | snapshot time | carry-forward alignment |
| `home_score` / `away_score` | score at snapshot | live `score` input + next-goal line |

Markets included (filter):

- `1X2 - FT`
- `Total Score Over/Under - FT`
- `Total Score Over/Under - FT - Home Team`
- `Total Score Over/Under - FT - Away Team`
- Next-goal: `market_name = '{n} Goal'` where `handicap = n*4`. Prematch uses goal #1
  (`handicap = 4`); live needs **all** next-goal lines so the correct one can be picked
  by score (see §6).

Base WHERE: `true_proba IS NOT NULL AND true_proba != 0`.

### Sink
A table in `risk_Lorenzo` (the only DB the user can write). Schema in §7. Created if
absent; the batch job appends a run (run identity via a `run_ts` / `source` column so
re-runs are distinguishable).

### Connection
A Teleport DB proxy is assumed already listening locally. The connection adapter takes
`host`, `port`, `user`, `password`, `database` from environment variables (or a small
config object) and connects with `clickhouse-connect` (HTTP interface). No Teleport
orchestration inside the job — if the proxy is down, the job fails fast with a clear
message. `clickhouse-connect` is added as a project dependency.

## 3. Architecture

Four units, each understandable and testable in isolation:

1. **`clickhouse_io.py`** — connection adapter. `query_arrow(sql) -> table/iterator` for
   reads; `insert(table, rows)` for batched writes. Reads connection config from env.
   No business logic, no SQL strings baked in beyond what the caller passes.
2. **Extraction SQL** (`queries.py`) — a single ordered scan selecting the market
   families and applying `handicap/4.0`, ordered by `(event_id, in_play,
   odds_timestamp)`. **Alignment is NOT done in SQL.** ClickHouse `ASOF JOIN` is
   strictly 1:1 (one right-row match per left-row), so it cannot gather every O/U line
   plus all three next-goal selections for a single moment — it was the wrong tool.
   Instead the scan returns one row per `(selection, timestamp)` and the Python reducer
   carries values forward.
3. **`pricing.py`** — pure-Python pricing + alignment. `moments_from_rows` is a
   carry-forward reducer (the ClickHouse equivalent of the CSV deriver's `MarketState`):
   streaming the ordered scan, it keeps the latest `true_proba` per
   `(market, line, selection)` and emits one Moment per distinct timestamp at which a
   full 1X2 triple has been seen, resetting state at each `(event_id, in_play)`
   boundary. Then renormalization, input assembly (no devig), next-goal-line selection,
   and the engine calls. Ported from `pm_reconstruct.py` (`_side_cells`, DP cache).
4. **`reconstruct_clickhouse.py`** — CLI orchestrator: stream + price, batched insert to
   `risk_Lorenzo`, write a reliability report.

Python owns the carry-forward alignment; ClickHouse just streams the ordered scan.
Streaming per `(event_id, in_play)` keeps memory flat and runs resumable per event.

## 4. Data flow

```
ClickHouse bi_Samuel.<table>
   │  extraction SQL: single ordered scan (event_id, in_play, odds_timestamp)
   ▼
raw selection rows  (one per market/line/selection/timestamp)
   │  moments_from_rows: carry-forward reducer → one Moment per timestamp
   │  with a full carried 1X2 triple (latest O/U + next-goal carried forward)
   ▼
pricing-moment  (event_id, in_play, moment_ts, brand, sr_id, score,
                 1X2 true_proba triple, O/U (line, over_prob) list,
                 next-goal home/away/none true_proba by line)
   ▼
pricing.py
   ├─ renormalize 1X2 to Σ≈1.0
   ├─ derive cap odds = 1 / (p * 1.02)   (flat 2% margin, brand-neutral)
   ├─ pick next-goal line = home_score + away_score + 1  (prematch ⇒ goal #1)
   ├─ assemble engine kwargs (probabilities, no devig)
   └─ price V2, V3, V4  (shared rounded-λ DP cache)
   ▼
enriched rows  →  batched insert  →  risk_Lorenzo.<table>
                                  →  reliability report (markdown)
```

## 5. Engine input adaptation

The engines (`engine_v2/v3/v4.price_early_payout_markets`) are already
**probability-driven**: `p_home_win/p_draw/p_away_win`, `total_ou=[(line, over_prob)]`,
`home_ou`, `away_ou`, `ftts_home_prob`, `ftts_away_prob`. So `true_proba` maps in
directly and **no devig is performed** (the old `devig_two_way`/`devig_three_way` calls
are dropped).

The functions also require decimal **1X2 odds** (`home_1x2_odds`, `draw_1x2_odds`,
`away_1x2_odds`), used for the selection **cap** and as the trailing-selection base when
live. We deliberately **do not** feed offered `price`: brand-specific margin
configurations make `price` inconsistent across brands and would leak that margin into
the cap. Instead, after renormalizing the 1X2 triple:

```
cap_odds_side = 1.0 / (p_side_renormalized * 1.02)
```

i.e. fair odds with a flat **2% margin** baked in, brand-neutral. The engine's
reductions/cap then operate on these synthetic source odds. `price` is retained in the
data for optional later reference but is not an engine input.

`max_home_lead` / `max_away_lead`: the engine uses these for history-aware live
deactivation. Betslip snapshots are opportunistic, so we cannot reconstruct a faithful
lead history. We approximate them from the max score observed in the snapshots we *do*
have for that `event_id`, and document the limitation (an unobserved 1-0→1-1 swing could
mis-price). Prematch rows use `(0,0)` and `0/0`.

## 6. Live / score handling

- `score = (home_score, away_score)` from the snapshot feeds the engine directly. When
  `score != (0,0)` the engine deactivates already-triggered sides (1UP for any lead, 2UP
  for `|diff| >= 2`), per existing logic.
- **Next-goal line selection:** the relevant next goal is goal number
  `home_score + away_score + 1`. Prematch (0 goals) ⇒ goal #1 ⇒ `handicap = 4`. Live
  picks the goal market whose `handicap/4.0 == total_goals + 1`. If that line is absent
  in the snapshot, 1UP is left unpriced for that moment (2UP still prices) — mirrors the
  old "no FTTS ⇒ no 1UP" behavior.
- FTTS comes from the 3-way next-goal market (Home / Away / None). Require all three;
  a 2-way devig would force home+away=1 and bias 1UP.

## 7. Output schema (`risk_Lorenzo.<table>`)

One row per `(event_id, in_play, moment_ts)`:

- **Keys / context:** `event_id`, `sr_id`, `brand`, `event_name`, `sr_start_time`,
  `in_play`, `moment_ts`, `home_score`, `away_score`, `run_ts`.
- **Reconstructed inputs:** `p_home`, `p_draw`, `p_away` (renormalized),
  `lambda_home`, `lambda_away`, `ftts_home`, `ftts_away`, `has_1up`.
- **Confidence:** `max_input_staleness_seconds`, `est_input_drift_pct`.
- **Per engine × market × side** (`{v2,v3,v4}` × `{1up,2up}` × `{home,away}`):
  `_odds`, `_prob`, `_ev`.

Insert in batches (e.g. 10k rows) with retry. Re-runs are distinguished by `run_ts` /
`source`; the consumer can select the latest run.

## 8. Error handling

- Skip a pricing moment lacking a complete 1X2 triple, or with no derivable O/U ≤ T
  (engine returns a deactivated all-None result) — dropped, not inserted, and counted.
- Renormalization guard: if the raw 1X2 `true_proba` sum drifts beyond a tolerance
  (e.g. ±5%), still renormalize but flag the row in the report's distribution.
- Connection / proxy failure: fail fast with an actionable message (proxy not
  listening, auth, db not writable).
- Insert failures: retry the batch; on repeated failure, abort with the last successful
  `event_id` logged so the run can resume.

## 9. Reliability report (markdown)

- Source table + filters; rows scanned vs pricing moments emitted.
- Split: prematch vs live counts; rows with 1UP priced vs 2UP-only.
- Per-market drift/hour (implied-prob) and staleness distribution (p50/p90/max).
- Renormalization-drift distribution and count of flagged rows.
- DP cache info.
- Explicit note on the `max_lead` approximation limitation.

## 10. Testing (TDD)

Unit (synthetic in-memory rows, no live ClickHouse):

- 1X2 renormalization (incl. drift flag boundary).
- Cap-odds derivation `1/(p*1.02)`.
- Next-goal-line selection by score (prematch ⇒ goal #1; 1-1 ⇒ goal #3; missing line ⇒
  1UP unpriced).
- carry-forward assembly: latest-value-per-series, one moment per full-1X2 timestamp,
  state reset across event/in_play boundaries, staleness over used inputs.
- Engine-input adaptation: probabilities passed without devig; deactivation when
  `score != (0,0)`.
- Output-row shape: all `{v2,v3,v4}×{1up,2up}×{home,away}` cells present.

Integration:

- One smoke test gated on a reachable proxy (skipped otherwise): tiny query → price →
  insert into a scratch table → read back.

## 11. Out of scope / YAGNI

- No Teleport login/proxy orchestration inside the job (assumes proxy is up).
- No retention/cleanup of `risk_Lorenzo` output (the consumer/owner manages that).
- No comparison of engine output against captured `1x2_1up_ft`/`1x2_2up_ft` odds — this
  job only *produces* reconstructed prices; comparison/backtest is the downstream sim.
- `price` is stored but not modeled.

## 12. Open implementation-plan concerns (not blocking this spec)

- **Branch strategy:** the work needs both the `pm_reconstruct` lineage (on
  `feat/pm-odds-reconstruction`) and `engine_v4` (currently untracked on
  `feat/pricer-v4-engine`). The plan must decide the integration branch and how V4
  lands.
- **Exact `risk_Lorenzo` output table name** and whether a DDL migration is checked in.
- **Confirming the next-goal `selection_name` vocabulary** (Home / Away / None labels)
  against the real table.
