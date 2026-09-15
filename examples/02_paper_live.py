"""示例 2：模拟盘（Paper Trading）—— 用真实 K 线逐根喂给实盘引擎。

这一步的意义：跑的是**同一套**策略 + 风控 + 下单接口，跟真钱完全一样的
代码路径，唯一区别是 adapter 是虚拟交易所（不联网、不碰钱）。

用法：
    python examples/02_paper_live.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni_trader.data import DataFeed  # noqa: E402
from omni_trader.exchange import LiveTrader, PaperAdapter  # noqa: E402
from omni_trader.risk import RiskConfig  # noqa: E402
from omni_trader.strategies import MomentumBreakout  # noqa: E402


def main() -> None:
    feed = DataFeed.from_json(Path(__file__).resolve().parents[1] / "samples/BTCUSDT_1h.json")

    # 虚拟交易所：初始 10,000 USDT，手续费 0.1%
    adapter = PaperAdapter(starting_cash=10_000.0, asset="USDT")

    cfg = RiskConfig(
        risk_per_trade=0.01,
        max_position_pct=0.30,
        daily_loss_limit=0.05,
        max_drawdown_limit=0.20,
    )

    trader = LiveTrader(
        strategy=MomentumBreakout({}),
        adapter=adapter,
        risk_config=cfg,
        initial_capital=10_000.0,
        dry_run=True,          # True = 只记录不下真单（adapter 本来也是模拟的）
        symbol="BTCUSDT",
    )

    events: dict[str, int] = {}
    for bar in feed.bars[:500]:
        ev = trader.step(bar)
        events[ev["event"]] = events.get(ev["event"], 0) + 1
        if ev["event"] == "halted":
            print(f"  [熔断触发] bar ts={bar.ts} reason={ev['reason']}")
            break
        if ev["event"] in ("open", "close"):
            print(f"  [{ev['event']:<5}] ts={bar.ts} px={bar.close:.2f} {ev}")

    print("\n=== 事件统计 ===")
    for k, v in sorted(events.items(), key=lambda x: -x[1]):
        print(f"  {k:<14} {v}")

    s = trader.summary()
    print("\n=== 模拟盘结果 ===")
    print(f"  final equity     {s['equity']:,.2f}")
    print(f"  return           {(s['equity'] / 10_000 - 1) * 100:+.2f}%")
    print(f"  orders           {s['trades']}  (opens={s['open_trades']})")
    print(f"  risk state       {s['risk']}")


if __name__ == "__main__":
    main()
