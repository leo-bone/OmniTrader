"""Zero-lookahead event backtester.

Fixes carried over from the audit of the original repos:
  * NO lookahead: a signal decided at bar `i` fills at bar `i+1` OPEN.
  * Correct equity for BOTH long and short (the original quant-trading used
    `capital + abs(pos)*close`, which inflated short equity).
  * Annualized Sharpe/Sortino on the equity curve (not a t-statistic on
    per-trade returns like the original nexus-terminal).
  * Kill switches (daily-loss + max-drawdown) are evaluated EVERY bar and
    block new entries + flatten the book (the original risk managers were
    never called in the loop).
  * Fees + slippage applied on every fill (the original backtest was frictionless).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

from ..data import Bar, DataFeed
from ..risk import RiskConfig, RiskManager
from ..strategies.base import Action, Context, Signal, Strategy


@dataclass
class Trade:
    side: str
    entry: float
    exit: float
    qty: float
    pnl: float          # net USD pnl including fees
    entry_ts: int
    exit_ts: int
    reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    num_trades: int
    periods_per_year: float
    halted: bool = False
    halt_reason: Optional[str] = None
    equity_curve: List[float] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["trades"] = [
            {k: v for k, v in t.__dict__.items()} for t in self.trades
        ]
        return d


class BacktestEngine:
    def __init__(
        self,
        feed: DataFeed,
        strategy: Strategy,
        risk_config: Optional[RiskConfig] = None,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.001,       # 0.1% per fill (taker)
        slippage: float = 0.0005,      # 0.05% fill slippage
        periods_per_year: Optional[float] = None,
    ):
        self.feed = feed
        self.strategy = strategy
        self.risk = RiskManager(risk_config)
        self.initial_capital = float(initial_capital)
        self.fee = fee_rate
        self.slip = slippage
        self._ppy = periods_per_year

    # ---- equity accounting (correct for long & short) ----
    @staticmethod
    def _equity(cash: float, pos: Optional[dict], close: float) -> float:
        if pos is None:
            return cash
        if pos["side"] == "long":
            return cash + pos["qty"] * close
        return cash - pos["qty"] * close  # short

    def _periods_per_year(self) -> float:
        if self._ppy is not None:
            return self._ppy
        bars = self.feed.bars
        if len(bars) < 2:
            return 8760.0  # default 1h crypto
        diffs = [bars[i].ts - bars[i - 1].ts for i in range(1, min(len(bars), 50))]
        med = statistics.median(diffs) or 3600
        return 365.0 * 86400.0 / med

    # ---- order execution ----
    def _open(self, cash, pos, side, qty, fill_price, ts) -> tuple:
        # buy fills at (1+slip), sell fills at (1-slip)
        if side == "long":
            px = fill_price * (1 + self.slip)
            notional = qty * px
            fee = notional * self.fee
            cash -= (notional + fee)
        else:  # short
            px = fill_price * (1 - self.slip)
            notional = qty * px
            fee = notional * self.fee
            cash += (notional - fee)
        new_pos = {"side": side, "qty": qty, "entry": px, "stop": 0.0,
                   "take": 0.0, "entry_ts": ts}
        return cash, new_pos

    def _close(self, cash, pos, fill_price, ts, reason="") -> tuple:
        qty = pos["qty"]
        side = pos["side"]
        if side == "long":
            px = fill_price * (1 - self.slip)
            notional = qty * px
            fee = notional * self.fee
            cash += (notional - fee)
            net_exit = notional - fee
            net_entry = qty * pos["entry"] * (1 + self.fee)
            pnl = net_exit - net_entry
        else:  # short cover
            px = fill_price * (1 + self.slip)
            notional = qty * px
            fee = notional * self.fee
            cash -= (notional + fee)
            net_exit = notional + fee
            net_entry = qty * pos["entry"] * (1 - self.fee)
            pnl = net_entry - net_exit
        trade = Trade(side=side, entry=pos["entry"], exit=px, qty=qty,
                     pnl=pnl, entry_ts=pos["entry_ts"], exit_ts=ts, reason=reason)
        return cash, None, trade

    def _stops(self, entry, side, signal: Signal):
        sp = signal.stop_pct if signal.stop_pct is not None else self.risk.cfg.default_stop_pct
        tp = signal.take_pct if signal.take_pct is not None else self.risk.cfg.default_take_pct
        stop = entry * (1 - sp) if side == "long" else entry * (1 + sp)
        take = entry * (1 + tp) if side == "long" else entry * (1 - tp)
        return stop, take

    # ---- main loop ----
    def run(self) -> BacktestResult:
        bars = self.feed.bars
        n = len(bars)
        cash = self.initial_capital
        pos: Optional[dict] = None
        pending: Optional[Signal] = None
        equity_curve: List[float] = []
        trades: List[Trade] = []
        ppy = self._periods_per_year()

        for i in range(n):
            bar = bars[i]

            # (1) execute signal decided at PREVIOUS bar, at THIS bar's open
            if pending is not None and pending.action != Action.HOLD:
                if pending.action == Action.CLOSE:
                    if pos is not None:
                        cash, pos, trade = self._close(cash, pos, bar.open, bar.ts, pending.reason)
                        trades.append(trade)
                        self.risk.record_realized_pnl(trade.pnl)
                elif pending.action in (Action.BUY, Action.SELL) and self.risk.can_trade():
                    side = pending.side
                    siz = self.risk.size_position(bar.open, side=side)
                    qty = pending.qty if pending.qty else siz.qty
                    if qty > 0:
                        cash, pos = self._open(cash, pos, side, qty, bar.open, bar.ts)
                        stop, take = self._stops(bar.open, side, pending)
                        pos["stop"], pos["take"] = stop, take
                pending = None

            # (2) intrabar stop / take check on current position (high/low)
            if pos is not None:
                if pos["side"] == "long":
                    if bar.low <= pos["stop"]:
                        cash, pos, trade = self._close(cash, pos, pos["stop"], bar.ts, "stop-loss")
                        trades.append(trade); self.risk.record_realized_pnl(trade.pnl)
                    elif bar.high >= pos["take"]:
                        cash, pos, trade = self._close(cash, pos, pos["take"], bar.ts, "take-profit")
                        trades.append(trade); self.risk.record_realized_pnl(trade.pnl)
                else:  # short
                    if bar.high >= pos["stop"]:
                        cash, pos, trade = self._close(cash, pos, pos["stop"], bar.ts, "stop-loss")
                        trades.append(trade); self.risk.record_realized_pnl(trade.pnl)
                    elif bar.low <= pos["take"]:
                        cash, pos, trade = self._close(cash, pos, pos["take"], bar.ts, "take-profit")
                        trades.append(trade); self.risk.record_realized_pnl(trade.pnl)

            # (3) mark to market + risk wiring
            equity = self._equity(cash, pos, bar.close)
            self.risk.update_equity(equity, bar.ts)
            equity_curve.append(equity)

            # (4) halt handling: flatten and stop new entries
            if self.risk.halted:
                if pos is not None:
                    pending = Signal(Action.CLOSE, reason="risk halt")
                else:
                    pending = None
                continue

            # (5) decide next action (no future data; executes at i+1 open)
            ctx = Context(bars=bars, index=i, position=pos, equity=equity,
                         params=self.strategy.params)
            pending = self.strategy.on_bar(ctx)

        # close any residual position at last close (mark-to-market final)
        if pos is not None:
            cash, pos, trade = self._close(cash, pos, bars[-1].close, bars[-1].ts, "eod")
            trades.append(trade)
            self.risk.record_realized_pnl(trade.pnl)

        return self._summarize(equity_curve, trades, ppy)

    # ---- metrics ----
    def _summarize(self, equity_curve, trades, ppy) -> BacktestResult:
        eq0 = self.initial_capital
        eq_end = equity_curve[-1] if equity_curve else eq0
        total_ret = eq_end / eq0 - 1.0

        rets = []
        for t in range(1, len(equity_curve)):
            prev = equity_curve[t - 1]
            if prev > 0:
                rets.append(equity_curve[t] / prev - 1.0)

        mean_r = statistics.mean(rets) if rets else 0.0
        std_r = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        sharpe = (mean_r / std_r) * math.sqrt(ppy) if std_r > 0 else 0.0
        downside = [r for r in rets if r < 0]
        dstd = statistics.pstdev(downside) if len(downside) > 1 else 0.0
        sortino = (mean_r / dstd) * math.sqrt(ppy) if dstd > 0 else 0.0

        peak = eq0
        max_dd = 0.0
        for e in equity_curve:
            peak = max(peak, e)
            if peak > 0:
                max_dd = max(max_dd, 1 - e / peak)

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        years = (len(equity_curve) / ppy) if ppy else 0.0
        cagr = ((eq_end / eq0) ** (1 / years) - 1.0) * 100 if years > 0 and eq0 > 0 else 0.0

        return BacktestResult(
            symbol=self.feed.symbol, timeframe=self.feed.timeframe,
            initial_capital=eq0, final_equity=eq_end,
            total_return_pct=total_ret * 100, cagr_pct=cagr,
            sharpe=sharpe, sortino=sortino, max_drawdown_pct=max_dd * 100,
            win_rate_pct=win_rate, profit_factor=profit_factor,
            num_trades=len(trades), periods_per_year=ppy,
            halted=self.risk.halted, halt_reason=self.risk.halt_reason,
            equity_curve=equity_curve, trades=trades,
        )
