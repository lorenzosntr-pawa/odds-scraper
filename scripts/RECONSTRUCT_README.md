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
- `--brand` — restrict to one brand. The source duplicates each event across ~13 brands,
  and `true_proba` is brand-independent, so **one brand is already a complete set of
  reconstructed odds**. Recommended (it's ~1/13th of 250M rows).
- `--limit` — cap rows scanned (smoke runs only).
- `--recreate` — drop + recreate the output table first. Use it the first time, or after
  any schema change.
- `--run-ts` — a tag stamped on every row so you can tell runs apart. Use the current
  date/time; it does not need to be exact.

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
- **Confidence:** `max_input_staleness_seconds`, `renorm_drift` (see below).
- **Prices:** for each engine (`v3`, `v4`) × market (`1up`, `2up`) × side (`home`,
  `away`): `_odds`, `_prob`, `_ev` (= prob × odds − 1). e.g. `v4_2up_home_odds`.

**How a moment is built (carry-forward):** 1X2, O/U, and next-goal are captured at
slightly different times. We read rows in time order per (brand, event, prematch/live),
keep the latest value of each market/line/selection, and whenever a full 1X2 is on hand
at a timestamp we emit a moment using the freshest value of everything.

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
