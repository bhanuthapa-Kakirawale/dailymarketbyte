"""FastAPI application + the `python -m private_desk.app` launcher.

Every route is a GET: the desk has no endpoint that changes anything. Pages are server-rendered
(Jinja2); the only client-side code is table sort/filter and the Plotly chart, whose library is
served from the installed `plotly` package (no CDN - the desk works offline).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import math
import os
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Undefined
from markupsafe import Markup, escape

from . import DESK_VERSION
from .services import DeskService
from .services import attention as at
from .services import candidates as cs
from .services import rules
from .services.history import HistoryFilter
from .settings import DEFAULT_HOST, DEFAULT_PORT, DeskSettings

HERE = os.path.dirname(os.path.abspath(__file__))
NA = Markup('<span class="na" title="data unavailable">UNAVAILABLE</span>')


# ------------------------------------------------------------------ template filters
def _missing(v) -> bool:
    return v is None or isinstance(v, Undefined) or (isinstance(v, float) and math.isnan(v))


def f_num(v, digits: int = 2):
    if _missing(v):
        return NA
    from config import fmt_in
    return fmt_in(float(v), digits)


def f_pct(v, digits: int = 2, signed: bool = True):
    if _missing(v):
        return NA
    cls = "pos" if v > 0 else "neg" if v < 0 else "flat"
    text = f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"
    return Markup(f'<span class="{cls}">{escape(text)}</span>')


def f_pp(v, digits: int = 1):
    if _missing(v):
        return NA
    cls = "pos" if v > 0 else "neg" if v < 0 else "flat"
    return Markup(f'<span class="{cls}">{v:+.{digits}f} pp</span>')


def f_x(v, digits: int = 1):
    return NA if _missing(v) else f"{v:.{digits}f}x"


def f_vol(v):
    if _missing(v):
        return NA
    v = float(v)
    if v >= 1e7:
        return f"{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{v / 1e5:.2f} L"
    from config import fmt_in
    return fmt_in(v, 0)


def f_date(v):
    if _missing(v):
        return NA
    return v.isoformat() if hasattr(v, "isoformat") else str(v)[:10]


def f_or_na(v):
    return NA if _missing(v) or v == "" else v


def f_label(v):
    return NA if not v else str(v).replace("_", " ")


_STRUCT_SHORT = {"BREAK_ABOVE_20D_RANGE": "▲20D", "BREAK_BELOW_20D_RANGE": "▼20D",
                 "BREAK_ABOVE_50D_RANGE": "▲50D", "BREAK_BELOW_50D_RANGE": "▼50D",
                 "CROSS_ABOVE_SMA20": "↗SMA20", "CROSS_BELOW_SMA20": "↘SMA20",
                 "CROSS_ABOVE_SMA50": "↗SMA50", "CROSS_BELOW_SMA50": "↘SMA50"}


def f_structure(v):
    """Compact structure-event label ("BREAK_BELOW_20D_RANGE, ..." -> "▼20D ▼50D")."""
    if not v:
        return "—"
    return " ".join(_STRUCT_SHORT.get(p.strip(), p.strip()) for p in str(v).split(","))


def build_templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    t.env.filters.update(num=f_num, pct=f_pct, pp=f_pp, x=f_x, vol=f_vol, d=f_date, na=f_or_na,
                         label=f_label, structure=f_structure)
    t.env.globals.update(rules=rules, change_labels=cs.CHANGE_LABELS, desk_version=DESK_VERSION,
                         NA=NA, attention_max=at.ATTENTION_MAX, attention_min=at.ATTENTION_MIN)
    return t


# ------------------------------------------------------------------ app
def create_app(settings: DeskSettings) -> FastAPI:
    app = FastAPI(title="Private Trading Intelligence Desk", docs_url=None, redoc_url=None,
                  openapi_url=None)
    svc = DeskService(settings)
    templates = build_templates()
    app.state.service = svc
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

    def page(request: Request, name: str, session, notice: str = "", **ctx):
        fresh = svc.freshness()
        return templates.TemplateResponse(request, name, {
            "session": session, "sessions": svc.sessions(), "notice": notice,
            "fresh": fresh, "page": name.split(".")[0], **ctx})

    def unavailable(request: Request, notice: str, name: str = "dashboard"):
        return page(request, "unavailable.html", None, notice, title=name)

    @app.exception_handler(Exception)
    async def _fail_safe(request: Request, exc: Exception):
        return HTMLResponse(templates.get_template("error.html").render(
            message=f"{type(exc).__name__}: {exc}"), status_code=500)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, session: str | None = None):
        s, notice = svc.resolve_session(session)
        if s is None:
            return unavailable(request, notice)
        return page(request, "dashboard.html", s, notice, d=svc.dashboard(s))

    @app.get("/radar", response_class=HTMLResponse)
    def radar(request: Request, session: str | None = None):
        s, notice = svc.resolve_session(session)
        if s is None:
            return unavailable(request, notice, "radar")
        return page(request, "radar.html", s, notice, d=svc.radar(s))

    @app.get("/radar.csv")
    def radar_csv(session: str | None = None):
        s, notice = svc.resolve_session(session)
        if s is None:
            raise HTTPException(404, notice)
        packets = svc.packets(s)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["session_date", "symbol", "company", "sector", "attention_level",
                    "novelty_type", "appearance", "families", "detector_ids", "price_change_pct",
                    "relative_volume", "vs_nifty_5d_pp", "vs_nifty_20d_pp", "replay_status",
                    "official_events", "reasons", "note"])
        for p in packets:
            st = p.radar_state
            w.writerow([p.session_date, p.symbol, p.company, p.sector, st["attention_level"],
                        st["novelty_type"], st["appearance"], "|".join(st["families"]),
                        "|".join(p.detector_ids), p.metrics["price_change_pct"],
                        p.metrics["relative_volume"], p.metrics["vs_nifty_5d_pp"],
                        p.metrics["vs_nifty_20d_pp"], p.metrics["replay_status"],
                        "|".join(f"{e['kind']}:{e['status']}" for e in p.official_events),
                        " / ".join(p.reasons), p.notes[0]])
        return Response(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="private_radar_{s}.csv"'})

    @app.get("/stock", response_class=HTMLResponse)
    def stock_index(request: Request, session: str | None = None, q: str = ""):
        s, notice = svc.resolve_session(session)
        if s is None:
            return unavailable(request, notice, "stock")
        if q.strip():
            sym = q.strip().upper()
            universe = svc.universe_symbols(s)
            if sym in universe:
                return page(request, "stock.html", s, notice, st=svc.stock(sym, s))
        universe = svc.universe_symbols(s)
        cands = {c.instrument for c in svc.repo.candidates(s)}
        rows = [{"symbol": k, "company": (v or {}).get("company"), "sector": (v or {}).get("sector"),
                 "candidate": k in cands} for k, v in sorted(universe.items())]
        return page(request, "stock_index.html", s, notice, rows=rows, q=q)

    @app.get("/stock/{symbol}", response_class=HTMLResponse)
    def stock(request: Request, symbol: str, session: str | None = None):
        s, notice = svc.resolve_session(session)
        if s is None:
            return unavailable(request, notice, "stock")
        return page(request, "stock.html", s, notice, st=svc.stock(symbol, s))

    @app.get("/api/chart/{symbol}")
    def chart(symbol: str, session: str | None = None):
        s, notice = svc.resolve_session(session)
        if s is None:
            return JSONResponse({"error": notice}, status_code=404)
        return JSONResponse(svc.chart(symbol, s))

    @app.get("/sectors", response_class=HTMLResponse)
    def sectors(request: Request, session: str | None = None):
        s, notice = svc.resolve_session(session)
        if s is None:
            return unavailable(request, notice, "sectors")
        return page(request, "sectors.html", s, notice, d=svc.dashboard(s))

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, start: str = "", end: str = "", symbol: str = "",
                sector: str = "", attention: str = "", family: str = "", reason: str = "",
                novelty: str = "", appearance: str = ""):
        def d(x):
            try:
                return dt.date.fromisoformat(x) if x else None
            except ValueError:
                return None
        f = HistoryFilter(start=d(start), end=d(end), symbol=symbol.strip(), sector=sector,
                          attention=attention, family=family, reason=reason, novelty=novelty,
                          appearance=appearance)
        h = svc.history(f)
        return page(request, "history.html", h.get("end"), "", h=h,
                    reason_codes=sorted(rules.REASON_RULES))

    @app.get("/quality", response_class=HTMLResponse)
    def quality(request: Request, session: str | None = None):
        s, notice = svc.resolve_session(session)
        return page(request, "quality.html", s, notice, q=svc.quality(s))

    @app.get("/healthz")
    def healthz():
        f = svc.freshness()
        return {"desk_version": DESK_VERSION, "local_only": True, "read_only": True,
                "latest_completed_session": f.latest_completed.isoformat()
                if f.latest_completed else None,
                "sources": [s.to_dict() for s in f.sources]}

    @app.get("/vendor/plotly.min.js")
    def plotly_js():
        try:
            import plotly
        except ImportError:
            raise HTTPException(404, "plotly package not installed (pip install -r requirements-desk.txt)")
        path = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
        if not os.path.isfile(path):
            raise HTTPException(404, "plotly.min.js not found in the plotly package")
        return FileResponse(path, media_type="application/javascript",
                            headers={"Cache-Control": "max-age=86400"})

    return app


# ------------------------------------------------------------------ launcher
def _print_banner(settings: DeskSettings, svc: DeskService) -> None:
    f = svc.freshness()
    print("=" * 72)
    print(" PRIVATE TRADING INTELLIGENCE DESK  -  PRIVATE / LOCAL ONLY  -  READ-ONLY")
    print("=" * 72)
    print(f" URL:            {settings.url}")
    print(f" Reading:        {settings.out_dir}")
    print(f" Cache (safe to delete): {settings.cache_dir}")
    print(f" Latest completed session (NSE calendar): {f.latest_completed or 'UNKNOWN'}")
    for s in f.sources:
        print(f"   {s.label:<22} {str(s.session or '-'):<12} {s.status:<8} {s.as_of or ''}")
    if f.warnings:
        print(" WARNING: some sources are stale or missing - check the Data Quality page.")
    print(" No trading, no orders, no uploads, no broker or YouTube credentials used.")
    print(" Radar candidate = attention item, not a trade recommendation.")
    print(" Stop with Ctrl+C.")
    print("=" * 72, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m private_desk.app",
                                 description="Private Trading Intelligence Desk (local, read-only)")
    ap.add_argument("--host", default=DEFAULT_HOST, help="loopback address only (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--out-dir", default=None, help="DMB output root to read (default: config.OUT_DIR)")
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    try:
        settings = DeskSettings.from_out_dir(args.out_dir, cache_dir=args.cache_dir,
                                             host=args.host, port=args.port)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    import uvicorn
    app = create_app(settings)
    _print_banner(settings, app.state.service)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
