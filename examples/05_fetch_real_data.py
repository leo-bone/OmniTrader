"""示例 5：拉真实行情数据（无需 API key）。

数据源优先级：
  1) ccxt（若已安装）— 支持任意交易所
  2) Binance 公开 REST /api/v3/klines — 纯标准库 urllib，零依赖

产出：标准 DataFeed JSON，可直接喂给 --data 或 DataFeed.from_json()。

用法：
    python examples/05_fetch_real_data.py --symbol BTCUSDT --timeframe 1h --limit 2000
    python examples/05_fetch_real_data.py --symbol ETHUSDT --timeframe 4h --out data/eth.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni_trader.data import Bar, DataFeed  # noqa: E402

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def fetch_via_ccxt(symbol: str, timeframe: str, limit: int) -> DataFeed | None:
    try:
        import ccxt  # type: ignore
    except ImportError:
        return None
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    bars = [Bar(ts=r[0] // 1000, open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in raw]
    return DataFeed(symbol, timeframe, bars)


def fetch_via_rest(symbol: str, timeframe: str, limit: int) -> DataFeed:
    """Binance 公开行情接口——不需要 key。单次上限 1000 根，自动分页。"""
    bars: list[Bar] = []
    end = None
    while len(bars) < limit:
        batch = min(1000, limit - len(bars))
        url = f"{BINANCE_KLINES}?symbol={symbol}&interval={timeframe}&limit={batch}"
        if end:
            url += f"&endTime={end}"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "omnitrader"}), timeout=30
        ) as r:
            raw = json.loads(r.read().decode())
        if not raw:
            break
        for k in raw:
            bars.append(Bar(ts=int(k[0]) // 1000, open=float(k[1]), high=float(k[2]),
                            low=float(k[3]), close=float(k[4]), volume=float(k[5])))
        end = raw[0][0] - 1  # 往前翻页
    bars.sort(key=lambda b: b.ts)
    return DataFeed(symbol, timeframe, bars)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    feed = None
    src = ""
    try:
        feed = fetch_via_ccxt(args.symbol, args.timeframe, args.limit)
        src = "ccxt"
    except Exception as e:
        print(f"[ccxt 失败] {type(e).__name__}: {e}")
    if not feed or not feed.bars:
        try:
            feed = fetch_via_rest(args.symbol, args.timeframe, args.limit)
            src = "binance-rest"
        except Exception as e:
            print(f"[REST 失败] {type(e).__name__}: {e}")
            print("\n取不到数据通常有三种原因：")
            print("  1) 当前网络到 Binance 不通（部分地区/公司网络需代理）；")
            print("  2) 沙箱环境限制了外网；")
            print("  3) symbol 拼写错（必须是 Binance 交易对，如 BTCUSDT / ETHUSDT）。")
            print("先跑内置的离线样例：python -m omni_trader.cli --strategy momentum")
            return

    if not feed.bars:
        print("没有取到数据：请检查网络（Binance API 在部分地区需要代理）。")
        return

    out = args.out or f"data/{args.symbol}_{args.timeframe}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    feed.to_json(out)
    first = datetime.utcfromtimestamp(feed.bars[0].ts)
    last = datetime.utcfromtimestamp(feed.bars[-1].ts)
    print(f"[{src}] {len(feed)} bars  {first} -> {last}")
    print(f"saved -> {out}")
    print(f"\n下一步：\n  python -m omni_trader.cli --strategy momentum --data {out}")


if __name__ == "__main__":
    main()
