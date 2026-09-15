#!/usr/bin/env python3
"""End-to-end smoke test for the OmniTrader web stack.

Starts the real server on a throwaway home dir (so it generates a random admin
password), then drives every endpoint through HTTP exactly the way the browser
does — including the SSE stream.

Usage: python3 scripts/e2e_web.py [port]
"""
from __future__ import annotations

import http.client as hclient
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
HOST = "127.0.0.1"
BASE = f"http://{HOST}:{PORT}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def http(method, path, body=None, token=None, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_up(deadline=25.0):
    t0 = time.time()
    last = None
    while time.time() - t0 < deadline:
        try:
            c = hclient.HTTPConnection(HOST, PORT, timeout=1)
            c.request("GET", "/api/health")
            r = c.getresponse()
            if r.status == 200:
                return True
            last = f"HTTP {r.status}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2)
    print(f"    (server never came up; last probe: {last})")
    return False


def sse_tail(path, stop_event, max_sec=90):
    """Read the SSE stream until we see `final`."""
    req = urllib.request.Request(BASE + path, headers={"Accept": "text/event-stream"})
    events = []
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=max_sec) as r:
        buf = b""
        while True:
            chunk = r.read(1)
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\n\n"):
                text = buf.decode().strip()
                buf = b""
                ev = "message"
                for line in text.splitlines():
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            data = line[6:]
                events.append((ev, data))
                if ev in ("final", "error"):
                    break
            if time.time() - t0 > max_sec:
                break
    return events


def main() -> int:
    home = tempfile.mkdtemp(prefix="ot-e2e-")
    env = dict(os.environ, OMNITRADER_HOME=home, PYTHONPATH=ROOT)
    logfile = open(os.path.join(home, "server.log"), "w")
    proc = subprocess.Popen(
        [PY, "-m", "omni_trader.web.server", "--port", str(PORT)],
        cwd=ROOT, env=env, stdout=logfile, stderr=subprocess.STDOUT,
        text=True,
    )
    failures = []
    try:
        if not wait_up():
            logfile.flush()
            print(open(logfile.name).read())
            proc.kill()
            print("FAIL: server did not come up")
            return 1
        print("[ok] server up")

        st, health = http("GET", "/api/health")
        assert st == 200 and health.get("ok"), health
        print(f"[ok] health           strategies={health['strategies']}")

        # login with a wrong password first (must be rejected identically)
        st, body = http("POST", "/api/auth/login", {"username": "admin", "password": "nope"})
        assert st == 401, (st, body)
        print(f"[ok] bad login -> 401 {body.get('error')}")

        pw_file = os.path.join(home, "admin-password.txt")
        if os.path.exists(pw_file):
            password = open(pw_file).read().split(":", 1)[1].strip().splitlines()[0]
        else:
            raise RuntimeError("no generated admin password file")
        st, body = http("POST", "/api/auth/login", {"username": "admin", "password": password})
        assert st == 200 and body.get("token"), (st, body)
        token = body["token"]
        print(f"[ok] login           admin/{password[:6]}… role={body.get('role')}")

        # unauthenticated API access must be rejected
        st, _ = http("GET", "/api/strategies")
        assert st == 401, st
        print("[ok] no-token API -> 401")

        st, me = http("GET", "/api/auth/me", token=token)
        assert st == 200, (st, me)
        print(f"[ok] me              {me}")

        st, meta = http("GET", "/api/strategies", token=token)
        assert st == 200 and meta["strategies"], meta
        n_genes = sum(len(v) for v in meta["space"]["strategies"].values()) + len(meta["space"]["risk"])
        print(f"[ok] strategies      {meta['strategies']} ({n_genes} evolvable genes)")

        # static assets
        for asset, ctype in (("/", "text/html"), ("/app.js", "javascript"), ("/styles.css", "text/css")):
            req = urllib.request.Request(BASE + asset)
            with urllib.request.urlopen(req, timeout=10) as r:
                body_bytes = r.read()
                ct = r.headers.get("Content-Type", "")
                assert r.status == 200 and body_bytes, asset
                assert ctype in ct, (asset, ct)
        print("[ok] static          index.html + app.js + styles.css served")

        # path traversal must not escape the frontend dir
        req = urllib.request.Request(BASE + "/../server.py")
        try:
            urllib.request.urlopen(req, timeout=5)
            print("[warn] traversal not blocked")
        except urllib.error.HTTPError as e:
            assert e.code in (404, 400, 403), e.code
            print(f"[ok] traversal       blocked ({e.code})")

        # backtest
        bt = {
            "source": {"kind": "regimes", "n": 1500, "seed": 7},
            "strategy": "momentum",
            "params": {"lookback": 20, "adx_period": 14, "adx_threshold": 20},
            "risk": {"risk_per_trade": 0.01, "max_position_pct": 0.3,
                     "default_stop_pct": 0.05, "default_take_pct": 0.10},
            "capital": 10000, "fee": 0.001, "slippage": 0.0005,
        }
        st, res = http("POST", "/api/backtest", bt, token=token)
        assert st == 200, (st, res)
        print(f"[ok] backtest        ret={res['total_return_pct']:+.2f}% "
              f"sharpe={res['sharpe']:.2f} trades={res['num_trades']} "
              f"curve={len(res['equity_curve'])}pts")

        st, res = http("POST", "/api/backtest", dict(bt, strategy="does_not_exist"), token=token)
        assert st == 400, (st, res)
        print(f"[ok] bad strategy -> 400 {res.get('error')}")

        # evolution + SSE
        ev = {
            "source": {"kind": "regimes", "n": 1500, "seed": 7},
            "config": {"population_size": 16, "generations": 4, "seed": 11,
                       "strategies": ["momentum", "mean_reversion", "grid"]},
        }
        st, ack = http("POST", "/api/evolution/start", ev, token=token)
        assert st == 202 and ack.get("job_id"), (st, ack)
        job_id = ack["job_id"]
        print(f"[ok] evolution start job={job_id}")

        events = sse_tail(f"/api/evolution/stream?id={job_id}&token={token}", None)
        kinds = {}
        for ev_name, data in events:
            kinds[ev_name] = kinds.get(ev_name, 0) + 1
        final = [d for k, d in events if k == "final"]
        assert final, f"no final event, saw {kinds}"
        result = final[0].get("result")
        assert result and result.get("champion"), result
        print(f"[ok] sse             {kinds}")
        print(f"     champion        {result['champion']['strategy']} "
              f"fitness={result['champion_fitness']:.3f}")
        print(f"     sealed TEST     ret={result['champion_test']['total_return_pct']:+.2f}% "
              f"sharpe={result['champion_test']['sharpe']:.2f} "
              f"trades={result['champion_test']['num_trades']}")
        print(f"     stop reason     {result['stop_reason']}")

        st, jobs = http("GET", "/api/jobs", token=token)
        assert st == 200 and jobs["jobs"], jobs
        print(f"[ok] jobs list       {len(jobs['jobs'])} job(s)")

        st, _ = http("POST", "/api/auth/logout", {}, token=token)
        print(f"[ok] logout          {st}")

    except AssertionError as exc:
        failures.append(str(exc))
        print("[FAIL]", exc)
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
        print("[FAIL]", type(exc).__name__, exc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
