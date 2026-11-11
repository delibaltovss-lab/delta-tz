#!/usr/bin/env python3
"""DELTA Matryoshka: deterministic, no-lookahead trendline backtest.
Public historical Binance USD-M futures archives; educational research, not live trading.
Python 3.10+. pip install -r requirements.txt
"""
from __future__ import annotations

import argparse
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BASE_URL = "https://data.binance.vision/data/futures/um"
FIELDS = ["open_time", "open", "high", "low", "close", "volume"]
PAIRS = [("1mo", "1w"), ("1w", "1d"), ("4h", "1h"), ("1h", "15m")]
EXIT_MODES = ["rr2", "trend_break", "atr_trail"]
FREQS = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON", "1mo": "MS"}
COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass
class Config:
    start: pd.Timestamp
    end: pd.Timestamp
    atr_len: int = 14
    atr_multiplier: float = 1.5
    pivot_span: int = 2
    near_atr: float = 0.25
    touch_from_atr: float = 0.30
    max_anchor_age: int = 50
    risk_usd: float = 2.0
    fee: float = 0.0005
    slip: float = 0.0002
    # Position ceiling optional. None = standardized fixed USD risk for research.
    max_notional: float | None = None


def month_starts(start: date, end: date):
    dt = date(start.year, start.month, 1)
    while dt < end:
        yield dt
        dt = date(dt.year + (dt.month == 12), 1 if dt.month == 12 else dt.month + 1, 1)


def fetch_zip(url: str, cache: Path) -> bytes:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return cache.read_bytes()
    r = requests.get(url, timeout=75)
    r.raise_for_status()
    if not zipfile.is_zipfile(io.BytesIO(r.content)):
        raise ValueError(f"Invalid ZIP: {url}")
    cache.write_bytes(r.content)
    return r.content


def parse_archive(data: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = [x for x in z.namelist() if x.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"Unexpected contents: {z.namelist()}")
        with z.open(members[0]) as f:
            raw = pd.read_csv(f, header=None, low_memory=False)
    # USD-M archive may include a header (spot generally doesn't).
    if str(raw.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        raw = raw.iloc[1:].copy()
    if raw.shape[1] < 6:
        raise ValueError("Expected >= 6 kline columns")
    raw = raw.iloc[:, :6].copy()
    raw.columns = FIELDS
    numeric = pd.to_numeric(raw["open_time"], errors="coerce")
    if numeric.isna().any():
        raise ValueError("Invalid timestamp in archive")
    med = float(numeric.median())
    unit = "us" if med > 1e14 else "ms"
    raw.index = pd.to_datetime(numeric, unit=unit, utc=True)
    out = raw[COLUMNS].apply(pd.to_numeric, errors="raise").astype(float)
    return out


def load_15m(symbol: str, warmup: date, end: date, cache_dir: Path, csv_dir: Path | None):
    if csv_dir is not None:
        file = csv_dir / f"{symbol}-15m.csv"
        raw = pd.read_csv(file)
        cols = set(raw.columns)
        if not {"open_time", *COLUMNS}.issubset(cols):
            raise ValueError(f"CSV {file} needs open_time, {COLUMNS}")
        numeric = pd.to_numeric(raw["open_time"], errors="coerce")
        if numeric.isna().any():
            raise ValueError("open_time should be Unix ms/us integers")
        ix = pd.to_datetime(numeric, unit="us" if numeric.median() > 1e14 else "ms", utc=True)
        out = pd.DataFrame({c: pd.to_numeric(raw[c]).to_numpy(dtype=float) for c in COLUMNS}, index=ix)
        return clean_and_check(out, warmup, end)

    frames = []
    # Monthly archive is normally published on first Monday of following month.
    # Current/final month is downloaded from daily archives.
    today = date.today()  # The last *complete* daily ZIP is typically yesterday UTC.
    for m in month_starts(warmup, end):
        next_month = date(m.year + (m.month == 12), (m.month % 12) + 1, 1)
        name = f"{symbol}-15m-{m:%Y-%m}"
        # For already finished months try monthly first. For the latest one try
        # monthly too (it may have been published); fall back to daily if 404.
        monthly_url = f"{BASE_URL}/monthly/klines/{symbol}/15m/{name}.zip"
        cache = cache_dir / symbol / f"{name}.zip"
        try:
            frames.append(parse_archive(fetch_zip(monthly_url, cache)))
            print(f"  {symbol}: {m:%Y-%m} monthly", flush=True)
            continue
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            if next_month < end - timedelta(days=35):
                raise RuntimeError(f"Unexpected missing historical monthly archive: {monthly_url}") from exc
        for day_offset in range((min(end, next_month) - max(warmup, m)).days):
            d = max(warmup, m) + timedelta(days=day_offset)
            if d >= today:
                raise RuntimeError(f"Archive for {d} is not yet finalized; choose --end at/before today UTC")
            daily_name = f"{symbol}-15m-{d:%Y-%m-%d}"
            daily_url = f"{BASE_URL}/daily/klines/{symbol}/15m/{daily_name}.zip"
            daily_path = cache_dir / symbol / f"{daily_name}.zip"
            frames.append(parse_archive(fetch_zip(daily_url, daily_path)))
            print(f"  {symbol}: {d:%Y-%m-%d} daily", flush=True)
    if not frames:
        raise RuntimeError("No data obtained")
    return clean_and_check(pd.concat(frames), warmup, end)


def clean_and_check(df: pd.DataFrame, warmup: date, end: date) -> pd.DataFrame:
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    a = pd.Timestamp(warmup, tz="UTC")
    b = pd.Timestamp(end, tz="UTC")
    df = df[(df.index >= a) & (df.index < b)]
    if df.empty:
        raise ValueError("Empty data after date filtering")
    if not df.index.is_monotonic_increasing or df[COLUMNS].isna().any().any():
        raise ValueError("Out-of-order or incomplete candles")
    gaps = df.index.to_series().diff().dropna()
    big = gaps[gaps > pd.Timedelta(minutes=15)]
    if len(big):
        print(f"  WARNING: {len(big)} gaps > 15m; largest {big.max()}")
        # A missing 15m candle makes resampling + stop handling inaccurate.
        # Avoid silently producing misleading backtest statistics.
        raise ValueError(f"{len(big)} missing market data segments; fix data first")
    return df


def aggregate(base: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "15m":
        return base.copy()
    rules = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    res = base.resample(FREQS[tf], label="left", closed="left").agg(rules).dropna()
    # Exclude partially sampled bars at dataset edges: resample bins must
    # contain a full set of contiguous 15m source candles.
    counts = base["close"].resample(FREQS[tf], label="left", closed="left").count()
    if tf in {"1h", "4h", "1d", "1w"}:
        expected = {"1h": 4, "4h": 16, "1d": 96, "1w": 672}[tf]
        res = res[counts == expected]
    else:
        expected = res.index.days_in_month * 96
        res = res[counts == expected]
    return res


def bar_close_times(ix: pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    if tf == "1mo":
        return ix + pd.offsets.MonthBegin(1)
    return ix + pd.Timedelta({"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D", "1w": "7D"}[tf])


def atr_wilder(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    # Wilder RMA: alpha=1/period, seed with mean of first period TRs.
    if len(tr) < period:
        return pd.Series(np.nan, index=df.index)
    arr = tr.to_numpy(float)
    out = np.full(len(arr), np.nan)
    out[period - 1] = arr[:period].mean()
    for i in range(period, len(arr)):
        out[i] = (out[i-1] * (period-1) + arr[i]) / period
    return pd.Series(out, index=df.index)


def pivots_at_confirmation(htf: pd.DataFrame, cfg: Config):
    n, s = len(htf), cfg.pivot_span
    highs = htf.high.to_numpy(float)
    lows = htf.low.to_numpy(float)
    pl, ph = {}, {}
    for k in range(s, n-s):
        if lows[k] < min(lows[k-s:k]) and lows[k] < min(lows[k+1:k+s+1]):
            pl[k+s] = (k, lows[k])
        if highs[k] > max(highs[k-s:k]) and highs[k] > max(highs[k+1:k+s+1]):
            ph[k+s] = (k, highs[k])
    return pl, ph


def build_confirmed_lines(htf: pd.DataFrame, tf: str, cfg: Config) -> pd.DataFrame:
    """Produce trendline state only AFTER completed HTF bar + confirmed pivot.
    Coordinates t0/t1 are wick candle OPEN times, not hindsight chosen touches.
    """
    low_events, high_events = pivots_at_confirmation(htf, cfg)
    ts = htf.index.view("int64") / 86400e9  # day-axis, UTC
    hclose = htf.close.to_numpy(float)
    h_als = atr_wilder(htf, cfg.atr_len).to_numpy(float)
    lows, highs = [], []
    long_invalid = short_invalid = False
    # directional line price at future LTF time: anchor_price + slope*(t - anchor_time)
    rec = []
    for i in range(len(htf)):
        if i in low_events:
            lows.append(low_events[i]); lows = lows[-2:]
            long_invalid = False
        if i in high_events:
            highs.append(high_events[i]); highs = highs[-2:]
            short_invalid = False
        row = {"available": bar_close_times(htf.index[i:i+1], tf)[0],
               "long_anchor_t": np.nan, "long_anchor_p": np.nan, "long_slope": np.nan,
               "short_anchor_t": np.nan, "short_anchor_p": np.nan, "short_slope": np.nan}
        for side, anchors in ((1, lows), (-1, highs)):
            if len(anchors) < 2: continue
            (j, pj), (k, pk) = anchors
            if i - k > cfg.max_anchor_age or k <= j: continue
            slope = (pk-pj)/(ts[k]-ts[j])
            if side == 1 and slope <= 0: continue
            if side == -1 and slope >= 0: continue
            level_at_close = pk + slope * (ts[i]-ts[k])
            atrval = h_als[i]
            if np.isfinite(atrval):
                if side == 1 and hclose[i] < level_at_close - 0.25 * atrval:
                    long_invalid = True
                if side == -1 and hclose[i] > level_at_close + 0.25 * atrval:
                    short_invalid = True
            invalid = long_invalid if side == 1 else short_invalid
            if invalid: continue
            key = "long" if side == 1 else "short"
            row[key + "_anchor_t"] = ts[k]
            row[key + "_anchor_p"] = pk
            row[key + "_slope"] = slope
        rec.append(row)
    return pd.DataFrame(rec).set_index("available")


def project_lines(ltf: pd.DataFrame, ltf_tf: str, state: pd.DataFrame, cfg: Config):
    # The signal is evaluated at lower-candle CLOSE. No upper candle closing
    # after that moment may contribute, including monthly/weekly bars.
    signal_ts = bar_close_times(ltf.index, ltf_tf)
    times = pd.DataFrame({"signal_close": signal_ts})
    merged = pd.merge_asof(times.sort_values("signal_close"),
                           state.reset_index().sort_values("available"),
                           left_on="signal_close", right_on="available", direction="backward")
    t = ltf.index.view("int64") / 86400e9
    ans = {}
    for side in ("long", "short"):
        ans[side] = (merged[f"{side}_anchor_p"].to_numpy(float) +
                     merged[f"{side}_slope"].to_numpy(float) *
                     (t - merged[f"{side}_anchor_t"].to_numpy(float)))
    return ans


def make_signals(ltf: pd.DataFrame, lines: dict, cfg: Config):
    """Close-confirmed touch of confirmed HTF trendline; buy/sell next bar open."""
    atr = atr_wilder(ltf, cfg.atr_len).to_numpy(float)
    o, h, l, c = (ltf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    active = {}
    for side, line in ((1, lines["long"]), (-1, lines["short"])):
        lp = np.roll(line, 1); lp[0] = np.nan
        if side == 1:
            # Previous close at least .30 ATR above support => actual pullback.
            sig = ((c[:-1] > o[:-1]) & (l[:-1] <= line[:-1] + cfg.near_atr*atr[:-1]) &
                   (l[:-1] >= line[:-1] - cfg.near_atr*atr[:-1]) &
                   (c[:-1] > line[:-1]) & (c[:-1]-line[:-1] <= 1.5*atr[:-1]) &
                   (np.roll(c,1)[:-1] > lp[:-1] + cfg.touch_from_atr*np.roll(atr,1)[:-1]))
        else:
            sig = ((c[:-1] < o[:-1]) & (h[:-1] >= line[:-1] - cfg.near_atr*atr[:-1]) &
                   (h[:-1] <= line[:-1] + cfg.near_atr*atr[:-1]) &
                   (c[:-1] < line[:-1]) & (line[:-1]-c[:-1] <= 1.5*atr[:-1]) &
                   (np.roll(c,1)[:-1] < lp[:-1] - cfg.touch_from_atr*np.roll(atr,1)[:-1]))
        valid = np.isfinite(atr[:-1]) & np.isfinite(line[:-1]) & np.isfinite(lp[:-1])
        sig &= valid
        # signals aligned to bar that closes; final bar cannot be traded.
        active[side] = np.concatenate([sig, [False]])
    return active, atr


def run_engine(ltf: pd.DataFrame, tf: str, lines: dict, signal: dict, atr: np.ndarray,
               cfg: Config, coin: str, parent: str, mode: str) -> list[dict]:
    ts = ltf.index.to_numpy()
    O, H, L, C = (ltf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    N = len(ltf)
    trades = []
    i = 1
    while i < N:
        # Signal generated by completed candle i-1. Enter at OPEN of i.
        if ltf.index[i] < cfg.start or ltf.index[i] >= cfg.end:
            i += 1; continue
        side = 1 if signal[1][i-1] else (-1 if signal[-1][i-1] else 0)
        if not side:
            i += 1; continue
        d = cfg.atr_multiplier * atr[i-1]
        if not np.isfinite(d) or d <= 0:
            i += 1; continue
        raw_entry = O[i]
        fill_entry = raw_entry * (1+side*cfg.slip)
        sl = fill_entry-side*d
        tp = fill_entry+side*2*d
        if sl <= 0: i += 1; continue
        # Denominator includes estimated roundtrip fees+slippage;
        # realized risk can still exceed this with adverse gaps.
        per_unit_risk = d + fill_entry*2*cfg.fee + fill_entry*cfg.slip
        qty = cfg.risk_usd/per_unit_risk
        if cfg.max_notional is not None:
            qty = min(qty, cfg.max_notional/fill_entry)
        if qty <= 0: i += 1; continue
        stop = sl
        extreme = fill_entry
        leave_idx = None; leave_price = None; reason = None
        # Trade loop stops at first exit, then advances to next LTF candle;
        # avoids overlapping positions for each coin/TF/mode.
        for j in range(i, N):
            price_open = O[j]
            # Stops: evaluate before targets; if both in a candle -> conservative SL.
            hit_stop = (L[j] <= stop) if side == 1 else (H[j] >= stop)
            hit_tp = (H[j] >= tp) if side == 1 else (L[j] <= tp)
            if hit_stop:
                gross_price = min(stop, price_open) if side == 1 else max(stop, price_open)
                leave_price = gross_price*(1-side*cfg.slip)
                reason = "stop"; leave_idx = j; break
            if mode == "rr2" and hit_tp:
                # Conservative: limit order fills at target, no beneficial gap.
                leave_price = tp; reason = "take_profit"; leave_idx = j; break
            if mode == "atr_trail":
                # Update stop only AFTER full candle j; active from next bar.
                extreme = max(extreme, H[j]) if side == 1 else min(extreme, L[j])
                if np.isfinite(atr[j]):
                    new_stop = extreme - side*cfg.atr_multiplier*atr[j]
                    stop = max(stop, new_stop) if side == 1 else min(stop, new_stop)
            if mode == "trend_break":
                relevant_line = lines["long" if side == 1 else "short"][j]
                crossed = (not np.isfinite(relevant_line)) or (
                    (C[j] < relevant_line) if side == 1 else (C[j] > relevant_line))
                if crossed and j+1 < N:
                    leave_price = O[j+1]*(1-side*cfg.slip)
                    reason = "line_break"; leave_idx = j+1; break
        if leave_idx is None:
            leave_idx = N-1
            leave_price = C[-1]*(1-side*cfg.slip)
            reason = "end_of_sample"
        gross = side*(leave_price-fill_entry)*qty
        costs = (fill_entry+leave_price)*qty*cfg.fee
        pnl = gross-costs
        trades.append({"coin": coin, "trend_tf": parent, "entry_tf": tf,
                       "exit_mode": mode, "direction": "LONG" if side == 1 else "SHORT",
                       "entry_time": str(ltf.index[i]), "exit_time": str(ltf.index[leave_idx]),
                       "entry": round(fill_entry, 8), "exit": round(leave_price, 8),
                       "initial_stop": round(sl, 8), "qty": round(qty, 8),
                       "pnl_usd": round(pnl, 6), "R": round(pnl/cfg.risk_usd, 6),
                       "exit_reason": reason})
        i = leave_idx + 1
    return trades


def summary(trades: pd.DataFrame, symbols, cfg):
    recs = []
    for coin in symbols:
        for parent, tf in PAIRS:
            for mode in EXIT_MODES:
                q = trades[(trades.coin == coin) & (trades.trend_tf == parent) &
                           (trades.entry_tf == tf) & (trades.exit_mode == mode)] if len(trades) else trades
                pnl = q.pnl_usd.to_numpy(dtype=float) if len(q) else np.array([])
                n = len(pnl)
                gross_wins = np.sum(pnl[pnl > 0]) if n else 0.
                gross_losses = -np.sum(pnl[pnl < 0]) if n else 0.
                equity = np.r_[0., np.cumsum(pnl)]
                dd = float(np.max(np.maximum.accumulate(equity)-equity)) if n else 0.
                recs.append({"coin": coin, "trend_tf": parent, "entry_tf": tf,
                    "exit_mode": mode, "trades": n,
                    "win_rate_pct": round(float(100*np.mean(pnl > 0)), 2) if n else None,
                    "profit_factor": round(float(gross_wins/gross_losses), 3) if gross_losses > 0 else None,
                    "total_pnl_usd": round(float(pnl.sum()), 2),
                    "avg_R": round(float(pnl.mean()/cfg.risk_usd), 3) if n else None,
                    "max_drawdown_usd": round(dd, 2),
                    "longs": int(np.sum(q.direction == "LONG")) if n else 0,
                    "shorts": int(np.sum(q.direction == "SHORT")) if n else 0})
    return pd.DataFrame(recs)


def main():
    p = argparse.ArgumentParser(description="DELTA Matryoshka historical research")
    p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    p.add_argument("--start", default="2023-11-10")
    p.add_argument("--end", default="2026-11-10", help="EXCLUSIVE UTC date; newest fully archived day")
    p.add_argument("--warmup", default="2021-01-01", help="Needed to confirm historic monthly pivots")
    p.add_argument("--cache", default="./cache")
    p.add_argument("--out", default="./results")
    p.add_argument("--csv-dir", type=Path, default=None, help="Optional local files e.g. BTCUSDT-15m.csv")
    p.add_argument("--max-notional", type=float, default=None,
                   help="Optional nominal cap per position; default none for equal-risk strategy test")
    p.add_argument("--fee", type=float, default=0.0005, help="Taker fee per side; default 0.05%%")
    p.add_argument("--slippage", type=float, default=0.0002, help="Adverse fill per side; default 0.02%%")
    args = p.parse_args()
    warmup, start, end = (date.fromisoformat(x) for x in (args.warmup, args.start, args.end))
    if not warmup < start < end:
        p.error("Expected warmup < start < end")
    cfg = Config(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                 max_notional=args.max_notional, fee=args.fee, slip=args.slippage)
    out = Path(args.out); out.mkdir(exist_ok=True, parents=True)
    all_trades = []
    meta = {"data": "Binance USD-M futures OHLCV 15m", "range_UTC": [str(start), str(end)],
            "warmup_from": str(warmup), "risk_usd": cfg.risk_usd,
            "ATR_period": cfg.atr_len, "stop_ATR_mult": cfg.atr_multiplier,
            "pivot_span": cfg.pivot_span, "touch_atr_tolerance": cfg.near_atr,
            "fee_side": cfg.fee, "slippage_side": cfg.slip, "max_notional": cfg.max_notional,
            "funding_modeled": False, "lookahead": "pivots confirmed after 2 right bars; signals evaluated on candle close"}
    for coin in args.symbols:
        print(f"\n[{coin}] Loading full 15m OHLCV ...", flush=True)
        base = load_15m(coin, warmup, end, Path(args.cache), args.csv_dir)
        print(f"[{coin}] {len(base):,} candles: {base.index[0]} .. {base.index[-1]}", flush=True)
        frames = {tf: aggregate(base, tf) for tf in FREQS}
        for parent, tf in PAIRS:
            state = build_confirmed_lines(frames[parent], parent, cfg)
            ltf = frames[tf]
            lines = project_lines(ltf, tf, state, cfg)
            signals, atr = make_signals(ltf, lines, cfg)
            for mode in EXIT_MODES:
                r = run_engine(ltf, tf, lines, signals, atr, cfg, coin, parent, mode)
                all_trades.extend(r)
                print(f"  {parent} -> {tf} | {mode}: {len(r)} trades", flush=True)
    trades = pd.DataFrame(all_trades)
    if trades.empty:
        trades = pd.DataFrame(columns=["coin", "trend_tf", "entry_tf", "exit_mode", "direction",
                                       "entry_time", "exit_time", "entry", "exit", "initial_stop",
                                       "qty", "pnl_usd", "R", "exit_reason"])
    trades.to_csv(out / "trades.csv", index=False)
    s = summary(trades, args.symbols, cfg)
    s.to_csv(out / "summary.csv", index=False)
    with (out / "methodology.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print("\nSUMMARY\n", s.to_string(index=False))
    print("\nSaved to:", out.resolve())

if __name__ == "__main__":
    main()
