"""Offline test of peak_v12.compute on synthetic data (no network, no API key)."""
import numpy as np
import pandas as pd
import peak_v12 as V


def synthetic(seed=7, years=6):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=252 * years)
    walk = lambda mu, sd, s0: pd.Series(s0 * np.exp(np.cumsum(rng.normal(mu / 252, sd / np.sqrt(252), len(idx)))), index=idx)
    vix = pd.Series(np.clip(18 + np.cumsum(rng.normal(0, 0.8, len(idx))) * 0.1 + rng.normal(0, 2, len(idx)), 9, 80), index=idx)
    wk = pd.date_range(idx[0], idx[-1], freq="W-WED")
    return dict(es=walk(.08, .18, 3000), nq=walk(.12, .24, 8000), vix=vix, vix9d=vix * rng.uniform(.85, 1.1, len(idx)),
                skew=pd.Series(130 + rng.normal(0, 6, len(idx)), index=idx), spy=walk(.08, .18, 300), rsp=walk(.07, .19, 100),
                hyg=walk(.03, .08, 80), ief=walk(.01, .06, 100), breadth_share=pd.Series(rng.uniform(.3, .8, len(idx)), index=idx),
                walcl=pd.Series(7e6 + np.cumsum(rng.normal(0, 2e4, len(wk))), index=wk),
                wtregen=pd.Series(7e5 + rng.normal(0, 5e4, len(wk)), index=wk),
                rrp=pd.Series(np.abs(rng.normal(500, 200, len(idx))), index=idx),
                dgs2=pd.Series(2 + np.cumsum(rng.normal(0, .03, len(idx))), index=idx),
                dfii10=pd.Series(.5 + np.cumsum(rng.normal(0, .02, len(idx))), index=idx),
                t10y2y=pd.Series(np.cumsum(rng.normal(0, .02, len(idx))), index=idx),
                epu=pd.Series(np.abs(rng.normal(120, 40, len(idx))), index=idx),
                gpr=pd.Series(np.abs(rng.normal(100, 30, len(idx))), index=idx))


def test_compute_runs_and_bounds():
    out = V.compute(synthetic())
    s = out.dropna(subset=["score"])
    assert len(s) > 300
    assert s.score.between(0, 100).all() and s.pct.dropna().between(0, 100).all()
    assert set(s.next_pos.dropna().unique()) <= {1.0, 1.25}
    assert (s.families.iloc[-1]) == 7
    for f in V.FAMILIES:
        assert s[f].dropna().between(0, 100).all(), f


def test_missing_inputs_degrade_gracefully():
    d = synthetic()
    for k in ("breadth_share", "hyg", "ief", "epu", "gpr", "walcl"):
        d[k] = pd.Series(dtype=float)
    out = V.compute(d).dropna(subset=["score"])
    assert out.families.iloc[-1] == 4 and out.credit.isna().all() and out.liquidity.isna().all() and out.uncertainty.isna().all()


def test_no_lookahead():
    d = synthetic()
    full = V.compute(d)
    cut = full.index[-120]
    part = V.compute({k: (v[v.index <= cut] if len(v) else v) for k, v in d.items()})
    a, b = full.loc[:cut, "score"].dropna(), part.score.dropna()
    both = a.index.intersection(b.index)
    assert np.allclose(a[both], b[both], atol=1e-9), "score changed when later data was removed"


if __name__ == "__main__":
    test_compute_runs_and_bounds(); test_missing_inputs_degrade_gracefully(); test_no_lookahead()
    print("peak_v12 offline tests: OK")
