import { useEffect, useState } from 'react';
import { ArrowClockwise, MagnifyingGlass } from '@phosphor-icons/react';
import { getMarketQuery } from './api';

const price = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 8 });
const time = value => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

function PriceChart({ points, symbol }) {
  const [hover, setHover] = useState(null);
  if (!points.length) return <p className="search-empty">该交易对暂时没有可用的价格曲线。</p>;
  const values = points.map(p => Number(p.close));
  const low = Math.min(...values), high = Math.max(...values);
  const padding = (high - low) * .12 || high * .001 || .001;
  const min = low - padding, range = high - low + padding * 2;
  const start = points[0].timestamp, end = points.at(-1).timestamp;
  const x = t => 104 + (t - start) / (end - start || 1) * 760;
  const y = value => 242 - (value - min) / range * 206;
  const selected = points[Math.min(hover ?? points.length - 1, points.length - 1)];
  const positive = values.at(-1) >= values[0];
  const locate = event => {
    const box = event.currentTarget.getBoundingClientRect();
    const cursor = (event.clientX - box.left) / box.width * 900;
    const at = start + Math.max(0, Math.min(1, (cursor - 104) / 760)) * (end - start);
    setHover(points.reduce((best, p, i) => Math.abs(p.timestamp - at) < Math.abs(points[best].timestamp - at) ? i : best, 0));
  };
  return <div className="search-chart">
    <div className="search-chart-readout"><span>{time(selected.timestamp)}</span><strong>价格 {price(selected.close)} USDT</strong></div>
    <svg viewBox="0 0 900 285" role="img" tabIndex="0" aria-label={`${symbol} 价格曲线，左右方向键查看各时间点`}
      onPointerMove={locate} onPointerLeave={() => setHover(null)} onKeyDown={event => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          setHover(index => Math.max(0, Math.min(points.length - 1, (index ?? points.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1))));
        }
      }}>
      <title>{symbol} · 15 分钟收盘价走势</title>
      {[0, 1, 2, 3, 4].map(i => {
        const value = min + range * i / 4;
        return <g key={i}><line x1="104" x2="864" y1={y(value)} y2={y(value)} stroke="#e8edf4" /><text x="94" y={y(value) + 4} textAnchor="end">{price(value)}</text></g>;
      })}
      <polyline points={points.map(p => `${x(p.timestamp)},${y(Number(p.close))}`).join(' ')} fill="none" stroke={positive ? '#159267' : '#dc4856'} strokeWidth="2.5" strokeLinejoin="round" />
      <line x1={x(selected.timestamp)} x2={x(selected.timestamp)} y1="30" y2="242" stroke="#aab8ca" strokeDasharray="4 4" />
      <circle cx={x(selected.timestamp)} cy={y(Number(selected.close))} r="4" fill="#2469db" />
      <text x="104" y="272">{time(start)}</text><text x="864" y="272" textAnchor="end">{time(end)}</text>
    </svg>
  </div>;
}

export function MarketSearch() {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [inputError, setInputError] = useState('');
  const [loading, setLoading] = useState(false);
  const [hours, setHours] = useState(24);
  useEffect(() => {
    if (!query) return;
    let stopped = false, fetching = false, controller;
    setData(null); setError('');
    const load = async () => {
      if (fetching) return;
      fetching = true; controller = new AbortController(); setLoading(true);
      try {
        const result = await getMarketQuery(query.symbol, controller.signal);
        if (!stopped) { setData(result); setError(''); }
      } catch (e) {
        if (!stopped && e.name !== 'AbortError') setError(e.message);
      } finally {
        fetching = false;
        if (!stopped) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, 30000);
    return () => { stopped = true; clearInterval(timer); controller?.abort(); };
  }, [query]);

  const submit = event => {
    event.preventDefault();
    const symbol = input.trim().toUpperCase().replace(/[\/_-]/g, '');
    if (!/^[A-Z0-9]{2,24}$/.test(symbol) || symbol === 'USDT') {
      setInputError('请输入币种代码，例如 BTC、UAI 或 BTC/USDT'); return;
    }
    setInputError(''); setInput(symbol); setQuery({ symbol });
  };
  const points = data?.points?.slice(-hours * 4) || [];
  const change = Number(data?.change_24h || 0) * 100;
  return <section className="market-search" aria-label="币种行情查询">
    <header><div><h2>币种行情查询</h2><p>输入币种代码，查看 Bitget USDT 永续合约价格走势</p></div><span>无需交易所 API</span></header>
    <form onSubmit={submit} className="market-search-form">
      <label htmlFor="market-symbol">币种代码</label>
      <div className="market-symbol-input"><MagnifyingGlass size={19} /><input id="market-symbol" placeholder="例如 BTC、UAI 或 BTC/USDT" value={input} maxLength={32} autoComplete="off" spellCheck={false} onChange={event => { setInput(event.target.value); setInputError(''); }} /></div>
      <button type="submit" disabled={!input.trim()}>{loading ? <ArrowClockwise className="spinning" /> : <MagnifyingGlass />}查询行情</button>
    </form>
    {inputError && <p className="search-error" role="alert">{inputError}</p>}
    {error && <p className="search-error" role="alert">{data ? '刷新失败，以下为上次成功获取的数据。' : ''}{error}</p>}
    {!data && <div className="search-empty" role="status">{loading ? `正在查询 ${query.symbol} 的行情…` : query ? '请检查币种代码，或重新查询。' : '输入币种后，价格曲线将在这里显示。支持大小写及 USDT 交易对格式。'}</div>}
    {data && <>
      <div className="search-summary"><div><h3>{data.symbol.replace(/USDT$/, '')} <small>/ USDT 永续</small></h3><strong>{price(data.last_price)} <small>USDT</small></strong><span className={change >= 0 ? 'search-positive' : 'search-negative'}>24h {change >= 0 ? '+' : ''}{change.toFixed(2)}%</span></div>
        <dl><div><dt>24h 最高</dt><dd>{price(data.high_24h)}</dd></div><div><dt>24h 最低</dt><dd>{price(data.low_24h)}</dd></div></dl>
        <div className="search-period" role="group" aria-label="查询曲线时间范围">{[6, 24].map(value => <button key={value} type="button" aria-pressed={hours === value} onClick={() => setHours(value)}>近 {value} 小时</button>)}</div>
      </div>
      <PriceChart key={`${data.symbol}-${hours}`} points={points} symbol={data.symbol} />
      <footer><span>15 分钟价格曲线 · 悬停或使用左右方向键查看 · 最新一根价格可能更新</span><time>更新于 {time(data.updated_at)} · 每 30 秒刷新{loading ? ' · 刷新中' : ''}</time></footer>
    </>}
  </section>;
}
