# OmniTrader

> Open-source **adaptive crypto quant trading framework**. Consolidates the best
> ideas of four personal repos (QuantAgent, quant-trading, nexus-terminal,
> cryptogene) into one auditable, self-hostable package — and fixes the bugs
> that made them unsafe to run with real money.

Two things were added since v0.1 and they are the reason to upgrade:

| | what it is | why it matters |
|---|---|---|
| **`omni_trader/evolution`** | real genetic search over strategy **and** risk genes | strategies are *filtered*, not hand-tuned — with mandatory out-of-sample validation |
| **`omni_trader/web`** | zero-dependency REST server + login + browser console | front end and back end actually run together, no cloud, no npm, no accounts required |

Neither adds a single third-party runtime dependency. The core — including the
web server, password hashing, sessions and the charts on the front end — is pure
standard library.

## Why this exists (audit-driven)

An independent audit of the 4 source repos found the same pattern everywhere:
**real ideas, but fatal defects that lose money or crash.** OmniTrader is the
consolidated, corrected successor.

| Defect (in original repos) | Status in OmniTrader |
|---|---|
| Backtest lookahead (fill at signal close) | ✅ fills at **next bar open** |
| Wrong equity for short positions | ✅ correct long/short accounting |
| Sharpe computed as a t-statistic | ✅ annualized Sharpe/Sortino on equity curve |
| Risk manager never called in loop → kill-switch dead | ✅ called every bar; halts block new entries |
| Close orders sent `quantity=0` (never close) | ✅ real position qty used |
| Float `round()` LOT_SIZE drift | ✅ `Decimal`-based `round_step_size` |
| No license / no tests / no CI | ✅ MIT + pytest (67 tests) + GitHub Actions |
| **RSI smoothing branch inverted** (`avg_l/avg_g`) → every bar after the seed window was mirrored around 50, silently flipping mean-reversion's long/short logic | ✅ standard `RS = avg_gain/avg_loss` throughout |
| **Grid ladder built once from bar 0** → price walks out of it, bot goes silent forever | ✅ re-centres on a rolling EMA anchor |
| **Indicators recomputed from scratch every bar** (O(n²) backtest) | ✅ streaming O(1) indicators; ~2 500-bar backtest is **20–50 ms** instead of seconds |

## Quick start

```bash
./run.sh --strategy momentum                     # single backtest
./run.sh evolve --population 32 --generations 12 # genetic strategy search
./run.sh web --port 8787                         # web console + REST API
./run.sh --test                                  # 67 tests
```

No install needed — `run.sh` works straight from a clone. Or install it as a
command:

```bash
pip install -e .
omnitrader backtest --strategy momentum --symbol BTCUSDT --capital 10000
omnitrader evolve   --bars 3000 --population 32 --generations 12
omnitrader web      --port 8787 [--open]
```

---

## 1. Strategy evolution (the "gene" part)

A **genome** is one complete, runnable trading configuration: strategy species +
its parameters + risk parameters (sizing, stop, take, daily-loss and drawdown
kill levels). Strategy and risk live in the same genome on purpose — a good
entry signal with suicidal sizing is still a losing configuration.

```
 generation 0   best +11.31  mean -3.64   mean_reversion:11 momentum:11 grid:10
 generation 3   best +14.71  mean +4.48   mean_reversion:12 momentum:16 grid: 4
 generation 6   best +19.28  mean +7.55   momentum:10 mean_reversion:22        <- grid died out
 generation 11  best +20.67  mean +16.71  momentum:32                          <- one species wins
```

**18 evolvable genes**, all ranges bounded to values that would survive a real
exchange (see `omni_trader/evolution/space.py`). Cross-species mating exists so
selection can compare strategy families on equal footing rather than being stuck
with whatever you happened to hand it.

### The part the old `cryptogene` repo got wrong

It mutated parameters, reported the best backtest, and called it a result — no
out-of-sample data, no cost model, no penalty for complexity. It would have
handed you a beautiful loser. What replaces it:

```
        TRAIN (50%)              VALIDATION (25%)            TEST (25%)
   |-------------------|   |-------------------|   |-------------------|
   fitness mostly comes    fitness here too, AND   SEALED. Never touched
   from here               the IS->OOS GAP IS      during evolution. Scored
                           SUBTRACTED             ONCE, for the champion.
```

```
fitness = 0.35 * IS + 0.65 * OOS  -  0.6 * max(0, IS - OOS)
          ...minus additive penalties: thin trading, oversized drawdown,
             hitting a risk kill-switch
```

All penalties **subtract**. Scaling a negative fitness toward zero would turn
−5 into −1.5, which any max-fitness selector reads as "better" — a classic way
to end up selecting losers.

Other deliberate choices:

* **Anti-clone** — a child duplicating a living individual is re-mutated, so the
  population doesn't collapse into ten copies of one genome.
* **Early stopping** — no improvement for `patience` generations ends the run
  instead of grinding out noise.
* **Determinism** — one seeded RNG drives everything; the same seed reproduces
  the same champion. Essential for trusting a result you cannot yet explain.

```python
from omni_trader.data import DataFeed
from omni_trader.evolution import EvolutionEngine, EvolutionConfig

feed = DataFeed.generate_regimes(n=3000)      # or DataFeed.from_json("BTCUSDT_1h.json")
res = EvolutionEngine(feed, EvolutionConfig(population_size=32, generations=12)).run()

print(res.champion.pretty())     # the winning configuration
print(res.champion_test)         # performance on data the search never saw
print(res.champion.to_dict())    # save it / reproduce it later
```

See `examples/06_genetic_evolution.py` for the full walkthrough, including the
control experiment that shows what searching **structureless noise** looks like.

> A note on synthetic data: `DataFeed.generate_sample` is a random walk with no
> exploitable structure — anything "found" there is luck. `generate_regimes`
> rotates trending / chopping / mean-reverting blocks so different strategies
> have different edges. Both are useful; the first is a **control**, not a
> benchmark.

---

## 2. Web console (the front-end + back-end part)

```bash
omnitrader web --port 8787
# or preset credentials:
OMNITRADER_ADMIN_PASSWORD='...' omnitrader web
```

Open <http://127.0.0.1:8787>. Three tabs:

* **回测实验室** — pick a strategy, its parameter sliders are generated
  automatically from the gene space, run a backtest, see the equity curve, all
  metrics, and every trade.
* **进化实验室** — configure the search (population, generations, participating
  species, data split), press start, and watch generations arrive **live over
  SSE**: a fitness chart, a per-generation table with species composition, and
  the champion card showing IS / OOS / **TEST** side by side, plus its ancestry.
* **关于 / 风险** — what the engine actually does, and the bugs it fixes.

Nothing is loaded from a CDN — no Chart.js, no React bundle, no external font.
The charts are hand-drawn on `<canvas>`, so the console works fully offline.

### Accounts

On first run a random admin password is generated, printed at boot, and saved
`0600` to `~/.omni_trader/admin-password.txt`. **Shipping a hardcoded default
admin password is how self-hosted tools get owned**, so there isn't one.

```bash
omnitrader web --create-user alice secretive-password    # add / reset a user
OMNITRADER_ALLOW_SIGNUP=1 omnitrader web                 # optional self-signup
```

### Security model

| Concern | Implementation |
|---|---|
| Password storage | PBKDF2-HMAC-SHA256, 200k iterations, per-user random salt, verified with `hmac.compare_digest` |
| Sessions | `base64url(payload).HMAC-SHA256(payload, server_secret)`, expiring, server secret stored `0600` |
| No plaintext on disk | passwords never written; the store holds salt + hash only |
| Brute force | per-IP+username throttle: 8 failures → 5-minute lockout |
| Enumeration | identical response and similar timing for "no such user" vs "wrong password" |
| Static files | served from one directory; path traversal outside it is rejected |
| Auth coverage | every `/api/*` route except `health` and `login` requires a token (tested) |

### REST API

```
POST /api/auth/login            -> {token, expires_at}
GET  /api/auth/me               -> {username, role}
POST /api/auth/password         -> change own password
GET  /api/strategies            -> strategy registry + full gene space
POST /api/data/generate         -> synthetic OHLCV summary
POST /api/backtest              -> metrics + equity curve + trades
POST /api/evolution/start       -> {job_id}
GET  /api/evolution/status      -> poll result / history
GET  /api/evolution/stream      -> SSE: generation-by-generation progress
POST /api/evolution/cancel      -> cooperative cancel
GET  /api/jobs                  -> recent jobs
GET  /api/health                -> liveness (no auth)
```

Evolution runs in a background thread keyed by job id, streams progress over
SSE, and can be cancelled mid-flight. One of the tests drives this end to end
through real sockets.

---

## Features

- **Zero-lookahead event backtester** with fees, slippage, realistic intrabar
  stop/take fills.
- **Correct metrics**: total return, CAGR, annualized Sharpe & Sortino, max
  drawdown, win rate, profit factor, Calmar (all in USD).
- **Live-wired risk manager**: risk-based sizing, stop/take, **daily-loss +
  max-drawdown kill switches that actually trigger and flatten the book.**
- **Strategy interface** + 3 reference strategies: `momentum` (Donchian+ADX),
  `mean_reversion` (RSI, optional Bollinger confirmation), `grid` (EMA-anchored).
- **Genetic evolution** with the out-of-sample protocol described above.
- **Self-hosted web console** with real accounts — no third-party auth service.
- **Decimal-safe** quantity rounding for live Binance execution.
- **Safe live adapter**: refuses to trade live without keys + explicit intent.
- CLI, reproducible sample data, MIT license, Docker, CI, 67 tests.

Runnable examples:

| file | what it shows |
|---|---|
| `examples/01_backtest_api.py` | backtest via Python API + parameter sweep + kill-switch proof |
| `examples/02_paper_live.py` | paper trading with `LiveTrader` (same code path as live) |
| `examples/03_onchain_quote.py` | Solana/BSC quotes, simulation-only by default |
| `examples/04_custom_strategy.py` | write your own strategy (dual-MA) |
| `examples/05_fetch_real_data.py` | download real OHLCV (no API key) |
| `examples/06_genetic_evolution.py` | genetic search, three-segment protocol, reusing the champion |

```bash
pytest -q                 # 67 tests (~26s)
python3 scripts/e2e_web.py   # end-to-end: start server, log in, backtest, evolve
```

## Data dependency policy (unchanged)

Core stays dependency-free — everything else is opt-in:

```bash
pip install -e ".[live]"       # ccxt
pip install -e ".[dashboard]"  # streamlit + plotly (legacy UI)
pip install -e ".[onchain]"    # web3 + solders + base58
pip install -e ".[dev]"        # pytest
```

The web console needs **nothing extra**.

## Roadmap

1. ~~Backtest core~~ ✅
2. ~~CEX execution~~ ✅ — `omni_trader.exchange` (paper + Binance via ccxt).
   *Next: unified risk budget across the original L1/L2/L3 tiers.*
3. ~~On-chain execution~~ ✅ (simulation-first) — Jupiter (Solana) + PancakeSwap (BSC).
   *Next: live broadcast wiring + MEV protection.*
4. ~~Dashboard~~ ✅ — plus the new self-hosted web console.
5. ~~Strategy evolution~~ ✅ — real genetic search with out-of-sample validation.
   *Next: parallel evaluation across cores; multi-objective Pareto selection.*
6. **Walk-forward / multi-asset portfolio** — beyond single-symbol, per-species.

## Disclaimer

For research and education. Crypto trading carries risk of total loss. A
strategy that looks excellent on synthetic data usually tells you more about the
generator than about the market. Never run live without testnet validation, hard
position limits, and an understanding of the code. **Not financial advice.**

---

*MIT License — free, auditable, self-hostable. No monthly fees, no black box.*
