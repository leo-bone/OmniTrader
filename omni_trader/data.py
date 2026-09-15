"""Market data structures and feeds."""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


@dataclass
class Bar:
    """A single OHLCV candle. `ts` is an epoch-seconds timestamp."""
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def datetime(self) -> datetime:
        return datetime.utcfromtimestamp(self.ts)


@dataclass
class DataFeed:
    """An in-memory OHLCV series plus helpers to load / generate it."""
    symbol: str
    timeframe: str
    bars: List[Bar] = field(default_factory=list)

    # ---------- loaders ----------
    @classmethod
    def from_json(cls, path: str | Path, symbol: str = "", timeframe: str = "1h") -> "DataFeed":
        raw = json.loads(Path(path).read_text())
        # accept either a list of bars or {"symbol","timeframe","bars":[...]}
        if isinstance(raw, dict):
            symbol = symbol or raw.get("symbol", "")
            timeframe = raw.get("timeframe", timeframe)
            raw = raw.get("bars", [])
        bars = [
            Bar(ts=int(b["ts"]), open=float(b["open"]), high=float(b["high"]),
                low=float(b["low"]), close=float(b["close"]), volume=float(b.get("volume", 0.0)))
            for b in raw
        ]
        return cls(symbol=symbol, timeframe=timeframe, bars=bars)

    def to_json(self, path: str | Path) -> None:
        payload = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": [
                {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": b.volume}
                for b in self.bars
            ],
        }
        Path(path).write_text(json.dumps(payload, indent=2))

    # ---------- generator (seeded, for out-of-the-box backtests) ----------
    @classmethod
    def generate_sample(
        cls,
        symbol: str = "BTCUSDT",
        n: int = 2000,
        start_price: float = 30000.0,
        vol: float = 0.012,
        drift: float = 0.0002,
        seed: int = 42,
        timeframe: str = "1h",
    ) -> "DataFeed":
        """Random-walk OHLCV with realistic intrabar high/low.

        Seeded so backtests are reproducible (unlike the synthetic fallback
        in the original nexus-terminal, which was unflagged noise).
        """
        rng = random.Random(seed)
        bars: List[Bar] = []
        price = start_price
        ts = int(datetime(2024, 1, 1).timestamp())
        step = 3600 if timeframe == "1h" else 86400
        for i in range(n):
            ret = drift + vol * rng.gauss(0, 1)
            open_ = price
            close = max(1.0, open_ * (1 + ret))
            # intrabar extreme: at least as far as open/close, plus noise
            wick = abs(ret) * open_ + open_ * vol * 0.5 * abs(rng.gauss(0, 1))
            high = max(open_, close) + wick
            low = min(open_, close) - wick
            low = max(0.5, low)
            volume = rng.uniform(50, 500) * (1 + abs(ret) * 20)
            bars.append(Bar(ts=ts, open=round(open_, 2), high=round(high, 2),
                            low=round(low, 2), close=round(close, 2), volume=round(volume, 2)))
            price = close
            ts += step
        return cls(symbol=symbol, timeframe=timeframe, bars=bars)

    @classmethod
    def generate_regimes(
        cls,
        symbol: str = "SYNTHUSDT",
        n: int = 3000,
        blocks: int = 8,
        start_price: float = 30000.0,
        vol: float = 0.008,
        seed: int = 7,
        timeframe: str = "1h",
    ) -> "DataFeed":
        """Synthetic market that alternates between real regimes.

        A plain random walk has NO exploitable structure — searching parameters
        against one just measures luck, and every "profit" the evolution finds
        is noise it will give back immediately. Real markets rotate between
        trending, chopping and mean-reverting stretches, and those regimes
        favour *different* strategies. This generator produces that rotation, so
        the evolution has something genuine to discover and the multi-species
        population has a reason to compete.

        Regime cycle: trending -> chopping -> mean-reverting -> trending ...
        (direction alternates on each trend block)
        """
        rng = random.Random(seed)
        kinds = ["trend", "chop", "revert", "trend", "revert", "chop"]
        step = 3600 if timeframe == "1h" else 86400
        ts = int(datetime(2024, 1, 1).timestamp())
        per = max(20, n // max(1, blocks))
        price = start_price
        bars: List[Bar] = []
        trend_sign = 1
        i = 0
        while len(bars) < n:
            kind = kinds[i % len(kinds)]
            run = per if len(bars) + per <= n else n - len(bars)
            if kind == "trend":
                trend_sign *= -1 if (i % (2 * len(kinds))) == len(kinds) else 1
                drift = 0.0016 * trend_sign
                anchor_mu = price
                phase = 0.0
            elif kind == "chop":
                anchor_mu = price
                phase = rng.uniform(0, 6.28)
                amp = price * vol * 9
            else:  # "revert": Ornstein-Uhlenbeck pull back to the anchor
                anchor_mu = price
                theta = 0.06
            for k in range(run):
                noise = rng.gauss(0, 1) * vol
                if kind == "trend":
                    ret = drift + noise
                elif kind == "chop":
                    target = anchor_mu + amp * math.sin(phase + k / 7.0)
                    ret = (target - price) / max(price, 1e-9) + noise * 0.35
                else:
                    ret = theta * (anchor_mu - price) / max(price, 1e-9) + noise
                open_ = price
                close = max(1.0, open_ * (1 + ret))
                wick = abs(ret) * open_ + open_ * vol * 0.5 * abs(rng.gauss(0, 1))
                high = max(open_, close) + wick
                low = max(0.5, min(open_, close) - wick)
                volume = rng.uniform(50, 500) * (1 + abs(ret) * 20)
                bars.append(Bar(ts=ts, open=round(open_, 2), high=round(high, 2),
                                low=round(low, 2), close=round(close, 2),
                                volume=round(volume, 2)))
                price = close
                ts += step
            i += 1
        return cls(symbol=symbol, timeframe=timeframe, bars=bars[:n])

    # ---------- convenience ----------
    def closes(self) -> List[float]:
        return [b.close for b in self.bars]

    def highs(self) -> List[float]:
        return [b.high for b in self.bars]

    def lows(self) -> List[float]:
        return [b.low for b in self.bars]

    def __len__(self) -> int:
        return len(self.bars)
