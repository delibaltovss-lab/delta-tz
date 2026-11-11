"""Run with `python test_offline.py`; no market downloads or synthetic performance claims."""
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from backtest import (Config, aggregate, atr_wilder, build_confirmed_lines,
                      clean_and_check, load_15m, make_signals, project_lines, run_engine)


def test_no_future_leakage():
    idx = pd.date_range('2024-01-01', periods=25, freq='1D', tz='UTC')
    # Crafted support swings, only known after 2 right-hand confirmed bars.
    lows = np.array([12,11,10,9,8,9,10,11,12,11,10,11,12,13,14,12,13,14,15,16,17,18,19,20,21.], dtype=float)
    highs = lows + 2
    htf = pd.DataFrame({'open': lows+1, 'high': highs, 'low': lows, 'close': lows+1, 'volume': 10}, index=idx)
    cfg = Config(pd.Timestamp('2024-01-01',tz='UTC'), pd.Timestamp('2025-01-01',tz='UTC'), atr_len=3)
    a = build_confirmed_lines(htf, '1d', cfg)
    # Modifying future candles must not change state of earlier HTF bars.
    mutated = htf.copy()
    mutated.iloc[20:,:] = mutated.iloc[20:,:] * 4
    b = build_confirmed_lines(mutated,'1d',cfg)
    pd.testing.assert_frame_equal(a.iloc[:20],b.iloc[:20])
    assert a.index[0] == idx[0] + pd.Timedelta(days=1)


def test_ordering_and_stop_priority():
    idx = pd.date_range('2026-01-01', periods=5, freq='15min', tz='UTC')
    # Signal after first bar, enter second bar at open 100; stop at 98.5 and target 103.
    o = [100, 100, 100, 100, 100]
    h = [101, 104, 101, 101, 101]
    l = [99, 98, 99, 99, 99]
    ltf = pd.DataFrame({'open':o,'high':h,'low':l,'close':[100]*5,'volume':[10]*5}, index=idx)
    cfg = Config(pd.Timestamp('2026-01-01',tz='UTC'), pd.Timestamp('2026-01-02',tz='UTC'), risk_usd=2, fee=0, slip=0)
    lines = {'long':np.array([99,99,99,99,99],dtype=float),'short':np.full(5,np.nan)}
    signals = {1:np.array([True,False,False,False,False]),-1:np.zeros(5,dtype=bool)}
    tr = run_engine(ltf,'15m',lines,signals,np.ones(5),cfg,'TEST','1h','rr2')
    assert len(tr) == 1
    assert tr[0]['entry_time'] == str(idx[1])
    assert tr[0]['exit_reason'] == 'stop', tr
    assert tr[0]['pnl_usd'] == -2.0, tr


def test_resample_and_import():
    idx = pd.date_range('2025-01-01',periods=2000,freq='15min',tz='UTC')
    rng = np.random.default_rng(42)
    c = np.maximum(1, 100 + np.cumsum(rng.normal(0, .2, len(idx))))
    df = pd.DataFrame({'open':c,'high':c+.4,'low':c-.4,'close':c,'volume':100.}, index=idx)
    for tf in ['15m','1h','4h','1d','1w','1mo']:
        x = aggregate(df, tf)
        assert not x.empty or tf in ['1mo'], tf
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'BTCUSDT-15m.csv'
        data=df.copy();data['open_time']=data.index.astype('int64')//10**6
        data.to_csv(path,index=False)
        got=load_15m('BTCUSDT',pd.Timestamp('2025-01-01').date(),pd.Timestamp('2025-02-01').date(),Path(d),Path(d))
        assert len(got)==len(df)
        np.testing.assert_allclose(got.close,df.close)


def test_end_to_end():
    idx = pd.date_range('2024-01-01', '2026-01-01', freq='15min', inclusive='left',tz='UTC')
    rng = np.random.default_rng(49)
    drift = np.linspace(0, 2*np.pi*9, len(idx))
    cyc = 6*np.sin(drift)+2*np.sin(drift*5)
    close=np.maximum(5,100+np.linspace(0,45,len(idx))+cyc+np.cumsum(rng.normal(0,.15,len(idx))))
    op=np.r_[close[0],close[:-1]]
    df=pd.DataFrame({'open':op,'high':np.maximum(op,close)+0.2,'low':np.minimum(op,close)-0.2,
                     'close':close,'volume':100},index=idx)
    cfg=Config(pd.Timestamp('2024-10-01',tz='UTC'),pd.Timestamp('2026-01-01',tz='UTC'))
    agg={tf:aggregate(df,tf) for tf in ['1h','4h','15m']}
    for parent,tf in [('4h','1h'),('1h','15m')]:
        lines=project_lines(agg[tf],tf,build_confirmed_lines(agg[parent],parent,cfg),cfg)
        sig,atr=make_signals(agg[tf],lines,cfg)
        for mode in ['rr2','trend_break','atr_trail']:
            trades=run_engine(agg[tf],tf,lines,sig,atr,cfg,'TEST',parent,mode)
            assert all(t['entry_time'] >= str(cfg.start) for t in trades)
            assert all(t['pnl_usd'] == t['pnl_usd'] for t in trades)
    print('Synthetic end-to-end validation OK (NOT real profitability results).')

if __name__ == '__main__':
    test_no_future_leakage()
    test_ordering_and_stop_priority()
    test_resample_and_import()
    test_end_to_end()
    print('PASS: 4 offline checks')
