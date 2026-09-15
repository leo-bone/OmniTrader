"""HTTP API + static file server for the OmniTrader web app.

Built on the standard library only (`http.server` with threads). No Flask, no
FastAPI, no uvicorn — installing the framework costs you exactly nothing, which
is the whole premise of this project.

Endpoints (all under /api require a session token except /api/health and
/api/auth/login):

    POST /api/auth/login            -> {token, expires_at}
    POST /api/auth/logout           -> {ok}
    GET  /api/auth/me               -> {username, role}
    POST /api/auth/password         -> change own password
    GET  /api/strategies            -> registry + gene space (drives the UI forms)
    POST /api/data/generate         -> synthetic OHLCV (random walk or regimes)
    POST /api/backtest              -> run one configuration
    POST /api/evolution/start       -> kick off a genetic search -> {job_id}
    GET  /api/evolution/status      -> poll state / result
    GET  /api/evolution/stream      -> SSE: live generation-by-generation feed
    POST /api/evolution/cancel      -> cooperative cancel
    GET  /api/jobs                  -> list recent jobs
    GET  /api/health                -> liveness probe (no auth)
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from ..backtest.engine import BacktestEngine
from ..data import Bar, DataFeed
from ..evolution import (EvolutionConfig, EvolutionEngine, FitnessConfig,
                         build_risk_config, build_strategy, describe_space)
from ..evolution.fitness import _downsample
from ..risk import RiskConfig
from ..strategies import REGISTRY
from .auth import LoginGuard, SessionManager, UserStore, home_dir

FRONTEND_DIR = Path(__file__).parent / "frontend"
MAX_BODY_BYTES = 8 * 1024 * 1024
JOB_TTL_SECONDS = 3600
MAX_JOBS = 50

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}


# ----------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------
class Job:
    def __init__(self, kind: str, payload: Dict[str, Any], owner: str):
        self.id = secrets.token_hex(8)
        self.kind = kind
        self.payload = payload
        self.owner = owner
        self.status = "queued"
        self.created_at = time.time()
        self.updated_at = time.time()
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.events: list = []
        self.cancel_event = threading.Event()
        self.cv = threading.Condition()
        self.progress: Dict[str, Any] = {"done": 0, "total": 0, "message": "queued"}

    def emit(self, event: str, data: Any) -> None:
        with self.cv:
            self.events.append((event, time.time(), data))
            if len(self.events) > 500:
                self.events = self.events[-500:]
            self.updated_at = time.time()
            self.cv.notify_all()

    def set_result(self, result: Dict[str, Any]) -> None:
        with self.cv:
            self.result = result
            self.status = "done"
            self.updated_at = time.time()
            self.cv.notify_all()

    def set_error(self, msg: str) -> None:
        with self.cv:
            self.error = msg
            self.status = "error"
            self.updated_at = time.time()
            self.cv.notify_all()

    def snapshot(self, include_result: bool = True) -> Dict[str, Any]:
        with self.cv:
            d: Dict[str, Any] = {
                "id": self.id, "kind": self.kind, "status": self.status,
                "owner": self.owner,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "progress": dict(self.progress),
                "event_count": len(self.events),
                "error": self.error,
            }
            if include_result:
                d["result"] = self.result
            return d


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, payload: Dict[str, Any], owner: str) -> Job:
        with self._lock:
            self._reap()
            job = Job(kind, payload, owner)
            self._jobs[job.id] = job
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, owner: Optional[str] = None) -> list:
        with self._lock:
            items = [j.snapshot(include_result=False) for j in self._jobs.values()]
        if owner:
            items = [i for i in items if i["owner"] == owner]
        return sorted(items, key=lambda i: i["created_at"], reverse=True)

    def _reap(self) -> None:
        now = time.time()
        stale = [jid for jid, j in self._jobs.items()
                 if now - j.updated_at > JOB_TTL_SECONDS]
        for jid in stale:
            self._jobs.pop(jid, None)
        if len(self._jobs) > MAX_JOBS:
            oldest = sorted(self._jobs.values(), key=lambda j: j.created_at)
            for j in oldest[: len(self._jobs) - MAX_JOBS]:
                self._jobs.pop(j.id, None)


# ----------------------------------------------------------------------
# Data helpers shared by the endpoints
# ----------------------------------------------------------------------
def feed_from_source(src: Optional[Dict[str, Any]], default_n: int = 2000) -> DataFeed:
    """Build a DataFeed from a request payload.

    Accepts either raw bars or a synthetic generator spec. Real-market CSV/JSON
    ingest lives in `examples/05_fetch_real_data.py` — the web UI takes the
    JSON it produces.
    """
    src = src or {}
    kind = src.get("kind", "regimes")
    if kind == "inline":
        raw = src.get("bars") or []
        if isinstance(raw, dict):  # {"symbol","timeframe","bars":[...]}
            symbol = raw.get("symbol", "INLINE")
            timeframe = raw.get("timeframe", "1h")
            rows = raw.get("bars", [])
        else:
            symbol = src.get("symbol", "INLINE")
            timeframe = src.get("timeframe", "1h")
            rows = raw
        bars = []
        for i, b in enumerate(rows):
            if isinstance(b, dict):
                bars.append(Bar(ts=int(b.get("ts", i)), open=float(b["open"]),
                                high=float(b["high"]), low=float(b["low"]),
                                close=float(b["close"]),
                                volume=float(b.get("volume", 0.0))))
            else:  # [ts,o,h,l,c,v]
                bars.append(Bar(ts=int(b[0]), open=float(b[1]), high=float(b[2]),
                                low=float(b[3]), close=float(b[4]),
                                volume=float(b[5]) if len(b) > 5 else 0.0))
        if len(bars) < 30:
            raise ValueError("inline data needs >= 30 bars")
        return DataFeed(symbol, timeframe, bars)
    if kind == "sample":
        return DataFeed.generate_sample(
            symbol=src.get("symbol", "BTCUSDT"),
            n=int(src.get("n", default_n)),
            seed=int(src.get("seed", 42)),
            vol=float(src.get("vol", 0.012)),
            drift=float(src.get("drift", 0.0002)),
        )
    return DataFeed.generate_regimes(
        symbol=src.get("symbol", "SYNTHUSDT"),
        n=int(src.get("n", default_n)),
        blocks=int(src.get("blocks", 8)),
        seed=int(src.get("seed", 7)),
        vol=float(src.get("vol", 0.008)),
    )


def run_backtest(cfg: Dict[str, Any]) -> Dict[str, Any]:
    feed = feed_from_source(cfg.get("source"))
    name = cfg.get("strategy", "momentum")
    if name not in REGISTRY:
        raise ValueError(f"unknown strategy '{name}'")
    risk = RiskConfig(**{k: v for k, v in (cfg.get("risk") or {}).items()
                         if k in RiskConfig.__dataclass_fields__})
    strategy = REGISTRY[name](dict(cfg.get("params") or {}))
    engine = BacktestEngine(
        feed, strategy, risk,
        initial_capital=float(cfg.get("capital", 10_000.0)),
        fee_rate=float(cfg.get("fee", 0.001)),
        slippage=float(cfg.get("slippage", 0.0005)),
    )
    res = engine.run()
    return {
        "symbol": res.symbol, "timeframe": res.timeframe,
        "initial_capital": res.initial_capital, "final_equity": res.final_equity,
        "total_return_pct": res.total_return_pct, "cagr_pct": res.cagr_pct,
        "sharpe": res.sharpe, "sortino": res.sortino,
        "max_drawdown_pct": res.max_drawdown_pct,
        "win_rate_pct": res.win_rate_pct, "profit_factor": res.profit_factor,
        "num_trades": res.num_trades, "halted": res.halted,
        "halt_reason": res.halt_reason,
        "bars": len(feed.bars),
        "equity_curve": _downsample(res.equity_curve, 400),
        "trades": [{"side": t.side, "entry": round(t.entry, 4),
                    "exit": round(t.exit, 4), "qty": round(t.qty, 6),
                    "pnl": round(t.pnl, 4), "entry_ts": t.entry_ts,
                    "exit_ts": t.exit_ts, "reason": t.reason}
                   for t in res.trades[:200]],
    }


def _evolution_config(cfg: Dict[str, Any]) -> EvolutionConfig:
    c = cfg or {}
    fit = dict(c.get("fitness") or {})
    return EvolutionConfig(
        strategies=tuple(c.get("strategies") or ("momentum", "mean_reversion", "grid")),
        population_size=int(c.get("population_size", 32)),
        generations=int(c.get("generations", 12)),
        elite_count=int(c.get("elite_count", 2)),
        tournament_size=int(c.get("tournament_size", 3)),
        crossover_rate=float(c.get("crossover_rate", 0.80)),
        mutation_rate=float(c.get("mutation_rate", 0.35)),
        mutation_scale=float(c.get("mutation_scale", 0.20)),
        patience=int(c.get("patience", 6)),
        seed=int(c.get("seed", 42)),
        mixed_strategy_crossover=bool(c.get("mixed_strategy_crossover", True)),
        train_frac=float(c.get("train_frac", 0.50)),
        val_frac=float(c.get("val_frac", 0.25)),
        initial_capital=float(c.get("capital", 10_000.0)),
        fee_rate=float(c.get("fee", 0.001)),
        slippage=float(c.get("slippage", 0.0005)),
        fitness=FitnessConfig(**fit),
    )


def run_evolution(job: Job) -> None:
    try:
        payload = job.payload
        feed = feed_from_source(payload.get("source"))
        cfg = _evolution_config(payload.get("config"))
        cfg.validate()

        def on_gen(rec):
            job.emit("generation", rec.to_dict())

        def on_progress(done, total, msg):
            job.progress = {"done": done, "total": total, "message": msg}
            job.emit("progress", job.progress)

        job.status = "running"
        job.emit("status", {"status": "running"})
        result = EvolutionEngine(feed, cfg, on_generation=on_gen,
                                 on_progress=on_progress,
                                 cancel=job.cancel_event).run()
        job.set_result(result.to_dict())
        job.emit("done", {"stop_reason": result.stop_reason})
    except Exception as exc:
        job.set_error(f"{type(exc).__name__}: {exc}")
        job.emit("error", {"message": str(exc),
                           "trace": traceback.format_exc(limit=6)})


# ----------------------------------------------------------------------
# Request handler
# ----------------------------------------------------------------------
class ApiHandler(BaseHTTPRequestHandler):
    server_version = "OmniTrader/0.2"
    protocol_version = "HTTP/1.1"

    # ---- plumbing ---------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        if os.environ.get("OMNITRADER_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def _json(self, code: int, obj: Any) -> None:
        raw = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}")
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

    def _current_user(self) -> Optional[Dict[str, Any]]:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[7:].strip()
        else:
            token = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        if not token:
            return None
        payload = self.server.sessions.verify(token)
        if not payload:
            return None
        user = self.server.users.get(payload.get("u", ""))
        if user is None:
            return None
        return {"username": user.username, "role": user.role}

    def _require_auth(self) -> Optional[Dict[str, Any]]:
        user = self._current_user()
        if user is None:
            self._json(401, {"error": "authentication required"})
            return None
        return user

    # ---- static -----------------------------------------------------
    def _serve_static(self, rel: str) -> None:
        rel = rel.split("?")[0]
        if rel in ("", "/", "/index.html"):
            path = FRONTEND_DIR / "index.html"
        else:
            candidate = (FRONTEND_DIR / rel.lstrip("/")).resolve()
            base = FRONTEND_DIR.resolve()
            if not str(candidate).startswith(str(base)) or not candidate.exists():
                self._json(404, {"error": "not found"})
                return
            path = candidate
        if not path.is_file():
            self._json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        ctype = _STATIC_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- verbs ------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._route_api(method, path, query)
            elif method == "GET":
                self._serve_static(path)
            else:
                self._json(405, {"error": "method not allowed"})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    # ---- API router -------------------------------------------------
    def _route_api(self, method: str, path: str, query: Dict[str, list]) -> None:
        srv = self.server
        body = self._read_body() if method in ("POST", "DELETE") else {}

        # ---- public ----
        if path == "/api/health" and method == "GET":
            return self._json(200, {
                "ok": True, "version": "0.2.0",
                "strategies": sorted(REGISTRY.keys()),
                "users": len(srv.users.list_users()),
                "jobs": len(srv.jobs.list()),
                "uptime_sec": round(time.time() - srv.started_at, 1),
            })

        if path == "/api/auth/login" and method == "POST":
            return self._api_login(body)

        if path == "/api/auth/register" and method == "POST":
            if os.environ.get("OMNITRADER_ALLOW_SIGNUP", "") != "1":
                return self._json(403, {"error": "self-registration is disabled"})
            return self._api_register(body)

        if path == "/api/evolution/stream" and method == "GET":
            user = self._require_auth()
            if user is None:
                return
            return self._api_stream(query)

        # ---- authenticated ----
        user = self._require_auth()
        if user is None:
            return

        routes = {
            ("/api/auth/me", "GET"): lambda: self._json(
                200, {"username": user["username"], "role": user["role"]}),
            ("/api/auth/logout", "POST"): lambda: self._json(200, {"ok": True}),
            ("/api/auth/password", "POST"): lambda: self._api_password(user, body),
            ("/api/auth/users", "GET"): lambda: self._json(
                200, {"users": srv.users.list_users()}),
            ("/api/strategies", "GET"): lambda: self._json(
                200, {"strategies": sorted(REGISTRY.keys()),
                      "space": describe_space()}),
            ("/api/data/generate", "POST"): lambda: self._api_generate(body),
            ("/api/backtest", "POST"): lambda: self._api_backtest(body),
            ("/api/evolution/start", "POST"): lambda: self._api_evo_start(user, body),
            ("/api/evolution/status", "GET"): lambda: self._api_evo_status(query),
            ("/api/evolution/cancel", "POST"): lambda: self._api_evo_cancel(body),
            ("/api/jobs", "GET"): lambda: self._json(
                200, {"jobs": srv.jobs.list(user["username"])}),
        }
        handler = routes.get((path, method))
        if handler:
            return handler()
        return self._json(404, {"error": f"no route for {method} {path}"})

    # ---- handlers ---------------------------------------------------
    def _api_login(self, body: Dict[str, Any]) -> None:
        srv = self.server
        username = str(body.get("username", "")).strip().lower()
        password = str(body.get("password", ""))
        if not username or not password:
            return self._json(400, {"error": "username and password are required"})
        key = f"{self.client_address[0]}:{username}"
        ok, wait = srv.guard.check(key)
        if not ok:
            return self._json(429, {"error": "too many failed attempts",
                                    "retry_after": wait})
        user = srv.users.authenticate(username, password)
        if user is None:
            left = srv.guard.record_failure(key)
            # identical message for bad user and bad password
            return self._json(401, {"error": "invalid credentials",
                                    "attempts_left": left})
        srv.guard.clear(key)
        session = srv.sessions.issue(user.username)
        return self._json(200, {**session, "role": user.role})

    def _api_register(self, body: Dict[str, Any]) -> None:
        username = str(body.get("username", "")).strip().lower()
        password = str(body.get("password", ""))
        try:
            user = self.server.users.create_user(username, password, role="user")
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        session = self.server.sessions.issue(user.username)
        return self._json(201, {**session, "role": user.role})

    def _api_password(self, user: Dict[str, Any], body: Dict[str, Any]) -> None:
        old = str(body.get("old_password", ""))
        new = str(body.get("new_password", ""))
        if not self.server.users.authenticate(user["username"], old):
            return self._json(401, {"error": "current password is wrong"})
        try:
            self.server.users.set_password(user["username"], new)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        return self._json(200, {"ok": True})

    def _api_generate(self, body: Dict[str, Any]) -> None:
        try:
            feed = feed_from_source(body.get("source") or body)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        closes = feed.closes()
        return self._json(200, {
            "symbol": feed.symbol, "timeframe": feed.timeframe,
            "bars": len(feed.bars),
            "first_ts": feed.bars[0].ts, "last_ts": feed.bars[-1].ts,
            "min": round(min(closes), 2), "max": round(max(closes), 2),
            "last": round(closes[-1], 2),
        })

    def _api_backtest(self, body: Dict[str, Any]) -> None:
        try:
            result = run_backtest(body)
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        return self._json(200, result)

    def _api_evo_start(self, user: Dict[str, Any], body: Dict[str, Any]) -> None:
        try:
            _evolution_config(body.get("config")).validate()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        job = self.server.jobs.create("evolution", body, user["username"])
        t = threading.Thread(target=run_evolution, args=(job,),
                             name=f"evo-{job.id}", daemon=True)
        t.start()
        return self._json(202, {"job_id": job.id, "status": job.status})

    def _api_evo_status(self, query: Dict[str, list]) -> None:
        job_id = (query.get("id") or [""])[0]
        job = self.server.jobs.get(job_id)
        if job is None:
            return self._json(404, {"error": "job not found"})
        return self._json(200, {**job.snapshot(),
                                "history": [e[2] for e in job.events
                                            if e[0] == "generation"]})

    def _api_evo_cancel(self, body: Dict[str, Any]) -> None:
        job = self.server.jobs.get(str(body.get("id", "")))
        if job is None:
            return self._json(404, {"error": "job not found"})
        job.cancel_event.set()
        job.emit("status", {"status": "cancelling"})
        return self._json(200, {"ok": True, "status": job.status})

    # ---- SSE --------------------------------------------------------
    def _api_stream(self, query: Dict[str, list]) -> None:
        job = self.server.jobs.get((query.get("id") or [""])[0])
        if job is None:
            return self._json(404, {"error": "job not found"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        sent = 0
        try:
            self._sse("hello", {"job_id": job.id, "status": job.status})
            while True:
                with job.cv:
                    job.cv.wait(timeout=15)
                    batch = job.events[sent:]
                    sent = len(job.events)
                    status = job.status
                    result = job.result
                    error = job.error
                for event, _ts, data in batch:
                    self._sse(event, data)
                if status in ("done", "error"):
                    self._sse("final", {"status": status, "result": result,
                                        "error": error})
                    break
                self._sse("ping", {"t": round(time.time(), 2)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    def _sse(self, event: str, data: Any) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        try:
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise


# ----------------------------------------------------------------------
class OmniTraderServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, home: Optional[str] = None):
        if home:
            os.environ["OMNITRADER_HOME"] = home
        super().__init__(addr, ApiHandler)
        self.users = UserStore()
        self.sessions = SessionManager()
        self.jobs = JobManager()
        self.guard = LoginGuard()
        self.started_at = time.time()


def serve(host: str = "127.0.0.1", port: int = 8787, home: Optional[str] = None,
          open_browser: bool = False) -> None:
    """Start the web server (blocking)."""
    httpd = OmniTraderServer((host, port), home=home)
    generated = getattr(httpd.users, "initial_password_generated", False)
    url = f"http://{host}:{port}"
    print("=" * 60)
    print("  OmniTrader web console")
    print("=" * 60)
    print(f"  URL     : {url}")
    print(f"  Users   : {len(httpd.users.list_users())}")
    if generated:
        pw = getattr(httpd.users, "initial_password", "?")
        print(f"  Login   : admin / {pw}")
        print(f"  (saved to {home_dir() / 'admin-password.txt'} — delete it later)")
    else:
        print("  Login   : your existing account")
    print("  Ctrl-C  : stop")
    print("=" * 60)
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.shutdown()


if __name__ == "__main__":  # python -m omni_trader.web.server
    import argparse

    p = argparse.ArgumentParser(description="OmniTrader web console")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--home", help="data dir (users.json, session secret)")
    p.add_argument("--open", action="store_true", help="open a browser on start")
    a = p.parse_args()
    serve(a.host, a.port, home=a.home, open_browser=a.open)
