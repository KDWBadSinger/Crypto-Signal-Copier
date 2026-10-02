import { useEffect, useRef, useState } from 'react';
import { ShieldCheck, Lightning, CaretDown } from '@phosphor-icons/react';
import { entryQuality } from './api';
import './entry-quality.css';

const money = value => value == null ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits:2 });
const percent = value => value == null ? '未知' : `${Number(value).toFixed(2)}%`;
const time = value => value ? new Date(value).toLocaleString('sv-SE', { hour12:false }) : '未记录';
const actions = { allow:'按计划', resize:'缩单', skip:'跳过' };
const fields = [
  ['max_notional','单币仓位金额上限','USDT · 杠杆后的总金额',1,10000000,100],
  ['entry_slippage_percent','开仓价格容忍','% · 相对最优对手价',0.01,3,0.05],
  ['thin_leverage_cap','偏薄盘口杠杆上限','x · 深度充足时沿用原设置',1,100,1],
  ['risk_percent','单笔压力亏损预算','% · 账户权益，非保证收益',0.1,7,0.1],
];
const advanced = [
  ['depth_percent','盘口参与比例','% · 双边分别计算',0.1,30,0.5],
  ['volume_percent','分钟成交额参与比例','% · 最近完整分钟与 5 分钟均值取小',0.1,20,0.5],
  ['exit_band_percent','退出深度价格范围','% · 当前盘口的压力情景',0.2,5,0.1],
  ['remaining_depth_percent','压力情景保留挂单','% · 假设其余挂单撤走',10,100,5],
  ['max_spread_percent','价差跳过阈值','%',0.01,3,0.05],
  ['chase_soft_percent','追价减半阈值','% · 多空按方向计算',0.1,10,0.1],
  ['chase_hard_percent','追价跳过阈值','%',0.2,15,0.1],
  ['pre_move_soft_percent','喊单前异动减半阈值','% · 前 5 根完整分钟线同向涨跌',1,20,0.5],
  ['pre_move_hard_percent','喊单前异动跳过阈值','%',2,40,0.5],
];

export function EntryDecision({ decision:d }) {
  if (!d) return null;
  return <section className="entry-decision" aria-label="入场质量依据">
    <div className="entry-decision-title"><strong>{d.mode === 'observe' ? '观察建议' : '入场决策'} · {actions[d.action]}</strong><small>{time(d.evaluated_at)}</small></div>
    <div className="entry-decision-metrics">
      <span>计划仓位<strong>{money(d.requested_notional)} USDT</strong></span><span>估算允许仓位<strong>{money(d.approved_notional)} USDT</strong></span>
      <span>估算保证金<strong>{money(d.estimated_margin)} USDT</strong></span><span>杠杆<strong>{d.requested_leverage ?? '—'}x → {d.effective_leverage ?? '—'}x</strong></span>
      <span>预计进场滑点<strong>{percent(d.entry_slippage_percent)}</strong></span><span>压力退出滑点<strong>{percent(d.stress_exit_slippage_percent)}</strong></span>
      <span>追价 / 前置异动<strong>{percent(d.chase_percent)} / {percent(d.pre_move_percent)}</strong></span><span>盘口价差<strong>{percent(d.spread_percent)}</strong></span>
    </div>
    <p>{d.reasons?.join('；')}</p>
    {d.caps && <small>各项仓位上限（USDT）：进场深度 {money(d.caps.entry_depth)} · 压力退出 {money(d.caps.exit_depth)} · 分钟成交额 {money(d.caps.volume)} · 亏损预算 {money(d.caps.risk)}</small>}
    <small>仓位金额已包含杠杆。盘口可能撤单，压力退出是当前快照估算，不保证未来能按此价格平仓。{d.mode === 'observe' ? '仅观察：上述建议没有改变订单。' : '实际成交以订单记录为准。'}</small>
  </section>;
}

export function EntryQuality({ mode }) {
  const [data,setData]=useState(null),[form,setForm]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [busy,setBusy]=useState(false),[expanded,setExpanded]=useState(null);
  const saving=useRef(false);
  useEffect(()=>{
    const controller=new AbortController(); let pending=false;
    const load=async()=>{if(pending||saving.current)return;pending=true;
      try { const next=await entryQuality(mode,null,controller.signal); if(!controller.signal.aborted&&!saving.current){setData(next);setForm(old=>old||next.settings);setError('');} }
      catch(e){if(!controller.signal.aborted)setError(e.message);} finally{pending=false;}
    }; load(); const timer=setInterval(load,10000);
    return()=>{controller.abort();clearInterval(timer);};
  },[mode]);
  const save=async e=>{e.preventDefault();setBusy(true);saving.current=true;setError('');setNotice('');
    try {const next=await entryQuality(mode,form);setData(next);setForm(next.settings);setNotice('入场规则已保存。已有持仓继续按原保护规则管理。');}
    catch(e){setError(e.message);}finally{saving.current=false;setBusy(false);}
  };
  const field=([key,label,hint,min,max])=><label key={key}>{label}<input aria-label={label} type="number" required min={min} max={max} step={key==='thin_leverage_cap'?1:'any'} value={form[key]} onChange={e=>setForm({...form,[key]:key==='thin_leverage_cap'?Number(e.target.value):e.target.value})}/><small>{hint}</small></label>;
  return <section className="entry-quality paper-panel" aria-label={`${mode==='uta'?'实盘':'模拟'}入场质量`}>
    <div className="entry-quality-heading"><div><span className="entry-quality-eyebrow"><Lightning weight="fill"/> 0.4.8 试验版 · 积极参数</span><h2>入场质量与仓位容量</h2><p>有空间就快速跟，拥挤时缩单。实盘与模拟独立设置。</p></div><span className={`entry-mode ${data?.settings.mode||''}`}><ShieldCheck size={17}/>{data?({enforce:'规则执行中',observe:'仅观察，不改订单',off:'规则已关闭'})[data.settings.mode]:'读取中'}</span></div>
    {error&&<p role="alert" className="entry-error">{error}</p>}{notice&&<p role="status" className="entry-notice">{notice}</p>}
    {form&&<form onSubmit={save}>
      <div className="entry-quality-controls"><label>运行方式<select aria-label="入场规则运行方式" value={form.mode} onChange={e=>setForm({...form,mode:e.target.value})}><option value="enforce">执行：自动缩单 / 跳过</option><option value="observe">仅观察：记录建议</option><option value="off">关闭：沿用原开仓</option></select></label><p>执行模式下，实盘用立即成交限价单（IOC），未成交部分取消，不反复追单；模拟按盘口估算。只约束新开仓。</p><button type="submit" disabled={busy}>{busy?'保存中…':'保存入场规则'}</button></div>
      <div className="entry-quality-fields">{fields.map(field)}</div>
      <details className="entry-advanced"><summary>调整深度、成交额与追价参数 <CaretDown size={14}/></summary><div className="entry-quality-fields">{advanced.map(field)}</div></details>
    </form>}
    <p className="entry-scope-note">容量取账户预算、双边盘口、近期成交额与金额上限中的较小值。使用交易所公开数据，不按市值猜金额；缺失原消息时间时“喊单前异动”标为未知。保留全仓账户模式，本版不自动切换逐仓。</p>
    <div className="entry-section-heading"><h3>最近入场决策</h3><small>包含跳过的信号 · 最近 10 条</small></div>
    {!data?.decisions?.length?<div className="entry-empty">等待新的跟单信号。这里会显示原计划、允许金额和限制原因。</div>:<div className="entry-decision-list">{data.decisions.slice(0,10).map(d=><div key={d.signal_id}>
      <button type="button" className="entry-decision-row" aria-expanded={expanded===d.signal_id} onClick={()=>setExpanded(expanded===d.signal_id?null:d.signal_id)}>
        <span><strong>{d.symbol}</strong><small>{d.source_name} · {time(d.evaluated_at)}</small></span><span className={`entry-action ${d.action}`}>{d.mode==='observe'?'建议 ':''}{actions[d.action]}</span><span className="entry-amount">{money(d.requested_notional)} → <strong>{money(d.approved_notional)}</strong> USDT</span><CaretDown size={16}/>
      </button>{expanded===d.signal_id&&<EntryDecision decision={d}/>}</div>)}</div>}
    <details className="entry-source-review"><summary>博主跟随质量 <small>真实样本 · 不自动封禁</small></summary>
      <p>只统计本模式最近 1,000 条受评估信号。已平仓净收益扣交易手续费，不含资金费；模拟与实盘分开。至少 20 笔平仓才显示质量分，计算为 50 + 平均净仓位收益率（%）× 10，限制在 0–100 分。分数不证明博主可信，也不预测盈利。</p>
      {!data?.sources?.length?<div className="entry-empty">尚无可评估样本。</div>:<div className="entry-source-table"><table><thead><tr><th>博主 / 质量分</th><th>信号 / 已平仓</th><th>已平仓净收益</th><th>平均前置异动</th><th>平均成交偏离参考</th><th>改文 / 删文</th></tr></thead><tbody>{data.sources.map(s=><tr key={s.source_key}><td><strong>{s.source_name}</strong><small>{s.score==null?s.verdict:`${s.score} / 100 · ${s.verdict}`}</small></td><td>{s.signals} / {s.closed}<small>缩单 {s.resized} · 跳过 {s.skipped}</small></td><td className={Number(s.net)<0?'loss':'profit'}>{money(s.net)} USDT<small>平均 {percent(s.average_net_return_percent)}</small></td><td>{percent(s.average_pre_move_percent)}<small>{s.pre_move_samples} 个可核验样本</small></td><td>{percent(s.average_entry_deviation_percent)}</td><td>{s.edited_messages} / {s.deleted_messages}<small>仅已观察到的信号原消息</small></td></tr>)}</tbody></table></div>}
      <small>删文通知可能缺失；0 条表示未观察到。价格提前异动、编辑或删除本身不能证明老鼠仓。未成交与被跳过信号没有虚构收益。</small>
    </details>
  </section>;
}
