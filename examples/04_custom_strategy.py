"""示例 4：写你自己的策略。

只要继承 Strategy 并实现 on_bar(ctx) -> Signal 即可。
引擎保证：ctx.bars 只到当前 bar，信号在**下一根 bar 的开盘**成交（无前视）。

用法：
    python examples/04_custom_strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni_trader.backtest.engine import BacktestEngine  # noqa: E402
from omni_trader.data import DataFeed  # noqa: E402
from omni_trader.risk import RiskConfig  # noqa: E402
from omni_trader.strategies import Action, Context, Signal, Strategy  # noqa: E402


class DualMA(Strategy):
    """双均线交叉：快线上穿慢线做多，下穿做空。"""

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.fast = int(self.params.get("fast", 10))
        self.slow = int(self.params.get("slow", 30))

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        if i < self.slow + 1:
            return Signal(Action.HOLD)

        closes: List[float] = [b.close for b in ctx.bars]
        ma_f = self.ema(closes, self.fast)
        ma_s = self.ema(closes, self.slow)

        prev_above = ma_f[i - 1] > ma_s[i - 1]
        now_above = ma_f[i] > ma_s[i]
        pos = ctx.position

        if pos is None:
            if now_above and not prev_above:
                return Signal(Action.BUY, side="long", reason="golden cross")
            if (not now_above) and prev_above:
                return Signal(Action.SELL, side="short", reason="death cross")
            return Signal(Action.HOLD)

        # 反向交叉 -> 平仓
        if pos["side"] == "long" and not now_above:
            return Signal(Action.CLOSE, reason="cross down")
        if pos["side"] == "short" and now_above:
            return Signal(Action.CLOSE, reason="cross up")
        return Signal(Action.HOLD)


if __name__ == "__main__":
    feed = DataFeed.from_json(Path(__file__).resolve().parents[1] / "samples/BTCUSDT_1h.json")

    for fast, slow in ((5, 20), (10, 30), (20, 60)):
        strat = DualMA({"fast": fast, "slow": slow})
        res = BacktestEngine(feed, strat, RiskConfig(), initial_capital=10_000).run()
        print(f"  DualMA({fast:>2},{slow:>2})  return={res.total_return_pct:+7.2f}%  "
              f"sharpe={res.sharpe:5.2f}  maxdd={res.max_drawdown_pct:5.2f}%  "
              f"trades={res.num_trades:3d}")
