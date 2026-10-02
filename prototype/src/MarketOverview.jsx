import { useEffect, useMemo, useState } from "react";
import { ArrowClockwise, ChartLineUp, Pulse } from "@phosphor-icons/react";
import { getMarketOverview } from "./api";
import "./market-overview.css";
import { MarketSearch } from './MarketSearch';

const REFRESH_INTERVAL = 15000;

function formatPrice(value) {
  const price = Number(value);
  if (!Number.isFinite(price)) return "—";
  const digits = price >= 1000 ? 2 : price >= 1 ? 3 : 5;
  return price.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function Sparkline({ values, positive, symbol }) {
  const points = useMemo(() => {
    const numbers = values.map(Number).filter(Number.isFinite);
    if (numbers.length < 2) return "";
    const min = Math.min(...numbers);
    const max = Math.max(...numbers);
    const range = max - min || 1;
    return numbers.map((value, index) => {
      const x = (index / (numbers.length - 1)) * 104;
      const y = 32 - ((value - min) / range) * 28;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }, [values]);

  return (
    <svg className={positive ? "market-spark positive" : "market-spark negative"} viewBox="0 0 104 36" role="img" aria-label={`${symbol} 最近 6 小时价格走势`}>
      <title>{symbol} 最近 6 小时价格走势</title>
      {points ? <polyline points={points} fill="none" vectorEffect="non-scaling-stroke" /> : <path d="M2 20 L102 20" />}
    </svg>
  );
}

export function MarketOverview() {
  const [market, setMarket] = useState({ items: [], updated_at: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feed, setFeed] = useState({ connected: false, quotes: {} });
  useEffect(() => {
    let stopped = false, socket, retry;
    const connect = () => {
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/market/stream`);
      socket.onmessage = event => { if (!stopped) { try { setFeed(JSON.parse(event.data)); } catch {} } };
      socket.onclose = () => { if (!stopped) { setFeed({ connected: false, quotes: {} }); retry = setTimeout(connect, 2000); } };
      socket.onerror = () => socket.close();
    };
    connect();
    return () => { stopped = true; clearTimeout(retry); socket?.close(); };
  }, []);

  const load = async () => {
    try {
      const next = await getMarketOverview();
      setMarket(next);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_INTERVAL);
    return () => window.clearInterval(timer);
  }, []);

  const updatedAt = market.updated_at
    ? new Date(market.updated_at).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "等待行情";

  return (
    <div className="market-page">
      <header className="market-page-header">
        <div><h1>市场行情</h1><p>关注主流币种，以及当前持仓和挂单涉及的交易对</p></div>
        <button type="button" onClick={load} disabled={loading}><ArrowClockwise className={loading ? "spinning" : ""} />刷新行情</button>
      </header>
      <section className="market-overview" aria-label="市场行情" aria-busy={loading}>
        <div className="market-heading">
          <div><ChartLineUp size={19} weight="bold" /><span><strong>USDT 永续行情</strong><small>Bitget 公开市场 · 最近 6 小时（15 分钟线）</small></span></div>
          <span className="market-live"><i />{feed.connected ? '公开行情推送连接中' : error ? "行情连接中断" : "REST 行情兜底"}</span>
        </div>
        <div className="market-strip" aria-live="polite">
          {market.items.map((item) => {
            const quote = feed.quotes[item.symbol];
            const live = feed.connected && quote && !quote.stale;
            const change = Number(item.change_24h) * 100;
            const positive = change >= 0;
            return (
              <article className="market-quote" key={item.symbol}>
                <div className="quote-name"><strong>{item.symbol.replace("USDT", "")}</strong><span>/ USDT</span>{item.tracked ? <b><Pulse weight="fill" />持仓/挂单</b> : null}</div>
                <div className="quote-body">
                  <span><strong>{formatPrice(live ? quote.last_price : item.last_price)}</strong><small className={positive ? "positive" : "negative"}>{positive ? "+" : ""}{change.toFixed(2)}%</small><small>{live ? `推送 ${new Date(quote.exchange_ts).toLocaleTimeString('zh-CN')}` : 'REST 快照 · 非逐笔'}</small></span>
                  <Sparkline values={item.closes || []} positive={positive} symbol={item.symbol} />
                </div>
                <div className="quote-range"><span>24h 低 {formatPrice(item.low_24h)}</span><span>高 {formatPrice(item.high_24h)}</span></div>
              </article>
            );
          })}
          {!market.items.length ? <div className="market-empty">{loading ? "正在获取实时行情…" : `行情暂不可用${error ? `：${error}` : ""}`}</div> : null}
        </div>
        {error && market.items.length > 0 ? <p role="alert" style={{ color: '#b45309', padding: '0 20px' }}>行情刷新失败，当前显示上次成功获取的数据：{error}</p> : null}
        <div className="market-foot"><span><i />公开推送 + REST 兜底，无需 API Key</span><time>统计快照 {updatedAt} · 15 秒刷新，推送价格每秒展示</time></div>
      </section>
      <MarketSearch />
    </div>
  );
}
