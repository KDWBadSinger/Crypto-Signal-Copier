import { useEffect, useState } from 'react';
import { getAccountPerformance } from './api';

export function AccountPerformance() {
  const [data, setData] = useState(null), [error, setError] = useState('');
  const mode = 'equity'; const [days, setDays] = useState('30');
  useEffect(() => {
    const controller = new AbortController(); let pending = false;
    const load = async () => { if (pending) return; pending = true; try { setData(await getAccountPerformance(controller.signal)); setError(''); } catch(e) { if (!controller.signal.aborted) setError(e.message); } finally { pending = false; } };
    load(); const timer = setInterval(load, 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  const cutoff = Date.now()-Number(days)*86400000;
  const points = (mode === 'equity' ? data?.equity_points || [] : data?.follow_points || []).filter(p => days === 'all' || Date.parse(p.observed_at) >= cutoff);
  const values = points.map(p => Number(p.equity)), min = Math.min(...values), max = Math.max(...values);
  const x = i => 30+(points.length === 1 ? 360 : i/(points.length-1)*720);
  const y = n => max === min ? 95 : 160-(n-min)/(max-min)*130;
  return <section className="account-performance" aria-label="真实账号收益曲线">
    <h2>真实账户 USDT 权益观察（非跟单收益）</h2><select aria-label="曲线时间范围" value={days} onChange={e => setDays(e.target.value)}><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="all">全部观测</option></select>
    {error && <p role="alert">读取失败：{error}</p>}
    <p>{mode === 'follow' ? data?.detail || '正在读取收益状态…' : '从连接实盘只读 API 后开始记录每日 UTC 最后一次有效权益；包含充值、提现、手动交易和浮盈，不代表本程序跟单收益。离线日期留空，不补造历史。'}</p>
    {points.length ? <><svg viewBox="0 0 780 200" role="img" aria-label="真实账户每日权益观测曲线"><line x1="30" y1="170" x2="750" y2="170" stroke="#dbe3ef" />{points.map((p,i) => <g key={p.day}>{i > 0 && Date.parse(p.day)-Date.parse(points[i-1].day) <= 86400000 && <line x1={x(i-1)} y1={y(values[i-1])} x2={x(i)} y2={y(values[i])} stroke="#5376df" strokeWidth="2" />}<circle cx={x(i)} cy={y(values[i])} r="6" fill="#5376df" tabIndex="0"><title>{p.day} · 权益 {p.equity} USDT · 观测 {p.observed_at}</title></circle></g>)}</svg><small>{points[0].day} — {points.at(-1).day} · 鼠标指向数据点查看权益</small><details><summary>查看每日数值</summary>{points.map(p => <p key={p.day}>{p.day}：{p.equity} USDT</p>)}</details></> : <div className="account-curve-empty">{mode === 'follow' ? '尚无已验证的实盘跟单收益数据，不绘制虚假零收益曲线' : '尚无真实账户权益观测，请在连接管理连接实盘只读 API'}<br /><small>Bitget 模拟盘与程序内模拟盘的数据不会混入此处。</small></div>}
  </section>;
}
