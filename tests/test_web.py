"""Tests for the zero-dependency web layer: auth primitives + REST endpoints."""
import json
import secrets
import threading
import time
import urllib.error
import urllib.request

import pytest

from omni_trader.web.auth import (LoginGuard, SessionManager, UserStore,
                                  home_dir)
from omni_trader.web.server import OmniTraderServer


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "home"
    d.mkdir()
    monkeypatch.setenv("OMNITRADER_HOME", str(d))
    assert home_dir() == d
    return d


# ----------------------------------------------------------------------
# password / users
# ----------------------------------------------------------------------
def test_password_hash_and_verify(home):
    store = UserStore(path=home / "u1.json")
    store.create_user("alice", "correct-horse-battery")
    assert store.authenticate("alice", "correct-horse-battery") is not None
    assert store.authenticate("alice", "wrong") is None
    assert store.authenticate("nobody", "whatever") is None


def test_password_is_salted_and_not_reversible(home):
    store = UserStore(path=home / "u2.json")
    store.create_user("bob", "same-password")
    store.create_user("carol", "same-password")
    bob, carol = store.get("bob"), store.get("carol")
    assert bob.salt != carol.salt, "identical passwords must not hash identically"
    assert bob.hash != carol.hash
    raw = json.loads((home / "u2.json").read_text())
    blob = json.dumps(raw)
    assert "same-password" not in blob, "plaintext must never touch disk"


def test_users_persist_across_instances(home):
    path = home / "u3.json"
    a = UserStore(path=path)
    a.create_user("dave", "daves-password")
    b = UserStore(path=path)          # fresh object, same file
    assert b.authenticate("dave", "daves-password") is not None


def test_weak_password_rejected(home):
    store = UserStore(path=home / "u4.json")
    with pytest.raises(ValueError):
        store.create_user("eve", "short")
    with pytest.raises(ValueError):
        store.create_user("bad name!", "long-enough-password")


def test_duplicate_username_rejected(home):
    store = UserStore(path=home / "u5.json")
    store.create_user("frank", "first-password")
    with pytest.raises(ValueError):
        store.create_user("frank", "second-password")


def test_password_change(home):
    store = UserStore(path=home / "u6.json")
    store.create_user("grace", "old-password")
    store.set_password("grace", "brand-new-password")
    assert store.authenticate("grace", "brand-new-password") is not None
    assert store.authenticate("grace", "old-password") is None


def test_bootstrap_admin_gets_a_random_password(home):
    store = UserStore(path=home / "u7.json")
    admin = store.get("admin")
    assert admin is not None
    marker = home / "admin-password.txt"
    assert marker.exists()
    assert store.initial_password_generated is True
    # an existing store must NOT be re-bootstrapped
    again = UserStore(path=home / "u7.json")
    assert again.initial_password_generated is False


# ----------------------------------------------------------------------
# sessions
# ----------------------------------------------------------------------
def test_session_roundtrip(home):
    sm = SessionManager(path=home / "s1.key")
    issued = sm.issue("alice")
    payload = sm.verify(issued["token"])
    assert payload and payload["u"] == "alice"


def test_tampered_token_is_rejected(home):
    sm = SessionManager(path=home / "s2.key")
    token = sm.issue("alice")["token"]
    blob, sig = token.split(".")
    # flip a character in the payload but keep the signature
    bad = ("A" if blob[1] != "A" else "B") + blob[1:]
    assert sm.verify(f"{bad}.{sig}") is None
    assert sm.verify(f"{blob}.{'A' * len(sig)}") is None


def test_expired_token_is_rejected(home):
    sm = SessionManager(path=home / "s3.key")
    token = sm.issue("alice", ttl=-10)["token"]
    assert sm.verify(token) is None


def test_secret_survives_restart(home):
    path = home / "s4.key"
    token = SessionManager(path=path).issue("alice")["token"]
    assert SessionManager(path=path).verify(token) is not None


def test_garbage_tokens(home):
    sm = SessionManager(path=home / "s5.key")
    assert sm.verify("") is None
    assert sm.verify("not-a-token") is None
    assert sm.verify("a.b.c") is None


# ----------------------------------------------------------------------
# brute-force guard
# ----------------------------------------------------------------------
def test_login_guard_locks_out_after_failures():
    guard = LoginGuard(max_attempts=3, lockout=60)
    ok, _ = guard.check("ip:alice")
    assert ok
    for _ in range(3):
        guard.record_failure("ip:alice")
    ok, wait = guard.check("ip:alice")
    assert not ok and wait > 0
    # another identity is unaffected
    assert guard.check("ip:bob")[0] is True
    guard.clear("ip:alice")
    assert guard.check("ip:alice")[0] is True


# ----------------------------------------------------------------------
# HTTP endpoints
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def server():
    """A real server, real sockets, in a background thread."""
    import tempfile
    import os
    home = tempfile.mkdtemp(prefix="ot-web-test-")
    admin_pw = secrets.token_urlsafe(18)
    os.environ["OMNITRADER_HOME"] = home
    os.environ["OMNITRADER_ADMIN_PASSWORD"] = admin_pw
    srv = OmniTraderServer(("127.0.0.1", 0), home=home)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True,
                              kwargs={"poll_interval": 0.2})
    thread.start()
    yield f"http://127.0.0.1:{port}", srv, admin_pw
    srv.shutdown()
    del os.environ["OMNITRADER_ADMIN_PASSWORD"]


def call(base, method, path, body=None, token=None, timeout=30):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_health_is_public(server):
    base, _, _ = server
    st, body = call(base, "GET", "/api/health")
    assert st == 200 and body["ok"] is True
    assert "momentum" in body["strategies"]


def test_login_and_protected_routes(server):
    base, _, admin_pw = server
    st, body = call(base, "POST", "/api/auth/login",
                    {"username": "admin", "password": "definitely-wrong"})
    assert st == 401 and "error" in body

    st, body = call(base, "POST", "/api/auth/login",
                    {"username": "admin", "password": admin_pw})
    assert st == 200 and body.get("token")
    token = body["token"]

    st, me = call(base, "GET", "/api/auth/me", token=token)
    assert st == 200 and me["username"] == "admin"

    st, _ = call(base, "GET", "/api/strategies")           # no token
    assert st == 401
    st, _ = call(base, "GET", "/api/strategies", token="garbage.token")
    assert st == 401


def test_strategies_endpoint_describes_the_gene_space(server):
    base, _, pw = server
    token = call(base, "POST", "/api/auth/login",
                 {"username": "admin", "password": pw})[1]["token"]
    st, body = call(base, "GET", "/api/strategies", token=token)
    assert st == 200
    assert set(body["strategies"]) == {"momentum", "mean_reversion", "grid"}
    space = body["space"]
    assert set(space["strategies"]) == {"momentum", "mean_reversion", "grid"}
    names = {g["name"] for g in space["risk"]}
    assert "risk_per_trade" in names and "max_drawdown_limit" in names
    for genes in space["strategies"].values():
        for g in genes:
            assert g["name"] and g["kind"]


def test_backtest_endpoint(server):
    base, _, pw = server
    token = call(base, "POST", "/api/auth/login",
                 {"username": "admin", "password": pw})[1]["token"]
    payload = {
        "source": {"kind": "regimes", "n": 600, "seed": 2},
        "strategy": "mean_reversion",
        "params": {"rsi_period": 14, "oversold": 30, "overbought": 70},
        "risk": {"risk_per_trade": 0.01, "max_position_pct": 0.3},
        "capital": 10000,
    }
    st, body = call(base, "POST", "/api/backtest", payload, token=token)
    assert st == 200, body
    assert body["bars"] == 600
    assert len(body["equity_curve"]) > 0
    assert isinstance(body["num_trades"], int)
    assert "sharpe" in body and "max_drawdown_pct" in body

    st, body = call(base, "POST", "/api/backtest",
                    dict(payload, strategy="nope"), token=token)
    assert st == 400


def test_static_frontend_is_served(server):
    base, _, _ = server
    for path, needle in (("/", "<!DOCTYPE html>"), ("/app.js", "function"),
                         ("/styles.css", "--bg")):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            text = r.read().decode()
        assert r.status == 200 and needle in text


def test_path_traversal_is_blocked(server):
    base, _, _ = server
    for path in ("/../server.py", "/../../pyproject.toml", "/%2e%2e/server.py"):
        try:
            urllib.request.urlopen(base + path, timeout=5)
            pytest.fail(f"traversal not blocked: {path}")
        except urllib.error.HTTPError as e:
            assert e.code in (400, 403, 404)


def test_evolution_job_lifecycle(server):
    base, _, pw = server
    token = call(base, "POST", "/api/auth/login",
                 {"username": "admin", "password": pw})[1]["token"]
    payload = {
        "source": {"kind": "regimes", "n": 700, "seed": 3},
        "config": {"population_size": 12, "generations": 3,
                   "strategies": ["momentum", "grid"]},
    }
    st, ack = call(base, "POST", "/api/evolution/start", payload, token=token)
    assert st == 202 and ack.get("job_id")
    job_id = ack["job_id"]

    st, status = call(base, "GET", f"/api/evolution/status?id={job_id}", token=token)
    assert st == 200 and status["id"] == job_id

    # poll until it finishes (small search — should be seconds)
    deadline = time.time() + 60
    while time.time() < deadline:
        st, status = call(base, "GET", f"/api/evolution/status?id={job_id}", token=token)
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.4)
    assert status["status"] == "done", status
    result = status["result"]
    assert result["champion"]["strategy"] in ("momentum", "grid")
    assert "total_return_pct" in result["champion_test"]
    assert len(result["history"]) >= 1


def test_unknown_route_404(server):
    base, _, pw = server
    token = call(base, "POST", "/api/auth/login",
                 {"username": "admin", "password": pw})[1]["token"]
    st, body = call(base, "GET", "/api/does-not-exist", token=token)
    assert st == 404 and "error" in body
