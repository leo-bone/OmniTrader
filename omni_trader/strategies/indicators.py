"""O(1) streaming indicators.

Why this module exists
----------------------
Every strategy in the original repos recomputed its **entire** indicator
history on **every** bar::

    closes = [b.close for b in ctx.bars]      # O(n) list copy, every bar
    rsi    = self.rsi_wilder(closes, 14)      # O(n) again, every bar

That is O(n^2) for an n-bar backtest. It is barely noticeable for one CLI run
over 2 000 bars, but the evolution engine evaluates
``population x generations x (IS + OOS)`` backtests — thousands of them — so
the quadratic term was the difference between "finishes tonight" and "finishes
never".

These classes keep the **same numbers** as the batch helpers in
``strategies.base`` while updating in O(1) amortized time per bar, so a full
backtest becomes O(n).

One deliberate behavioural fix: the batch ``rsi_wilder`` used
``100 - 100/(1 + avg_gain/avg_loss)`` for the seeded value but
``100 - 100/(1 + avg_loss/avg_gain)`` inside the smoothing loop — i.e. every
bar after the seed window was **inverted around 50**. That silently flipped
the mean-reversion strategy's long/short logic. The streaming RSI below uses
the standard definition throughout (``RS = avg_gain / avg_loss``), and
``strategies.base.rsi_wilder`` has been fixed to match.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Tuple

NAN = float("nan")


class RollingExtremes:
    """Rolling max/min over the last ``window`` pushed values.

    Uses a monotonic deque per direction: `push` is O(1) amortized, `max`/`min`
    are O(1). Emulates the ``highs[i-window:i]`` slice used by the original
    batch strategies — the window covers values pushed **before** the current
    bar, never the current bar itself.
    """

    __slots__ = ("window", "_maxq", "_minq", "_n")

    def __init__(self, window: int):
        if window <= 0:
            raise ValueError("RollingExtremes window must be >= 1")
        self.window = int(window)
        self._maxq: Deque[Tuple[int, float]] = deque()
        self._minq: Deque[Tuple[int, float]] = deque()
        self._n = 0

    def push(self, value: float) -> None:
        i = self._n
        self._n += 1
        q = self._maxq
        while q and q[-1][1] <= value:
            q.pop()
        q.append((i, value))
        q = self._minq
        while q and q[-1][1] >= value:
            q.pop()
        q.append((i, value))

    @property
    def ready(self) -> bool:
        """True once at least `window` values have been pushed."""
        return self._n >= self.window

    def _prune(self, q: Deque[Tuple[int, float]]) -> None:
        limit = self._n - self.window
        while q and q[0][0] < limit:
            q.popleft()

    def max(self) -> float:
        if not self.ready:
            return NAN
        self._prune(self._maxq)
        return self._maxq[0][1] if self._maxq else NAN

    def min(self) -> float:
        if not self.ready:
            return NAN
        self._prune(self._minq)
        return self._minq[0][1] if self._minq else NAN

    def reset(self) -> None:
        self._maxq.clear()
        self._minq.clear()
        self._n = 0


class RollingWindow:
    """Fixed-size FIFO window with O(1) push and O(window) stats.

    Mirrors the original ``closes[i-period:i+1]`` window (size ``period + 1``,
    inclusive of the current bar).
    """

    __slots__ = ("size", "_buf")

    def __init__(self, size: int):
        if size <= 0:
            raise ValueError("RollingWindow size must be >= 1")
        self.size = int(size)
        self._buf: Deque[float] = deque(maxlen=self.size)

    def push(self, value: float) -> None:
        self._buf.append(value)

    @property
    def ready(self) -> bool:
        return len(self._buf) >= self.size

    def mean(self) -> float:
        if not self._buf:
            return NAN
        return sum(self._buf) / len(self._buf)

    def std(self) -> float:
        """Population standard deviation (same convention as the old code)."""
        n = len(self._buf)
        if n == 0:
            return NAN
        m = sum(self._buf) / n
        return (sum((x - m) ** 2 for x in self._buf) / n) ** 0.5

    def reset(self) -> None:
        self._buf.clear()


class EMA:
    """Exponential moving average, O(1) per bar, available from bar 0."""

    __slots__ = ("period", "k", "_last", "_n")

    def __init__(self, period: int):
        if period <= 0:
            raise ValueError("EMA period must be >= 1")
        self.period = int(period)
        self.k = 2.0 / (self.period + 1)
        self._last = NAN
        self._n = 0

    def update(self, value: float) -> float:
        if self._n == 0:
            self._last = value
        else:
            self._last = value * self.k + self._last * (1 - self.k)
        self._n += 1
        return self._last

    @property
    def value(self) -> float:
        return self._last

    def reset(self) -> None:
        self._last = NAN
        self._n = 0


class RSI:
    """Wilder's RSI, O(1) per bar.

    Numerically identical to ``Strategy.rsi_wilder`` (with that helper's
    inverted-smoothing bug fixed) but it never touches the whole series.
    Returns NaN until `period` price changes have been seen — exactly like the
    batch version's leading NaNs.
    """

    __slots__ = ("period", "_prev", "_seed_g", "_seed_l", "_avg_g", "_avg_l", "value")

    def __init__(self, period: int = 14):
        if period <= 0:
            raise ValueError("RSI period must be >= 1")
        self.period = int(period)
        self.reset()

    def reset(self) -> None:
        self._prev: Optional[float] = None
        self._seed_g: List[float] = []
        self._seed_l: List[float] = []
        self._avg_g: Optional[float] = None
        self._avg_l: Optional[float] = None
        self.value = NAN

    def update(self, close: float) -> float:
        if self._prev is None:
            self._prev = close
            self.value = NAN
            return self.value

        ch = close - self._prev
        self._prev = close
        gain = ch if ch > 0 else 0.0
        loss = -ch if ch < 0 else 0.0

        p = self.period
        if self._avg_g is None:
            self._seed_g.append(gain)
            self._seed_l.append(loss)
            if len(self._seed_g) < p:
                self.value = NAN
                return NAN
            self._avg_g = sum(self._seed_g) / p
            self._avg_l = sum(self._seed_l) / p
            self._seed_g = []
            self._seed_l = []
        else:
            self._avg_g = (self._avg_g * (p - 1) + gain) / p
            self._avg_l = (self._avg_l * (p - 1) + loss) / p

        if self._avg_l == 0:
            self.value = 100.0
        else:
            self.value = 100.0 - 100.0 / (1.0 + self._avg_g / self._avg_l)
        return self.value


class ADX:
    """Wilder's ADX (with internal +DI/-DI), O(1) per bar.

    Reproduces ``MomentumBreakout._adx`` exactly: the first value lands at bar
    index ``period`` (built from the first `period` TR/DM samples); afterwards
    ATR/+DI/-DI/DX are Wilder-smoothed recursively.
    """

    __slots__ = ("period", "_ph", "_pl", "_pc", "_seed_tr", "_seed_pdm",
                 "_seed_mdm", "_atr", "_pdi", "_mdi", "value", "_n")

    def __init__(self, period: int = 14):
        if period <= 0:
            raise ValueError("ADX period must be >= 1")
        self.period = int(period)
        self.reset()

    def reset(self) -> None:
        self._ph: Optional[float] = None
        self._pl: Optional[float] = None
        self._pc: Optional[float] = None
        self._seed_tr: List[float] = []
        self._seed_pdm: List[float] = []
        self._seed_mdm: List[float] = []
        self._atr = 0.0
        self._pdi = 0.0
        self._mdi = 0.0
        self.value = NAN
        self._n = 0  # 0 = collecting seed window, 1 = seeded

    def update(self, high: float, low: float, close: float) -> float:
        ph, pl, pc = self._ph, self._pl, self._pc
        self._ph, self._pl, self._pc = high, low, close
        if ph is None:
            self.value = NAN
            return NAN

        up = high - ph
        dn = pl - low
        plus_dm = max(up, 0.0) if up > dn else 0.0
        minus_dm = max(dn, 0.0) if dn > up else 0.0
        tr = max(high - low, abs(high - pc), abs(low - pc))

        p = self.period
        if self._n == 0:
            self._seed_tr.append(tr)
            self._seed_pdm.append(plus_dm)
            self._seed_mdm.append(minus_dm)
            if len(self._seed_tr) < p:
                self.value = NAN
                return NAN
            self._atr = sum(self._seed_tr) / p
            self._pdi = sum(self._seed_pdm) / p
            self._mdi = sum(self._seed_mdm) / p
            self._n = 1
            # first ADX value is DX itself (not yet smoothed) — same as batch
            pdi_r = 100.0 * self._pdi / self._atr if self._atr else 0.0
            mdi_r = 100.0 * self._mdi / self._atr if self._atr else 0.0
            self.value = (100.0 * abs(pdi_r - mdi_r) / (pdi_r + mdi_r)
                          if (pdi_r + mdi_r) else 0.0)
            return self.value

        self._atr = (self._atr * (p - 1) + tr) / p
        self._pdi = (self._pdi * (p - 1) + plus_dm) / p
        self._mdi = (self._mdi * (p - 1) + minus_dm) / p
        pdi_r = 100.0 * self._pdi / self._atr if self._atr else 0.0
        mdi_r = 100.0 * self._mdi / self._atr if self._atr else 0.0
        dx = 100.0 * abs(pdi_r - mdi_r) / (pdi_r + mdi_r) if (pdi_r + mdi_r) else 0.0
        self.value = (self.value * (p - 1) + dx) / p
        return self.value
