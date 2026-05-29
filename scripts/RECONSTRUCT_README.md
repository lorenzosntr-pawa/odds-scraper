# 1UP / 2UP odds reconstruction (ClickHouse)

This batch reconstructs **V3 and V4** 1UP/2UP fair + margined odds from the real
betslip odds log in ClickHouse, and writes the results back into ClickHouse for the
NGR/GGR simulation to consume.

- **Reads:** `bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo`
- **Writes:** `risk_Lorenzo.oneup_twoup_reconstructed`
- **Code:** `src/odds_scraper/reconstruct/`, run via `scripts/reconstruct_clickhouse.py`

---

## 1. Setup (one time)

Install dependencies:

```powershell
uv sync --extra dev
```

## 2. Open the tunnel to ClickHouse

The script connects to a **local port** that Teleport forwards to ClickHouse. Either:

- **Teleport Connect app:** click **Connect** on the ClickHouse database; it opens a local
  proxy and shows a `127.0.0.1` host + port + db user. Leave it open. **OR**
- **CLI:** `tsh login --proxy <cluster-address>:443`, then
  `tsh proxy db <db-name> --db-user <user> --tunnel --port 8123` (leave it running).

## 3. Point the script at the tunnel

In the terminal you'll run the script from, set these (values from step 2):

```powershell
$env:CH_HOST     = "localhost"
$env:CH_PORT     = "<the local port>"
$env:CH_USER     = "<your db user>"
$env:CH_PASSWORD = ""
$env:CH_DATABASE = "risk_Lorenzo"
```

(These last only for the current terminal window.)

## 4. Sanity check

```powershell
uv run pytest tests/reconstruct/test_integration.py -v
```

`2 passed` = connection + table labels + a real price-and-insert all work. `2 skipped`
= env vars not set in this terminal.

To inspect the source table's columns / labels at any time:

```powershell
uv run python scripts/inspect_source.py --source bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo
```

## 5. Run it

**Smoke run** (one brand, capped, throwaway table — finishes in ~1 min):

```powershell
uv run python scripts/reconstruct_clickhouse.py `
  --source bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo `
  --output risk_Lorenzo.recon_smoke `
  --report data/reconstruct_smoke.md `
  --run-ts "2026-05-29 15:00:00" `
  --brand betpawa-ghana `
  --limit 500000 `
  --recreate
```

**Full single-brand run** (drop `--limit`, real output table):

```powershell
uv run python scripts/reconstruct_clickhouse.py `
  --source bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo `
  --output risk_Lorenzo.oneup_twoup_reconstructed `
  --report data/reconstruct_report.md `
  --run-ts "2026-05-29 15:00:00" `
  --brand betpawa-ghana `
  --recreate
```

Flags:
- `--engines` — comma list of engines to compute: `v3`, `v4`, or `v3,v4` (default
  `v3,v4`). Engines not listed get NULL columns. **V4 alone needs no next-goal data**
  (its prematch 1UP is DP-direct), so `--engines v4` prices every row without depending
  on next-goal availability — and the next-goal market is then **skipped entirely from
  the scan** (smaller/faster). Next-goal is pulled only when `v3` is selected (V3's 1UP
  needs FTTS). λ always comes from the O/U lines, never from next-goal.
- `--prematch` / `--live` / `--complete` — which in-play states to price.
  **Default is `--complete` (both).** When live rows are included, the script **probes
  the source for a live score** and prints whether real scores are present. If they are,
  live is priced with the real running score (already-ahead sides deactivated); if the
  source has no live score (as it historically did — all 0-0), it warns and prices live
  as 0-0. Prematch is always correct regardless.
- `--brand` — restrict to one brand. The source duplicates each event across ~13 brands,
  and `true_proba` is brand-independent, so **one brand is already a complete set of
  reconstructed odds**.
- `--aggregate-brands` — pool **all** brands per event into one denser timeline (don't
  combine with `--brand`). Because `true_proba` is identical across brands, this just
  samples the same signal at more timestamps → **more moments at higher confidence**
  (the 1X2 anchor finds closer O/U / next-goal captures). Output rows are tagged
  `brand='ALL'`. Preferred for generating the sim's odds set.
- `--limit` — cap rows scanned (smoke runs only).
- `--recreate` — drop + recreate the output table first. Use it the first time, or after
  any schema change.
- `--run-ts` — a tag stamped on every row so you can tell runs apart. Use the current
  date/time; it does not need to be exact.

> **Live scoring.** Live 1UP/2UP needs the running home/away score (to deactivate a side
> that's already gone ahead). Historically this table had `home_score`/`away_score` = 0
> on every row, so live couldn't be scored. The CLI now **probes at runtime** and tells
> you whether the source carries live scores; when present, `--live`/`--complete` price
> with the real score. (Note the `max_lead` history is still inferred from the snapshots
> we observe, so a lead between two unobserved snapshots can be missed — a sampling
> limitation, separate from whether the score column is populated.)

It prints a progress line every 1M source rows and a final summary, and writes the
markdown report.

---

## 6. What the output table looks like

**One row = one "pricing moment":** a single event, one brand, one timestamp, in one
state (prematch or live). An event produces many rows as odds move.

Column groups:

- **Identity / context:** `run_ts`, `brand`, `event_id`, `sr_id`, `event_name`,
  `sr_start_time`, `in_play` (0=prematch, 1=live), `moment_ts`, `home_score`, `away_score`.
- **Reconstructed inputs:** `p_home/p_draw/p_away` (fair 1X2, renormalized to sum 1),
  `lambda_home/lambda_away` (implied scoring rates from O/U), `ftts_home/ftts_away`
  (next-goal probabilities; blank when next-goal data wasn't available).
- **Confidence:** `max_input_staleness_seconds`, `renorm_drift`, and `confidence`
  (a 0–1 weight combining the two — see below; multiply your row value by it).
- **Prices:** for each engine (`v3`, `v4`) × market (`1up`, `2up`) × side (`home`,
  `away`): `_odds`, `_prob`, `_ev` (= prob × odds − 1). e.g. `v4_2up_home_odds`.

> **V4 1UP needs no next-goal data.** V4's prematch 1UP is computed directly from the
> scoring-rate DP (no FTTS / next-goal regression), so V4 produces a 1UP price on *every*
> priceable row — its 1UP coverage ≈ its 2UP coverage. (V2/V3 level-1UP *do* need the
> next-goal market, so their 1UP is sparser.) The report's "V4 1UP priced" line counts
> actual V4 1UP output, not next-goal availability.

**How a moment is built (carry-forward):** 1X2, O/U, and next-goal are captured at
slightly different times. We read rows in time order per (brand, event, prematch/live,
**score**), keep the latest value of each market/line/selection, and emit a moment **at
each timestamp where a 1X2 was captured** — using the freshest carried O/U / next-goal.
Anchoring on the 1X2 snapshot mirrors the sim: a 1UP/2UP bet replaces a 1X2 bet placed
at that moment, so we price at the times a 1X2 was actually offered (not at O/U-only
timestamps).

**Score consistency:** the carry-forward is segmented by **score**, so a moment never
mixes inputs captured at different scores (e.g. a 0-0 1X2 won't be carried into a 2-0
moment). This prevents feeding the engine odds that are inconsistent with the current
score. (Settled O/U lines — e.g. Over 1.5 once 2 goals are in — contribute nothing to λ
anyway: the engine subtracts the current score off each line and ignores lines at/below
it.) A consequence: a live moment only prices when a full 1X2 was captured *at that
score*, so live coverage is lower than prematch — by design, to stay correct.

---

## 7. Reading the two confidence columns

Every row is a price built from three ingredients (1X2, Over/Under, next-goal). These
two columns tell you how trustworthy it is.

### `max_input_staleness_seconds` — how fresh the ingredients are

Age (seconds) of the *oldest* input used. Lower = fresher = better.

- **0–300 (under 5 min):** ✅ Good — reflects the live market. Safe for simulation.
- **300–1800 (5–30 min):** ⚠️ Okay-ish — one input is getting old. Fine prematch, weaker live.
- **over 1800 (30+ min):** ❌ Stale — built on old odds. Don't rely on it.

### `renorm_drift` — how consistent the 1X2 odds were

The three 1X2 outcomes should add up to 100%. This is how far off they were before we
corrected them. **Ignore the minus sign — only the size matters** (negative just means
they summed to under 1, positive to over 1).

- **0 to 0.02:** ✅ Good — consistent inputs. Trust the row.
- **0.02 to 0.05:** ⚠️ Borderline — slightly mismatched, use with mild caution.
- **above 0.05:** ❌ Not good — inputs didn't line up; treat as unreliable.

**Bottom line for simulations:** trust a row when staleness is **low (ideally < 5 min)**
*and* drift is **small (< 0.02, sign ignored)**. Drop or down-weight anything with
staleness over ~30 min or drift over 0.05.

### `confidence` — the two combined into one weight (0–1)

`confidence` packages the two checks into a single number so you can **multiply each
row's value by it** instead of hand-picking cutoffs:

- each input maps to a 0–1 band — staleness: 1.0 at ≤300s, 0.0 at ≥1800s, linear between;
  drift: 1.0 at ≤0.02, 0.0 at ≥0.05, linear between (sign ignored) —
- `confidence = staleness_band × drift_band`.

So `1.0` = fresh **and** consistent (full weight); `0.0` = stale **or** badly inconsistent
(ignore); values between scale smoothly. Use it directly as a row weight, or keep only
rows above a threshold (e.g. `confidence > 0`, or `> 0.5` to be strict).

`scripts/usable_counts.py --table <t>` reports coverage and how many V4 rows survive the
strict / moderate / custom bands.

---

## 8. Gotchas (already handled in code — here for reference)

- The next-goal market name is the literal string `{handicap} Goal`; the goal number is
  in the `handicap` column (`handicap/4` = goal index).
- 1X2 / next-goal team selections are `1`/`X`/`2`; next-goal no-goal is `None`; O/U is
  `Over`/`Under`.
- The client runs with **compression off** (the Teleport proxy rejects lz4) and
  **sessionless** (so the streaming read and the insert don't deadlock).
- `CREATE TABLE IF NOT EXISTS` won't change an existing table — use `--recreate` after a
  schema change.
