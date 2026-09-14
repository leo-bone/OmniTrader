"""Tests for the on-chain layer — simulation path only, no network, no keys."""
import pytest
from decimal import Decimal

from omni_trader.onchain import JupiterClient, PancakeSwapClient, EnvSigner, Quote


def test_jupiter_simulation_no_network():
    c = JupiterClient(simulation=True)
    q = Quote(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        in_amount=Decimal("1"),
        out_amount=Decimal("150"),
        price_impact_pct=Decimal("0.1"),
        raw={"inAmount": "1000000000", "outAmount": "150000000000"},
    )
    receipt = c.execute_swap(q, live=False)
    assert receipt["simulated"] is True
    assert "out_est" in receipt


def test_pancake_simulation_no_network():
    c = PancakeSwapClient(simulation=True)
    q = Quote("0x..","0x..", Decimal("1"), Decimal("3000"), Decimal("0.2"), raw={})
    receipt = c.execute_swap(q, live=False)
    assert receipt["simulated"] is True


def test_env_signer_does_not_leak_secret():
    s = EnvSigner("SOLANA_PRIVATE_KEY")
    assert "SOLANA_PRIVATE_KEY" in repr(s)
    # without the env var set, accessing the key must raise, not print
    import os

    os.environ.pop("SOLANA_PRIVATE_KEY", None)
    with pytest.raises(RuntimeError):
        s.public_key()  # triggers _load -> raises when unset
