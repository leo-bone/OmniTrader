"""OmniTrader dashboard (Streamlit).

Rewrites the old single-file ``nexus-terminal`` HTML into a clean, modular
Python app that reuses the SAME audited backtest engine + risk module, so the
numbers you see are exactly what the framework computes (no duplicate,
lookahead-prone JS math).

Run:
    pip install streamlit plotly
    streamlit run dashboard/app.py

Sections:
  * Sidebar  — strategy, capital, data source (bundled sample / upload JSON /
               live Binance pull), strategy params.
  * Metrics  — Sharpe, Sortino, MaxDD, Profit Factor, Win rate, CAGR, Return.
  * Equity + Drawdown chart (Plotly).
  * Trades table.
  * Optional live ticker (needs ``ccxt``; shows last price + recent OHLCV).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from omni_trader.data import DataFeed, Bar
from omni_trader.backtest.engine import BacktestEngine
from omni_trader.risk import RiskConfig
from omni_trader.strategies import Momentum, MeanReversion, GridBot

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "BTCUSDT_1h.json"

STRATEGY_MAP = {
    "momentum": Momentum,
    "mean_reversion": MeanReversion,
    "grid": GridBot,
}


@st.cache_data
def load_sample() -> DataFeed:
    return DataFeed.from_json(str(SAMPLE))


def parse_params(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        st.error(f"Params JSON 解析失败: {e}")
        return {}


def build_feed(uploaded, live_symbol, live_tf, live_limit) -> DataFeed | None:
    if uploaded is not None:
        try:
            return DataFeed.from_json(uploaded)
        except Exception as e:  # noqa
            st.error(f"上传文件解析失败: {e}")
            return None
    if live_symbol:
        try:
            from omni_trader.exchange import BinanceAdapter  # lazy (needs ccxt)

            bars = BinanceAdapter().fetch_ohlcv(live_symbol, live_tf, live_limit)
            return DataFeed(live_symbol, live_tf, bars)
        except Exception as e:  # noqa
            st.error(f"实盘行情拉取失败（需 ccxt）: {e}")
            return None
    return load_sample()


def main() -> None:
    st.set_page_config(page_title="OmniTrader", layout="wide")
    st.title("OmniTrader · 量化回测与风控看板")
    st.caption("统一框架：零前视回测 · 多/空正确核算 · 实接熔断 · Decimal 精度")

    # ---- sidebar ----
    with st.sidebar:
        st.header("配置")
        strategy_name = st.selectbox("策略", list(STRATEGY_MAP), index=0)
        capital = st.number_input("初始资金 (USDT)", min_value=100.0, value=10_000.0, step=100.0)
        fee = st.number_input("手续费 (每笔)", min_value=0.0, value=0.001, step=0.0001, format="%.4f")
        slip = st.number_input("滑点", min_value=0.0, value=0.0005, step=0.0001, format="%.4f")

        st.subheader("数据来源")
        src = st.radio("来源", ["内置样本", "上传 JSON", "实盘 (Binance)"])
        uploaded = None
        live_symbol = ""
        if src == "上传 JSON":
            uploaded = st.file_uploader("上传 OHLCV JSON", type=["json"])
        elif src == "实盘 (Binance)":
            live_symbol = st.text_input("交易对", "BTCUSDT").upper()
            live_tf = st.selectbox("周期", ["1m", "5m", "15m", "1h", "4h", "1d"], index=3)
            live_limit = st.slider("K线数量", 100, 1000, 500)

        st.subheader("策略参数 (JSON，可空)")
        default_params = json.dumps(STRATEGY_MAP[strategy_name]().params, ensure_ascii=False, indent=0)
        params_raw = st.text_area("params", value=default_params, height=120)

        run = st.button("运行回测", type="primary")

    if not run:
        st.info("在左侧配置后点击「运行回测」。默认使用内置 BTCUSDT 1h 样本数据。")
        return

    feed = build_feed(uploaded, live_symbol if src == "实盘 (Binance)" else "",
                     live_tf if src == "实盘 (Binance)" else "1h",
                     live_limit if src == "实盘 (Binance)" else 500)
    if feed is None:
        return

    params = parse_params(params_raw)
    strategy = STRATEGY_MAP[strategy_name](params)
    risk = RiskConfig()

    with st.spinner("回测中..."):
        result = BacktestEngine(
            feed, strategy, risk,
            initial_capital=capital, fee_rate=fee, slippage=slip,
        ).run()

    # ---- metrics ----
    m = result
    cols = st.columns(4)
    cols[0].metric("总收益 %", f"{m.total_return_pct:.2f}")
    cols[1].metric("年化 (CAGR) %", f"{m.cagr_pct:.2f}")
    cols[2].metric("夏普", f"{m.sharpe:.2f}")
    cols[3].metric("索提诺", f"{m.sortino:.2f}")
    cols = st.columns(4)
    cols[0].metric("最大回撤 %", f"{m.max_drawdown_pct:.2f}")
    cols[1].metric("盈利因子", f"{m.profit_factor:.2f}" if m.profit_factor != float('inf') else "∞")
    cols[2].metric("胜率 %", f"{m.win_rate_pct:.1f}")
    cols[3].metric("交易次数", f"{m.num_trades}")

    if m.halted:
        st.error(f"⚠️ 风控熔断触发：{m.halt_reason}")

    # ---- equity + drawdown ----
    import plotly.graph_objects as go

    eq = m.equity_curve
    peak = []
    pk = eq[0] if eq else 0
    dd = []
    for e in eq:
        pk = max(pk, e)
        peak.append(pk)
        dd.append((e / pk - 1.0) * 100 if pk > 0 else 0.0)

    fig = go.Figure()
    fig.add_trace(go.Scatter(y=eq, name="权益", line=dict(color="#2563eb")))
    fig.add_trace(go.Scatter(y=peak, name="峰值", line=dict(color="#9ca3af", dash="dot")))
    fig.update_layout(title="权益曲线", height=360, margin=dict(l=40, r=20, t=40, b=30))
    st.plotly_chart(fig, use_container_width=True)

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(y=dd, name="回撤 %", fill="tozeroy", line=dict(color="#dc2626")))
    fig2.update_layout(title="回撤曲线", height=260, margin=dict(l=40, r=20, t=40, b=30))
    st.plotly_chart(fig2, use_container_width=True)

    # ---- trades ----
    if m.trades:
        rows = [t.__dict__ for t in m.trades]
        st.subheader(f"交易明细 ({len(rows)})")
        st.dataframe(rows, use_container_width=True)
    else:
        st.warning("该参数下无成交（策略未触发信号）。可调整策略参数或换数据源。")

    # ---- live ticker (optional) ----
    if src == "实盘 (Binance)":
        try:
            from omni_trader.exchange import BinanceAdapter

            last = BinanceAdapter().get_ticker(live_symbol)
            st.sidebar.success(f"实时 {live_symbol}: {last}")
        except Exception:  # noqa
            st.sidebar.warning("实时行情不可用（需 ccxt 且未配置密钥则只能拉公开行情）。")


if __name__ == "__main__":
    main()
