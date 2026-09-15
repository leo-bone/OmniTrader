"""示例 3：链上执行层（Solana / BSC）。

默认 simulation=True —— 报价是真实的，成交是模拟的，绝不碰你的钱包。
只有同时满足两个条件才会发真实交易：
    1) 构造客户端时 simulation=False
    2) 调用 execute_swap(..., live=True) 且传入带真实私钥的 Signer

私钥只从环境变量读取：SOLANA_PRIVATE_KEY / BSC_PRIVATE_KEY，永不打印。

用法：
    python examples/03_onchain_quote.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni_trader.onchain import JupiterClient, PancakeSwapClient, Quote, EnvSigner  # noqa: E402

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"


def demo_offline_simulation() -> None:
    """不需要网络：手工构造一个 Quote，走模拟成交路径。"""
    print("=== A. 离线：模拟成交（不联网、不签名）===")
    q = Quote(
        input_mint=SOL_MINT,
        output_mint=USDC_MINT,
        in_amount=Decimal("1.5"),
        out_amount=Decimal("247.83"),
        price_impact_pct=Decimal("0.12"),
    )
    jup = JupiterClient(simulation=True)
    print("  solana ->", jup.execute_swap(q, live=True))  # live=True 也会被 simulation 拦住

    cake = PancakeSwapClient(simulation=True)
    q2 = Quote(input_mint=WBNB, output_mint=USDT_BSC,
               in_amount=Decimal("0.5"), out_amount=Decimal("298.10"),
               price_impact_pct=Decimal("0"))
    print("  bsc    ->", cake.execute_swap(q2, live=True))


def demo_live_quote() -> None:
    """联网拉取真实报价（Jupiter 公开 API）。失败会优雅跳过。"""
    print("\n=== B. 联网：真实报价（只读，不交易）===")
    try:
        jup = JupiterClient(simulation=True)
        q = jup.get_quote(SOL_MINT, USDC_MINT, Decimal("1.0"), slippage_bps=50)
        print(f"  1 SOL -> {q.out_amount} USDC (price impact {q.price_impact_pct}%)")
    except Exception as e:  # 网络/限流
        print(f"  [跳过] 拉报价失败：{type(e).__name__}: {e}")
        print("         这是网络问题，不影响本地功能。")


def demo_signer_safety() -> None:
    """演示私钥只读环境变量、且永不出现在 repr/str 里。"""
    print("\n=== C. 私钥安全：EnvSigner ===")
    import os

    if not os.environ.get("SOLANA_PRIVATE_KEY"):
        print("  未设置 SOLANA_PRIVATE_KEY —— 这正是默认安全行为。")
        print("  若要真交易：export SOLANA_PRIVATE_KEY='<base58 私钥>'（注意加引号、别写进 shell 历史）")
        return
    s = EnvSigner("SOLANA_PRIVATE_KEY")
    print(f"  repr: {s!r}")     # 不会泄漏私钥
    print(f"  pub : {s.public_key()}")


if __name__ == "__main__":
    demo_offline_simulation()
    demo_live_quote()
    demo_signer_safety()
