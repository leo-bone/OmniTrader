#!/usr/bin/env bash
# OmniTrader 一键入口（无需 pip install）。
#
#   ./run.sh --strategy momentum                      # 回测（内置样例数据）
#   ./run.sh --strategy grid --data data/BTCUSDT_1h.json
#   ./run.sh evolve --population 32 --generations 12  # 基因式策略进化
#   ./run.sh web --port 8787                          # Web 控制台（含登录）
#   ./run.sh --dashboard                              # Streamlit 轻量看板
#   ./run.sh --test                                   # 跑测试
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$(command -v python3 || echo /Users/leo/.workbuddy/binaries/python/envs/default/bin/python)}"

case "${1:-}" in
  evolve)
    shift; cd "$ROOT"; exec "$PY" -m omni_trader.cli evolve "$@"
    ;;
  web)
    shift; cd "$ROOT"; exec "$PY" -m omni_trader.cli web "$@"
    ;;
  --dashboard|-d)
    exec "$PY" -m streamlit run "$ROOT/dashboard/app.py"
    ;;
  --test|-t)
    cd "$ROOT"; exec "$PY" -m pytest -q
    ;;
  --example|-e)
    exec "$PY" "$ROOT/examples/${2:-01_backtest_api.py}"
    ;;
  --e2e)
    exec "$PY" "$ROOT/scripts/e2e_web.py" "${2:-8791}"
    ;;
  --help|-h|"")
    cat <<'USAGE'
OmniTrader 用法：

  ./run.sh --strategy momentum [回测参数]    单个配置回测
  ./run.sh evolve [进化参数]                 基因式策略进化（优胜劣汰）
  ./run.sh web --port 8787                   启动 Web 控制台 + REST API（需登录）
  ./run.sh --dashboard                       启动 Streamlit 轻量看板
  ./run.sh --test                            运行测试套件
  ./run.sh --e2e                             端到端打通前后端
  ./run.sh --example 01_backtest_api.py      运行示例脚本

也可安装为命令行：pip install -e . 后用 omnitrader / omnitrader evolve / omnitrader web
USAGE
    ;;
  *)
    cd "$ROOT"; exec "$PY" -m omni_trader.cli "$@"
    ;;
esac
