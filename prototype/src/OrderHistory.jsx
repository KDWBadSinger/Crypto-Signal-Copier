import { useEffect, useState } from 'react';
import { getOrderHistory } from './api';
import './order-history.css';

export const orderPrice = value => value == null || value === '' ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 8 });
export const orderTime = value => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString('sv-SE', { hour12: false }) : '未记录';

export function OrderHistory({ mode, id, revision }) {
  const [data, setData] = useState(null), [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getOrderHistory(mode, id, controller.signal).then(result => { setData(result); setError(''); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [mode, id, revision]);
  const signal = data?.signal;
  return <div className="order-history" aria-label={`${mode === 'paper' ? '模拟' : '实盘'}订单详情`}>
    <div className="order-history-heading"><h3>信号解析与操作日志</h3><span>时间按本机时区显示 · {Intl.DateTimeFormat().resolvedOptions().timeZone}</span></div>
    {error && <p role="alert">读取详情失败：{error}</p>}
    {!data && !error && <p>正在读取订单详情…</p>}
    {data?.legacy && <p className="history-note">历史订单未保存完整操作依据；仅展示实际留存记录，不补造历史日志。</p>}
    {signal?.symbol && <section className="order-signal-snapshot"><div><h4>{data.legacy ? '当前留存的信号解析（非开仓快照）' : '开仓时的信号解析'}</h4><dl>
      <div><dt>币种 / 方向</dt><dd>{signal.symbol} · {signal.side === 'long' ? '做多' : '做空'}</dd></div>
      <div><dt>入场参考</dt><dd>{orderPrice(signal.entry_low)} — {orderPrice(signal.entry_high)}</dd></div>
      <div><dt>止损</dt><dd>{signal.awaiting_protection ? '临时保护，等待博主回复' : orderPrice(signal.stop_loss)}</dd></div>
      <div><dt>止盈</dt><dd>{signal.take_profits?.length ? signal.take_profits.map(orderPrice).join(' / ') : '等待保护回复'}</dd></div>
    </dl></div><div><h4>{signal.source_name || '信号原文'}</h4><pre>{signal.raw_text}</pre></div></section>}
    <ol className="order-events">{data?.events?.map((item, index) => <li key={`${item.at}-${index}`}>
      <time>{orderTime(item.at)}</time><div><strong>{item.detail}</strong>
      <p className="event-actor">{({ signal: '信号指令', user: '用户操作', system: '系统执行 / 交易所核对' })[item.actor] || '执行记录'}</p>
      {item.sources?.map((source, i) => <blockquote key={`${source.chat_id}-${source.message_id}-${i}`}>
        <div>依据：{source.source_name || '来源未记录'}{source.message_id != null ? ` · 消息 #${source.message_id}` : ''}</div>
        <pre>{source.text || '原文未留存'}</pre><small>信息发送时间：{orderTime(source.sent_at)}</small>
      </blockquote>)}
      {!item.sources?.length && <small>{item.actor === 'user' ? '依据：用户在本机确认操作' : '依据：程序执行规则或交易所回执；无独立博主消息'}</small>}
      </div></li>)}</ol>
    {data && !data.events?.length && <p>此订单尚无已留存的操作日志。</p>}
  </div>;
}
