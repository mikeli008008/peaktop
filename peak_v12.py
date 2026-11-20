"""Peak Detector v1.2 — seven-family peak-risk score (calculation only, no Streamlit).

Ported from the nq-replay lab (research/peaktop/v12.py and research/paper/peaktop_paper.py, 2026-11-20).
Lab result 2012-2026: the rule below beat buy-and-hold NQ in all three eras (2012-17, 2018-21, 2022-26);
edge t +2.89 on buy-and-hold, which is below the lab's money bar (t 3). It is in forward paper from 2026-11-23.
This is a risk gauge, not a sell signal: high readings came before smaller NQ gains, not reliably before falls.

How it works
  Seven families, higher = more peak risk. Each input is ranked against its own last 756 trading days (about three
  years; at least 252), giving 0-1. Inputs are averaged inside a family; the score is 100 x the average of the
  families available that day (at least 3).
    trend        ES and NQ: distance from the 200-day and 50-day averages, place in the 60-day range
    breadth      SPY at a 60-day high while RSP/SPY falls (days in 30), -(RSP/SPY 59-day change),
                 1 - share of stocks above their 200-day average
    vol          -min(VIX, its 30d mean), -max(SKEW, its 30d mean), -(ES 30d realized vol / VIX),
                 max(1 - VIX9D/VIX, its 30d mean)
    credit       -(HYG/IEF 20-day change)
    liquidity    -(Fed net liquidity 13-week change), WALCL - TGA - reverse repo
    rates        2-year yield 6-month change, 10-year real yield 6-month change, -(10y - 2y)
    uncertainty  Economic Policy Uncertainty 30-day mean, Geopolitical Risk 30-day mean
  Rule tested in the lab: hold 1.25x NQ the session after the score is in its own bottom 20% (trailing 756 days),
  else 1.0x. It never cuts below 1.0x.

Differences from the lab build (data only, same rule):
  - FRED values are current values, dated the day after each observation (the lab used first-release vintages).
  - Breadth's third input: the lab used all US stocks above $5; the app uses the stock list it is given
    (S&P 500 members today, so it has survivorship bias). Without it the family averages the other two inputs.
  - Futures closes come from Yahoo Finance continuous contracts (ES=F, NQ=F), falling back to ^GSPC / ^NDX.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RW, RMIN = 756, 252
FAMILIES = ["trend", "breadth", "vol", "credit", "liquidity", "rates", "uncertainty"]
LABELS = {"trend": "趋势 Trend", "breadth": "广度 Breadth", "vol": "波动 Volatility", "credit": "信用 Credit",
          "liquidity": "流动性 Liquidity", "rates": "利率 Rates", "uncertainty": "不确定性 Uncertainty"}

# Lab results (nq-replay research/peaktop/results/REPORT_v12.md, dev data to 2026-02-20). Fixed reference numbers.
LAB_ERAS = pd.DataFrame({
    "era": ["2012-17", "2018-21", "2022-26"],
    "days_tilted_pct": [17, 38, 18],
    "rule_sharpe": [1.49, 1.25, 0.41],
    "bh_sharpe": [1.37, 1.17, 0.40],
    "ic_next20": [-0.063, -0.402, -0.105],
})
LAB_BANDS = pd.DataFrame({   # NQ next-20-session return by the score's own percentile, all eras 2012-26
    "band": ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"],
    "mean_fwd20_pct": [3.47, 1.74, 1.08, 0.08, 0.57],
    "share_up_pct": [81.4, 68.2, 68.1, 60.2, 63.3],
    "share_fell_5pct": [4.3, 8.4, 11.3, 14.7, 11.9],
})
LAB_ALPHA_T = 2.89


def prank(s: pd.Series) -> pd.Series:
    """Percentile of each value within its own trailing RW observations (ties count half)."""
    s = s.dropna()
    return s.rolling(RW, min_periods=RMIN).apply(
        lambda a: (a[:-1] < a[-1]).mean() + 0.5 * (a[:-1] == a[-1]).mean(), raw=True)


def _known_next_day(s: pd.Series | None) -> pd.Series:
    """A FRED / index value dated d is treated as known on d + 1."""
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s = pd.Series(s).dropna().astype(float)
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize() + pd.Timedelta(days=1)
    return s[~s.index.duplicated(keep="last")].sort_index()


def _clean(s: pd.Series | None) -> pd.Series:
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s = pd.Series(s).dropna().astype(float)
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def compute(d: dict) -> pd.DataFrame:
    """d: closes as Series (index = date): es, nq, vix, vix9d, skew, spy, rsp, hyg, ief; optional breadth_share
    (0-1 share of stocks above their 200d mean); FRED series as published: walcl, wtregen, rrp, dgs2, dfii10,
    t10y2y, epu; gpr (daily Geopolitical Risk). Returns one row per trading day with the seven families (0-100),
    score, pct (0-100), held (exposure held that day) and next_pos (exposure for the next session)."""
    es, nq, vix = _clean(d.get("es")), _clean(d.get("nq")), _clean(d.get("vix"))
    idx = es.index.intersection(nq.index).intersection(vix.index)
    if len(idx) < RMIN + 210:
        raise ValueError("not enough history (need about 2 years of ES, NQ and VIX)")
    es, nq, vix = es.reindex(idx), nq.reindex(idx), vix.reindex(idx)
    vix9d = _clean(d.get("vix9d")).reindex(idx).ffill(limit=3)
    skew = _clean(d.get("skew")).reindex(idx).ffill(limit=3)
    on = lambda s: s.reindex(s.index.union(idx)).ffill(limit=5).reindex(idx)
    R = lambda s: prank(s).reindex(idx)
    fam = lambda *c: pd.concat(c, axis=1).mean(axis=1, skipna=True)
    dev = lambda c, n: c / c.rolling(n).mean() - 1
    don = lambda c: (c - c.rolling(60).min()) / (c.rolling(60).max() - c.rolling(60).min()).replace(0, np.nan)

    F = pd.DataFrame(index=idx)
    F["trend"] = fam(R((dev(es, 200) + dev(nq, 200)) / 2), R((dev(es, 50) + dev(nq, 50)) / 2), R((don(es) + don(nq)) / 2))

    spy, rsp = _clean(d.get("spy")), _clean(d.get("rsp"))
    parts = []
    if len(spy) and len(rsp):
        sr = pd.concat([spy.rename("SPY"), rsp.rename("RSP")], axis=1).dropna()
        rt = sr.RSP / sr.SPY
        div = ((sr.SPY >= sr.SPY.rolling(60).max()) & ((rt - rt.shift(60)) < 0)).astype(float).rolling(30).sum()
        div = div.where(np.arange(len(sr)) >= 89)
        parts += [R(div), R(-(rt / rt.shift(59) - 1))]
    share = _clean(d.get("breadth_share"))
    if len(share):
        parts.append(R(1 - share))
    F["breadth"] = fam(*parts) if parts else np.nan

    ev = np.minimum(vix, vix.rolling(30).mean())
    esk = np.maximum(skew, skew.rolling(30).mean())
    rv = es.pct_change().rolling(30).std() * np.sqrt(252)
    cg = 1 - vix9d / vix
    F["vol"] = fam(R(-ev), R(-esk), R(-(rv / (vix / 100))), R(np.maximum(cg, cg.rolling(30).mean())))

    hyg, ief = _clean(d.get("hyg")), _clean(d.get("ief"))
    if len(hyg) and len(ief):
        hi = pd.concat([hyg.rename("H"), ief.rename("I")], axis=1).dropna()
        F["credit"] = R(-(hi.H / hi.I).pct_change(20))
    else:
        F["credit"] = np.nan

    w, tg, rp = (_known_next_day(d.get(k)) for k in ("walcl", "wtregen", "rrp"))
    if len(w) and len(tg):
        days = pd.date_range(idx[0] - pd.Timedelta(days=400), idx[-1], freq="D")
        w, tg = w.reindex(w.index.union(days)).ffill().reindex(days), tg.reindex(tg.index.union(days)).ffill().reindex(days)
        rp = (rp.reindex(rp.index.union(days)).ffill().reindex(days) if len(rp) else pd.Series(0.0, index=days)).fillna(0.0)
        nl = w - tg - rp * 1000                       # WALCL, WTREGEN in $ millions; RRPONTSYD in $ billions
        wk = nl.resample("W").last().shift(13)
        wkend = pd.Series(days, index=days).dt.to_period("W").dt.end_time.dt.normalize()
        F["liquidity"] = R(-pd.Series(nl.values / wk.reindex(wkend.values).values - 1, index=days).reindex(idx))
    else:
        F["liquidity"] = np.nan

    dgs2, tips, curve = (on(_known_next_day(d.get(k))) if d.get(k) is not None and len(d.get(k)) else None
                         for k in ("dgs2", "dfii10", "t10y2y"))
    rparts = [R(x - x.shift(126)) for x in (dgs2, tips) if x is not None] + ([R(-curve)] if curve is not None else [])
    F["rates"] = fam(*rparts) if rparts else np.nan

    uparts = []
    for k in ("epu", "gpr"):
        s = d.get(k)
        if s is not None and len(s):
            uparts.append(R(on(_known_next_day(s)).rolling(30, min_periods=20).mean()))
    F["uncertainty"] = fam(*uparts) if uparts else np.nan

    F = F[FAMILIES]
    n = F.notna().sum(axis=1)
    score = (F.mean(axis=1, skipna=True) * 100).where(n >= 3)
    pct = prank(score).reindex(idx)
    sig = pct.shift(1)
    held = pd.Series(np.where(sig <= 0.20, 1.25, 1.0), index=idx).where(sig.notna()).shift(1)
    out = (F * 100).assign(score=score, pct=pct * 100, families=n, held=held,
                           next_pos=np.where(pct <= 0.20, 1.25, 1.0))
    out["next_pos"] = out.next_pos.where(pct.notna())
    out["nq"] = nq
    return out
