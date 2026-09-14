# OmniTrader

> Open-source **adaptive crypto quant trading framework**. Consolidates the best
> ideas of four personal repos (QuantAgent, quant-trading, nexus-terminal,
> cryptogene) into one auditable, self-hostable package — and fixes the bugs
> that made them unsafe to run with real money.

## Why this exists (audit-driven)

An independent audit of the 4 source repos found the same pattern everywhere:
**real ideas, but fatal defects that lose money or crash.** OmniTrader is the
consolidated, corrected successor.

| Defect (in original repos) | Status in OmniTrader |
|---|---|
| Backtest lookahead (fill at signal close) | ✅ fills at **next bar open** |
| Wrong equity for short positions | ✅ correct long/short accounting |
| Sharpe computed as a t-statistic | ✅ annualized Sharpe/Sortino on equity curve |
| Risk manager never called in loop → kill-switch dead | ✅ `update_equity`/`record_pnl` called every bar; halts block new entries |
| Close orders sent `quantity=0` (never close) | ✅ real position qty used |
| Float `round()` LOT_SIZE drift | ✅ `Decimal`-based `round_step_size` |
| No license / no tests / no CI | ✅ MIT + pytest + GitHub Actions |

## Features

- **Zero-lookahead event backtester** with fees, slippage, and realistic
  intrabar stop/take fills.
- **Correct metrics**: total return, CAGR, annualized Sharpe & Sortino,
  max drawdown, win rate, profit factor (all in USD).
- **Live-wired risk manager**: position sizing by risk, stop/take, **daily-loss
  + max-drawdown kill switches that actually trigger and flatten the book.**
- **Strategy interface** + 3 reference strategies: `momentum` (Donchian+ADX),
  `mean_reversion` (RSI, optional Bollinger confirmation), `grid`.
- **Decimal-safe** quantity rounding for live Binance execution.
- **Safe live adapter**: refuses to trade live without keys + explicit intent.
- CLI, reproducible sample data, MIT license, Docker, CI.

## Quick start

```bash
pip install -e .
# backtest on a generated 2000-bar sample (or pass --data real.json)
omnitrader --strategy momentum --symbol BTCUSDT --capital 10000
omnitrader --strategy mean_reversion --symbol ETHUSDT --params '{"oversold":25}'
omnitrader --strategy grid --export equity.json
```

Run the test suite:

```bash
pytest -q
```

## Roadmap (the "superior product" path)

1. **Backtest core** ✅ — verifiable, zero-lookahead, fees/slippage.
2. **CEX execution** ✅ — `omni_trader.exchange` (paper + Binance via ccxt +
   `LiveTrader` loop). *Next: unified risk budget across the original L1/L2/L3 tiers.*
3. **On-chain execution** ✅ (simulation-first) — `JupiterClient` (Solana) +
   `PancakeSwapClient` (BSC). *Next: live broadcast wiring + MEV protection.*
4. **Dashboard** ✅ — Streamlit app reusing this engine (fixes nexus-terminal's
   Sharpe / lookahead / fake-ATR bugs).
5. **Strategy evolution** — real genetic/search over params with out-of-sample
   validation (fix cryptogene's "theater" evolution). *Next up.*
6. **Walk-forward / multi-asset portfolio** — expand beyond single-symbol.

## Disclaimer

For research and education. Crypto trading carries risk of total loss. Never run
live without testnet validation, hard position limits, and an understanding of
the code. **Not financial advice.**

---

*MIT License — free, auditable, self-hostable. No monthly fees, no black box.*
