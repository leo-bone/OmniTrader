# OmniTrader 使用手册

面向实际使用，不讲原理。所有命令都在项目根目录执行：

```bash
cd /Users/leo/WorkBuddy/2026-09-14-19-13-34/omnitrader
```

---

## 0. 三种启动方式（选一个）

| 方式 | 命令 | 适用场景 |
|---|---|---|
| **A. 一键脚本（推荐）** | `./run.sh --strategy momentum` | 不用装任何东西，直接跑 |
| **B. 项目内直接跑** | `python3 -m omni_trader.cli --strategy momentum` | 必须在项目根目录 |
| **C. 装成命令** | `pip install -e ".[dev]"` 后 `omnitrader --strategy momentum` | 想全局调用 |

> 说明：核心是**纯标准库零依赖**，不装也能跑回测。只有看板（streamlit）、
> 实盘（ccxt）、链上（web3）才需要额外装。
> 本机已配好：`/Users/leo/.workbuddy/binaries/python/envs/default` 这个 venv
> 写了 `.pth`，任意目录都能 `import omni_trader`。

`run.sh` 的其他用法：

```bash
./run.sh --test                    # 跑 18 个测试
./run.sh --dashboard               # 启动看板（需 streamlit）
./run.sh --example 01_backtest_api.py
```

---

## 1. 五分钟：跑第一个回测

```bash
./run.sh --strategy momentum
```

不传 `--data` 时用的是**内置样例数据**（2000 根 1h K 线，随机游走，会明确提示），
用来验证链路，结果数字没有投资意义。

输出长这样：

```
====================================================
  BTCUSDT  [1h]  strategy backtest
====================================================
  Initial capital : 10,000.00
  Final equity    : 9,853.28
  Total return    : -1.47%
  CAGR            : -6.27%
  Sharpe (ann.)   : -0.50        <- 已正确年化
  Sortino (ann.)  : -0.28
  Max drawdown    : 7.70%
  Win rate        : 33.3%
  Profit factor   : 0.90
  Trades          : 21
  ⚠ HALTED        : max drawdown limit hit ...   <- 触发熔断才有这行
====================================================
```

三个内置策略：

| 策略 | 逻辑 | 适合行情 | 可调参数 |
|---|---|---|---|
| `momentum` | Donchian 突破 + ADX 过滤 | 趋势 | `lookback`(20) `adx_period`(14) `adx_threshold`(20) |
| `mean_reversion` | RSI 超买超卖（可选布林确认） | 震荡 | `rsi_period`(14) `oversold`(30) `overbought`(70) `bb_confirm`(false) |
| `grid` | 等距网格低买高卖 | 横盘 | `grid_count`(10) `grid_gap_pct`(0.01) |

传参数用 JSON：

```bash
./run.sh --strategy momentum --params '{"lookback":10}'
./run.sh --strategy mean_reversion --params '{"bb_confirm":true}'
```

---

## 2. 用真实数据（这一步不做，回测等于白跑）

### 2.1 自动拉取（无需 API key）

```bash
python3 examples/05_fetch_real_data.py --symbol BTCUSDT --timeframe 1h --limit 2000
# -> data/BTCUSDT_1h.json
./run.sh --strategy momentum --data data/BTCUSDT_1h.json
```

优先用 ccxt（若已装），否则走 Binance 公开 REST。**国内网络可能需要代理**。
拉不到时脚本会明说原因，不会静默给你假数据。

### 2.2 自己准备 JSON

格式就一种，`ts` 是秒级时间戳：

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "bars": [
    {"ts": 1704067200, "open": 42000.0, "high": 42500.0, "low": 41800.0, "close": 42300.0, "volume": 123.4},
    {"ts": 1704070800, "open": 42300.0, "high": 42400.0, "low": 42100.0, "close": 42250.0, "volume": 98.7}
  ]
}
```

也接受**纯数组**（省掉 symbol/timeframe 外层）。`timeframe` 影响夏普的年化系数，
写错了数字就不对（`1h` → ×√(24×365)，`1d` → ×√365）。

### 2.3 关键：别只看全样本

内置样例跑出来 `grid` 亏 19.8% 并触发熔断——**不是 bug，是网格策略遇上了单边趋势**。
正确用法是切时间段对比：

```bash
# 前 1000 根（样本内）调参，后 1000 根（样本外）验证
./run.sh --strategy momentum --data data/first_half.json
./run.sh --strategy momentum --data data/second_half.json
```

样本内漂亮、样本外崩 = 过拟合，直接扔掉。

---

## 3. 风控参数（这才是这个框架值钱的地方）

```bash
./run.sh --strategy grid \
  --capital 10000 \
  --risk-per-trade 0.01 \       # 单笔最大亏损 = 1% 权益
  --max-position-pct 0.30 \     # 单标的仓位上限 30%
  --daily-loss-limit 0.05 \     # 单日亏 5% 停止交易
  --max-drawdown-limit 0.20 \   # 总回撤 20% 停止交易
  --fee 0.001 --slippage 0.0005
```

| 参数 | 默认 | 触发后果 |
|---|---|---|
| `risk_per_trade` | 0.01 | 决定每笔仓位大小（按止损距离倒推），不是固定手数 |
| `max_position_pct` | 0.30 | 超过不开仓 |
| `daily_loss_limit` | 0.05 | 当日已实现亏损超限 → `halted`，不再开仓 |
| `max_drawdown_limit` | 0.20 | 权益从峰值回撤超限 → `halted` |

**熔断是真接线的**（这点是原 4 个仓库全都没有的）。验证方式：

```bash
python3 examples/01_backtest_api.py     # 最后一段把回撤上限压到 5%，会打印 halted=True
```

---

## 4. 写自己的策略

继承 `Strategy`，实现 `on_bar(ctx) -> Signal`。完整可运行例子见
`examples/04_custom_strategy.py`（双均线）。

```python
from omni_trader.strategies import Action, Context, Signal, Strategy

class MyStrat(Strategy):
    def on_bar(self, ctx: Context) -> Signal:
        if ctx.index < 50:
            return Signal(Action.HOLD)
        closes = [b.close for b in ctx.bars]
        ma = self.ema(closes, 20)          # 内置：ema / rsi_wilder / atr
        pos = ctx.position                  # None 或 {"side","qty","entry",...}
        if pos is None and closes[-1] > ma[-1]:
            return Signal(Action.BUY, side="long", reason="above MA20")
        if pos and pos["side"] == "long" and closes[-1] < ma[-1]:
            return Signal(Action.CLOSE, reason="below MA20")
        return Signal(Action.HOLD)
```

规则只有一条你必须知道：**信号在下一根 K 线开盘成交**。
所以你可以在 `on_bar` 里放心用 `ctx.bars[-1].close`（当前收盘），不会前视。

不指定 `qty` 时由风控按止损距离和 `risk_per_trade` 自动算仓位——建议就用自动。

---

## 5. 模拟盘 → 真盘（按这个顺序，别跳步）

### 第 1 步：模拟盘（现在就能跑，不联网不碰钱）

```bash
python3 examples/02_paper_live.py
```

跑的是 **LiveTrader + PaperAdapter**，代码路径跟真盘完全一致：
同样的策略、同样的风控、同样的下单接口，只有交易所是虚拟的。
这一步能暴露 90% 的实盘 bug（仓位翻转、平不掉、熔断不生效）。

### 第 2 步：Binance 测试网

```bash
export BINANCE_API_KEY="..."
export BINANCE_API_SECRET="..."
```

```python
from omni_trader.exchange import BinanceAdapter, LiveTrader
adapter = BinanceAdapter(testnet=True)     # 默认 testnet，且默认不放真单
```

`BinanceAdapter` 三重保险：① 默认 `testnet=True`；② 没 key 就拒绝下单；
③ 数量用 `Decimal` 按 LOT_SIZE 取整（这是原仓库会被交易所拒单的那个坑）。

### 第 3 步：小额真盘

```python
adapter = BinanceAdapter(api_key=..., secret=..., testnet=False, futures=False)
trader  = LiveTrader(strategy, adapter, risk_config=cfg, initial_capital=1000)
for bar in your_live_bar_stream():     # 你自己接 WebSocket / 定时轮询
    ev = trader.step(bar)
    if ev["event"] == "halted":
        send_alert(ev["reason"]); break
```

**上真盘前逐项打勾**：

- [ ] 同参数在样本外数据上仍然盈利
- [ ] 模拟盘连续跑满 1 周无异常
- [ ] `--max-drawdown-limit` 设的是你真能承受的数字，不是默认值
- [ ] 交易所 API 已限制为"只交易、不允许提现"
- [ ] 单笔资金 ≤ 总资金 1~2%
- [ ] 有独立于程序的告警（熔断触发时你能收到通知）

---

## 6. 链上（Solana / BSC）

```bash
python3 examples/03_onchain_quote.py
```

默认 `simulation=True`：**报价是真的，成交是模拟的**。
要发真实交易必须同时满足：客户端 `simulation=False` **且** 调用时 `live=True`
**且** 环境变量里有私钥。

```bash
export SOLANA_PRIVATE_KEY='<base58 私钥>'   # 注意引号，别留在 shell history
```

私钥只通过 `EnvSigner` 从环境变量读，`repr()`/`str()` 都不会泄漏它——
这是原 QuantAgent 把私钥当钱包地址发出去那个漏洞的直接修复。

---

## 7. 看板（可选）

```bash
pip install streamlit plotly
./run.sh --dashboard
# 浏览器打开 http://localhost:8501
```

看板复用**同一套审计过的回测引擎和风控模块**（不是另写一套 JS），
所以指标跟 CLI 跑出来的完全一致。

---

## 8. 常见问题

| 现象 | 原因 |
|---|---|
| 提示 `using generated ... sample` | 没传 `--data`，跑的是内置随机数据 |
| 夏普看起来偏高/偏低 | 检查数据的 `timeframe` 写对没有，年化系数按它算 |
| `Trades: 0` | 策略条件没触发。`mean_reversion` 开 `bb_confirm` 后条件很严，先关掉试 |
| 一上来就 `HALTED` | 熔断生效了，说明这个策略/参数在这个行情下回撤超你设的上限 |
| `pip install -e .` 报 bdist_wheel 错 | venv 的 setuptools 太旧，用方式 A/B 跑就行，不影响使用 |
| 拉不到行情 | 网络到 Binance 不通，需要代理；或先跑内置样例 |

---

## 9. 目录速查

```
omni_trader/
  data.py               Bar / DataFeed，JSON 载入与生成
  strategies/           base(Action/Signal/Context/Strategy) + momentum / mean_reversion / grid
  backtest/engine.py    回测引擎（次棒开盘成交、正确多空权益、年化夏普）
  risk.py               仓位 sizing + 熔断（真实接线）
  broker.py             券商抽象
  exchange/             base / paper(模拟盘) / binance(ccxt) / live_engine(LiveTrader)
  onchain/              base / signer(EnvSigner) / solana(Jupiter) / bsc(PancakeSwap)
  cli.py                命令行入口
examples/               01 回测API+参数扫描  02 模拟盘  03 链上  04 自定义策略  05 拉真实数据
samples/                内置样例 K 线
tests/                  18 个测试
```

改完代码记得 `./run.sh --test`，18 个全绿再上真盘。
