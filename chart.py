"""Nifty chart rendered in 3 layers so the video can animate it:
   0 = empty axes, 1 = candles + EMAs + close, 2 = everything incl. support/resistance/trendline."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from config import fmt_in

BG = "#0d1530"
GREEN, RED, YEL, CYAN, PURPLE = "#2ee59d", "#ff4d6d", "#ffd60a", "#4cc9f0", "#b388ff"


def _draw(m, stage, path, w_px, h_px, bars):
    full = m["chart_df"]
    df = full.tail(bars).reset_index(drop=True)
    n = len(df)
    off = len(full) - n
    x = np.arange(n)
    fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.02, 0.07, 0.80, 0.90], facecolor=BG)
    c = m["close"]

    if stage >= 1:
        up = (df.Close >= df.Open).values
        col = np.where(up, GREEN, RED)
        ax.vlines(x, df.Low, df.High, colors=col, linewidth=1.1)
        body = np.maximum((df.Close - df.Open).abs().values, df.Close.mean() * 0.0004)
        ax.bar(x, body, bottom=np.minimum(df.Open, df.Close), width=0.62, color=col, linewidth=0)
        ax.plot(x, df.ema20, color=CYAN, lw=1.6)
        ax.plot(x, df.ema50, color=PURPLE, lw=1.6)
        ax.axhline(c, color="white", ls=":", lw=1, alpha=0.45)

    t = m["trend"]
    if stage >= 2:
        if t and t["x0"] - off >= 0:
            x0, x1 = t["x0"] - off, t["x1"] - off + 3
            ax.plot([x0, x1], [t["y0"], t["y0"] + t["slope"] * (x1 - x0)], color=YEL, lw=2.8)
        for r in m["levels"]["res"]:
            ax.axhline(r, color=RED, ls="--", lw=1.8, alpha=0.9)
        for s_ in m["levels"]["sup"]:
            ax.axhline(s_, color=GREEN, ls="--", lw=1.8, alpha=0.9)

    lo = min([df.Low.min(), df.ema50.min(), df.ema20.min()] + m["levels"]["sup"])
    hi = max([df.High.max(), df.ema50.max(), df.ema20.max()] + m["levels"]["res"])
    pad = (hi - lo) * 0.06
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, n + 1)

    # right-side labels, pushed apart so they never overlap (positions identical in every stage)
    labels = [(r, f"R {fmt_in(r)}", RED, BG, 2) for r in m["levels"]["res"]]
    labels += [(s_, f"S {fmt_in(s_)}", GREEN, BG, 2) for s_ in m["levels"]["sup"]]
    labels += [(c, fmt_in(c), BG, "white", 1)]
    labels.sort(key=lambda z: z[0])
    gap = (hi - lo + 2 * pad) * 0.055
    ys = [z[0] for z in labels]
    for _ in range(50):
        for i in range(1, len(ys)):
            if ys[i] - ys[i - 1] < gap:
                mid = (ys[i] + ys[i - 1]) / 2
                ys[i - 1], ys[i] = mid - gap / 2, mid + gap / 2
    for (y, txt, fg, bg, need), yy in zip(labels, ys):
        if stage >= need:
            ax.text(n + 1.8, yy, txt, color=fg, fontsize=14, fontweight="bold", va="center", ha="left",
                    clip_on=False, bbox=dict(boxstyle="round,pad=0.28", fc=bg, ec=fg if bg == BG else bg, lw=1.3))

    dates = full.index[-n:]
    ticks = np.linspace(3, n - 3, 4).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([dates[i].strftime("%d %b") for i in ticks], color="#96a2c4", fontsize=13)
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    ax.grid(axis="x", color="white", alpha=0.07)
    handles = [Line2D([], [], color=CYAN, lw=3, label="EMA 20"), Line2D([], [], color=PURPLE, lw=3, label="EMA 50")]
    if t:
        handles.append(Line2D([], [], color=YEL, lw=3, label=f"Trendline ({t['kind']})"))
    ax.legend(handles=handles, loc="upper left", fontsize=12, frameon=False, labelcolor="#dfe6ff")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def make_chart(m: dict, prefix: str, w_px: int = 980, h_px: int = 680, bars: int = 75) -> list:
    return [_draw(m, s, f"{prefix}_{s}.png", w_px, h_px, bars) for s in (0, 1, 2)]
