import { useEffect, useState } from 'react';
import { ChartLineUp, Coins, ShieldCheck, Wallet } from '@phosphor-icons/react';
import { getBitgetAccount, getLatestSignal, getSystemStatus } from './api';
import { orderPrice, orderTime } from './OrderHistory';
export const liveMoney = value => value == null ? '—' : Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function Metric({ label, value, detail, unit = 'USDT', icon: Icon, tone = '' }) {
  return <article className="paper-metric"><div><span>{label}</span><Icon size={19}/></div><strong className={tone}>{liveMoney(value)} {unit}</strong><small>{detail}</small></article>;
}
export function LiveAccountOverview({ data, curve, busy, act, onManage }) {
  const [account, setAccount] = useState(null), [latest, setLatest] = useState(null), [system, setSystem] = useState(null);
  const [error, setError] = useState(''), [signalError, setSignalError] = useState(''), [draft, setDraft] = useState(null);
  useEffect(() => {
    const controller = new AbortController(); let pending = false;
    const load = async () => {
      if (pending) return; pending = true;
      const [assets, recent, connection] = await Promise.allSettled([getBitgetAccount(controller.signal), getLatestSignal(controller.signal), getSystemStatus(controller.signal)]);
      pending = false; if (controller.signal.aborted) return;
      if (assets.status === 'fulfilled' && assets.value.environment === 'live') { setAccount(assets.value); setError(''); }
      else { setAccount(null); setError(assets.status === 'rejected' ? assets.reason.message : '当前为模拟盘连接，请在连接管理配置实盘账户。'); }
      if (recent.status === 'fulfilled') { setLatest(recent.value); setSignalError(''); }
      else { setLatest(null); setSignalError(recent.reason.status === 404 ? '' : recent.reason.message); }
      if (connection.status === 'fulfilled') setSystem(connection.value);
      else { setSystem(null); setSignalError(connection.reason.message); }
    };
    load(); const timer = setInterval(load, 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  const channels = system?.telegram_selected_channels || [];
  const selected = draft ?? data?.selected_sources ?? channels.map(c => c.id);
  return <>
    {error && <div className="live-account-error" role="alert">账户信息暂不可用：{error}</div>}
    <section className="paper-metrics" aria-label="实盘账户概要">
      <Metric label="账户权益" value={account?.account_equity_usdt} detail={account ? `交易所 · ${orderTime(account.updated_at)}` : '等待交易所账户数据'} icon={Wallet}/>
      <Metric label="累计收益" value={curve?.realized_after_fees} detail="跟单已实现收益减交易手续费，不含资金费" tone={Number(curve?.realized_after_fees) < 0 ? 'loss' : 'profit'} icon={ChartLineUp}/>
      <Metric label="浮动盈亏" value={account?.unrealised_pnl_usd} unit="USD" detail="交易所全账户浮盈，包含其他持仓" tone={Number(account?.unrealised_pnl_usd) < 0 ? 'loss' : 'profit'} icon={ChartLineUp}/>
      <Metric label="可用本金" value={account?.assets?.find(a => a.coin === 'USDT')?.available} detail="交易所 USDT 可用余额" icon={Coins}/>
    </section>
    {signalError && <p role="alert">监听状态读取失败：{signalError}</p>}
    <section className="paper-control-grid">
      <article className="paper-panel paper-latest"><div className="paper-panel-title"><div><span>最近监听信号</span><h2>{latest?.symbol || '暂无信号'}</h2></div>{latest && <span className={`direction ${latest.side}`}>{latest.side === 'long' ? '做多' : '做空'}</span>}</div>
        {latest ? <><div className="signal-price-grid"><div><span>入场参考</span><strong>{orderPrice(latest.entry_low)} – {orderPrice(latest.entry_high)}</strong></div><div><span>止损</span><strong>{latest.awaiting_protection ? '等待回复' : orderPrice(latest.stop_loss)}</strong></div><div><span>来源</span><strong>{latest.source_name}</strong></div></div><pre className="live-signal-text">{latest.raw_text}</pre><small>接收时间 {orderTime(latest.received_at)}</small></> : <p>新的 Telegram 信号会显示在这里。</p>}
      </article>
      <article className="paper-panel paper-settings"><div className="paper-panel-title"><div><span>跟单策略</span><h2>博主与执行设置</h2></div><ShieldCheck size={25}/></div>
        <div className="source-options live-sources">{channels.map(c => <label className={`source-option ${selected.includes(c.id) ? 'selected' : ''}`} key={c.id}><input type="checkbox" checked={selected.includes(c.id)} disabled={busy || !data} onChange={() => setDraft(selected.includes(c.id) ? selected.filter(id => id !== c.id) : [...selected, c.id])}/><span>{c.title}</span></label>)}</div>
        {!channels.length && <p>请在连接管理选择需要监听的 Telegram 频道。</p>}
        <div className="live-actions"><button disabled={busy || !data || draft === null} onClick={async () => { if (await act('sources', { chat_ids: draft }, '实盘跟单来源已保存，仅影响新开仓。')) setDraft(null); }}>保存跟单来源</button><button onClick={onManage}>管理 Telegram 连接</button></div>
        <p className="performance-note">只处理选中频道的新信号；取消选择后仍保护已有跟单持仓。离线消息与编辑消息不补单。</p>
      </article>
    </section>
  </>;
}
