"""Product router: one request model, one runner per product mode.

    python main.py                               -> postmarket (renders from the canonical report)
    python main.py --mode postmarket             -> same
    python main.py --mode report                 -> products.report_job.run_report_job (no video)
    python main.py --mode premarket [--shadow]   -> products.premarket.run_premarket

main.py builds a `VideoRequest` and hands it to `route()`; it holds no mode-specific logic.
POST keeps its `main.run(args)` entry point - the router only calls it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

MODES = ("postmarket", "premarket", "report")


@dataclass(frozen=True)
class VideoRequest:
    mode: str = "postmarket"
    session_date: dt.date | None = None     # PRE: the session about to open (default: today IST)
                                            # REPORT: the session to build (default: latest final)
    as_of: dt.datetime | None = None        # PRE: the cutoff (default: now IST)
    upload: bool = False
    force: bool = False
    demo: bool = False
    hook_ai: bool = False
    frames_only: bool = False
    out_dir: str | None = None
    shadow: bool = False                    # PRE: shadow run (output/pre_shadow/<date>/)
    skip_radar: bool = False                # REPORT: report + intelligence only

    @classmethod
    def from_args(cls, args) -> "VideoRequest":
        sd = getattr(args, "session_date", None)
        ao = getattr(args, "as_of", None)
        return cls(mode=getattr(args, "mode", "postmarket") or "postmarket",
                   session_date=dt.date.fromisoformat(sd) if sd else None,
                   as_of=dt.datetime.fromisoformat(ao) if ao else None,
                   upload=bool(getattr(args, "upload", False)),
                   force=bool(getattr(args, "force", False)),
                   demo=bool(getattr(args, "demo", False)),
                   hook_ai=bool(getattr(args, "hook_ai", False)),
                   frames_only=bool(getattr(args, "frames_only", False)),
                   out_dir=getattr(args, "out_dir", None),
                   shadow=bool(getattr(args, "shadow", False)),
                   skip_radar=bool(getattr(args, "skip_radar", False)))


def route(request: VideoRequest, args=None, postmarket_runner=None):
    """Dispatch to the product runner. `postmarket_runner(args)` is main.run - passed in so the
    POST pipeline is invoked with its own argparse namespace."""
    if request.mode not in MODES:
        raise ValueError(f"unknown mode {request.mode!r}; expected one of {MODES}")
    if request.mode == "postmarket":
        if request.shadow:
            raise ValueError("--shadow is a premarket option; POST has no shadow mode")
        return postmarket_runner(args)
    if request.mode == "report":
        if request.upload:
            raise ValueError("--mode report builds the canonical report only; it never uploads")
        from .report_job import run_report_job
        return run_report_job(request.session_date, demo=request.demo,
                              skip_radar=request.skip_radar)
    from .premarket import run_premarket
    return run_premarket(request)


__all__ = ["VideoRequest", "route", "MODES"]
