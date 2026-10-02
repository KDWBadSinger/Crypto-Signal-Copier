import { useEffect, useState } from 'react';
import { getPaperReport, getPaperReports } from './api';

const money = value => Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const duration = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 86400)}天 ${Math.floor(value % 86400 / 3600)}小时 ${Math.floor(value % 3600 / 60)}分`;
};

function EquityChart({ points, initial }) {
  const [range, setRange] = useState([0, Math.max(points.length - 1, 0)]);
  const [hover, setHover] = useState(null);
  const last = Math.max(0, points.length - 1);
  const start = Math.min(range[0], last), end = Math.max(start, Math.min(range[1], last));
  const visible = points.slice(start, end + 1);
  const values = visible.filter(p => p.equity !== null).map(p => Number(p.equity));
  const min = Math.min(Number(initial), ...values), max = Math.max(Number(initial), ...values);
  const padding = Math.max((max-min) * 0.15, 1), low = min-padding, high = max+padding;
  const x = index => 75 + (visible.length > 1 ? index / (visible.length-1) : 0.5) * 790;
  const y = value => 225 - (Number(value)-low)/(high-low)*190;
  let newSegment = true;
  const path = visible.map((point, index) => {
    if (point.equity === null) { newSegment = true; return ''; }
    const segment = `${newSegment ? 'M' : 'L'} ${x(index)} ${y(point.equity)}`;
    newSegment = false; return segment;
  }).join(' ');
  const selected = hover === null ? visible.at(-1) : visible[hover];
  const zoom = direction => {
    const width = end-start;
    if (direction > 0) setRange([start, Math.max(start, end-Math.max(1, Math.ceil(width/3)))]);
    else setRange([Math.max(0, start-1), Math.min(last, end+Math.max(1, Math.ceil(width/2)))]);
    setHover(null);
  };
  return <div className="paper-performance-chart">
    <div className="performance-chart-tools"><span>账户权益 · USDT</span><button onClick={() => zoom(1)} disabled={end === start}>放大</button><button onClick={() => zoom(-1)}>缩小</button><button onClick={() => { setRange([0, last]); setHover(null); }}>全部日期</button></div>
    <svg viewBox="0 0 900 265" role="img" aria-label="每日账户权益曲线，使用下方日期滑块缩放，悬停查看余额与收益"
      onPointerMove={event => { const rect = event.currentTarget.getBoundingClientRect(); const ratio = (event.clientX-rect.left)/rect.width; setHover(Math.max(0, Math.min(visible.length-1, Math.round((ratio*900-75)/790*(visible.length-1))))); }}
      onPointerLeave={() => setHover(null)}>
      {[0,1,2,3].map(i => { const value = low+(high-low)*i/3; return <g key={i}><line x1="75" x2="865" y1={y(value)} y2={y(value)} stroke="#e5eaf2"/><text x="65" y={y(value)+4} textAnchor="end">{money(value)}</text></g>; })}
      <path d={path} fill="none" stroke="#246bfd" strokeWidth="3" />
      {visible.map((point, index) => point.equity !== null && <circle key={point.day} cx={x(index)} cy={y(point.equity)} r={hover === index ? 6 : 3} fill="#246bfd" />)}
      {hover !== null && <line x1={x(hover)} x2={x(hover)} y1="25" y2="230" stroke="#6a7b94" strokeDasharray="4 4"/>}
      <text x="75" y="252">{visible[0]?.day}</text><text x="865" y="252" textAnchor="end">{visible.at(-1)?.day}</text>
    </svg>
    <div className="performance-tooltip" aria-live="polite">{selected ? <><strong>{selected.day}（UTC）</strong>{selected.equity === null ? <span>当日无在线行情记录，不填造余额与收益</span> : <><span>余额 <b>{money(selected.balance)} USDT</b></span><span>权益 <b>{money(selected.equity)} USDT</b></span><span>当日收益 <b>{selected.daily_profit === null ? '数据中断，无法计算' : `${money(selected.daily_profit)} USDT`}</b></span><span>累计收益 <b>{money(selected.profit)} USDT</b></span><small>最后观测：{selected.observed_at?.replace('T',' ').slice(0,19)} UTC</small></>}</> : '创建模拟后开始积累每日记录'}</div>
    <div className="performance-range"><label>起始日期<input aria-label="收益曲线起始日期" type="range" min="0" max={last} value={start} onChange={event => { setRange([Number(event.target.value), Math.max(end, Number(event.target.value))]); setHover(null); }}/></label><label>结束日期<input aria-label="收益曲线结束日期" type="range" min="0" max={last} value={end} onChange={event => { setRange([Math.min(start, Number(event.target.value)), Number(event.target.value)]); setHover(null); }}/></label></div>
  </div>;
}

export function PaperPerformance({ revision }) {
  const [report, setReport] = useState(null), [archives, setArchives] = useState([]), [selected, setSelected] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getPaperReport(selected, controller.signal), getPaperReports(controller.signal)]).then(([next, history]) => { setReport(next); setArchives(history); setError(''); }).catch(err => { if (err.name !== 'AbortError') setError(err.message); });
    return () => controller.abort();
  }, [selected, revision]);
  const account = report?.account;
  return <section className="paper-panel paper-performance">
    <div className="paper-panel-title"><div><span>独立模拟档案 · 不需要交易所 API</span><h2>{account?.lifecycle === 'stopped' ? '跟单结算报告' : '跟单收益曲线'}</h2></div><select aria-label="选择模拟报告" value={selected} onChange={event => setSelected(event.target.value)}><option value="">当前模拟</option>{archives.map(item => <option key={item.simulation_id} value={item.simulation_id}>{item.simulation_id} · 已结算</option>)}</select></div>
    {error && <p role="alert">{error}</p>}
    {account?.initialized && <><div className="performance-summary"><div><small>模拟 ID</small><strong>{account.simulation_id}</strong></div><div><small>跟单跨度</small><strong>{duration(account.elapsed_seconds)}</strong></div><div><small>累计在线运行</small><strong>{duration(account.active_seconds)}</strong></div><div><small>{account.lifecycle === 'stopped' ? '结算余额' : '当前权益'}</small><strong>{money(account.equity)} USDT</strong></div><div><small>累计净收益 / 收益率</small><strong>{money(Number(account.equity)-Number(account.initial_balance))} / {money(account.return_percent)}%</strong></div><div><small>已付模拟手续费</small><strong>{money(account.fees_paid)} USDT</strong></div></div>
      <p className="performance-note">开始：{new Date(account.started_at).toLocaleString('zh-CN')} · {account.stopped_at ? `结算：${new Date(account.stopped_at).toLocaleString('zh-CN')}` : '运行时持续跟单；关闭应用后暂停，重新打开只继续处理新信号，不追补离线交易'}</p>
      <EquityChart key={`${account.simulation_id}:${report.daily.length}`} points={report.daily} initial={account.initial_balance} />
      <p className="performance-note">按 UTC 日汇总每日最后一次有效估值，今天为未完结数据。余额不含浮盈，权益包含浮盈；收益已扣模拟手续费。非全天在线时，记录不等于精确午夜结算；缺失日期留空。模拟不复现离线期间成交、资金费率、订单簿滑点或交易所强平，不能视为实盘收益承诺。</p>
    </>}
  </section>;
}
