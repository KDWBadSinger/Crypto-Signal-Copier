import { Fragment, useEffect, useState } from 'react';
import { AccountPerformance } from './AccountPerformance';
import { LiveAccountOverview, liveMoney } from './LiveAccountOverview';
import { OrderHistory, orderPrice, orderTime } from './OrderHistory';
import { EntryQuality } from './EntryQuality';
import './paper-account.css';
import { ClosePositionDialog } from './ClosePositionDialog';
import { utaRequest } from './api';
import './uta-execution.css';
const names={prepared:'准备中',submitting:'提交中',awaiting_fill:'等待成交',protecting:'确认保护',protected:'保护正常',managing:'管理中',needs_reconciliation:'等待核对',closed:'已结算',rejected:'已拒绝'};
export function UtaExecution({ onManage }){
  const [expanded,setExpanded]=useState(null);
  const [data,setData]=useState(null),[form,setForm]=useState(null),[curve,setCurve]=useState(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[confirm,setConfirm]=useState('');
  const [days,setDays]=useState(90),[hover,setHover]=useState(null);
  const [closeTarget,setCloseTarget]=useState(null);
  const closePositions=async()=>{if(await act('close-positions',{position_id:closeTarget.all?null:closeTarget.signal_id,confirmation:'确认实盘平仓'},'平仓请求已处理，请查看各单成交与核对状态；等待核对不代表已平仓。'))setCloseTarget(null);};
  useEffect(()=>{let stopped=false,pending=false;const load=async()=>{if(pending)return;pending=true;try{
    const [status,profit]=await Promise.all([utaRequest('execution'),utaRequest('performance')]);
    if(!stopped){setData(status);setForm(old=>old||status.limits);setCurve(profit);}
  }catch(e){if(!stopped)setError(e.message);}finally{pending=false;}};load();const timer=setInterval(load,5000);return()=>{stopped=true;clearInterval(timer);};},[]);
  const act=async(path,body,message)=>{setBusy(true);setError('');setNotice('');try{setData(await utaRequest(path,body));setNotice(message);return true;}catch(e){setError(e.message);return false;}finally{setBusy(false);}};
  const points=(curve?.points||[]).filter(p=>days===0||Date.parse(p.day)>=Date.now()-days*86400000);
  const values=points.map(p=>Number(p.cumulative)),lo=Math.min(0,...values),hi=Math.max(0,...values);
  const x=i=>points.length===1?380:30+i*700/(points.length-1),y=v=>160-(v-lo)*130/(hi-lo||1);
  return <div className="live-account-page"><header className="paper-header"><div><span className="paper-eyebrow">BITGET UTA · REAL MONEY</span><h1>实盘操作</h1><p>真实资金自动跟单 · 全仓 USDT 永续 · 每 5 秒刷新账户与订单</p></div></header><section className="uta-panel live-operation" aria-label="UTA 实盘自动跟单"><section className="paper-panel live-configuration">
    <div className="uta-heading"><div><small>REAL MONEY · 仅影响新订单</small><h2>实盘开仓设置</h2></div><strong>{!data?'正在读取':data.enabled?(data.ready?'运行中':'恢复核对中'):'新开仓已停止'}</strong></div>
    <details className="live-rules"><summary>执行与保护规则</summary><p>仅跟随已选择频道的实时消息。市价信号通过行情校验后立即开仓，临时止损预算为初始保证金的 100%（含预估双边手续费）；5 分钟未收到有效完整保护回复则请求市价退出。三档止盈按初始成交数量及开仓时保存的比例执行（币种杠杆页设置）。历史消息不补单。</p>
    <p>开仓参考价仅允许十进制移位纠错，纠正后与行情偏差超过 10% 拒单。关闭应用或断网时无法定时平仓；交易所止损保留，重连后先核对再处理超时。止损不是实际亏损上限。</p></details>
    {form&&<form className="uta-risk" onSubmit={e=>{e.preventDefault();act('risk',form,'风险参数已保存，仅影响新订单。');}}>
      <label>每单保证金 / 实时净值<input aria-label="实盘保证金比例" type="number" min="0.1" max="7" step="0.1" required value={form.position_percent} onChange={e=>setForm({...form,position_percent:e.target.value})}/><small>% · 硬上限 7%</small></label>
      <div><strong>币种杠杆自动选择</strong><p>单独配置优先；未配置采用“币种杠杆”页保存的最大杠杆比例（向下取整），0% 暂停未配置币种新开仓。</p></div>
      <label>同时未平仓仓位数量上限<input aria-label="实盘仓位上限" type="number" min="1" max="6" step="1" required value={form.max_positions} onChange={e=>setForm({...form,max_positions:Number(e.target.value)})}/><small>个 · 包含账户其他持仓</small></label><button disabled={busy}>保存实盘参数</button>
    </form>}
    <div className="uta-warning">全仓风险：保证金比例不是最大亏损比例。不要手动交易正在跟单的币种。低于最小下单量会拒单，不会扩大仓位。止损失效且仓位归属确认时，程序会请求只减仓退出。</div>
    {!data?.enabled&&<details className="uta-activation"><summary>启用真实资金自动交易</summary><p>确认后使用已保存 API 真实下单，重启后恢复。关闭应用期间不能接收博主管理消息；已提交的交易所保护单继续执行。仅使用可承受亏损的资金。</p><label>输入「我确认启用实盘自动跟单」<input value={confirm} autoComplete="off" onChange={e=>setConfirm(e.target.value)}/></label><button disabled={busy||data?.desktop_only||confirm!=='我确认启用实盘自动跟单'} onClick={()=>act('activate',{confirmation:confirm},'已启用，只处理之后收到的新信号。')}>确认并启用实盘</button>{data?.desktop_only&&<p>只能从 EXE 桌面窗口启用，浏览器预览禁止激活实盘。</p>}</details>}
    <div className="uta-actions"><button disabled={busy||!data?.enabled} onClick={()=>act('pause',{},'已停止新开仓；已有仓位继续管理。')}>停止新开仓</button><button disabled={busy||!data?.management_authorized||data?.desktop_only} onClick={()=>act('reconcile',{},'已核对，请查看订单详情。')}>立即核对 / 恢复</button><small>最近核对：{data?.last_check?new Date(data.last_check).toLocaleString():'尚未启用'}</small></div>
    {(error||data?.error)&&<p role="alert" className="uta-error">{error||data.error}</p>}{notice&&<p role="status">{notice}</p>}<p>{data?.detail}</p>
    </section><EntryQuality mode="uta"/><LiveAccountOverview data={data} curve={curve} busy={busy} act={act} onManage={onManage}/>
    <section className="paper-panel paper-trades"><div className="paper-panel-title"><div><span>真实交易 · 本程序归属的跟单订单</span><h2>实际订单与持仓</h2></div><button className="position-close" disabled={busy||data?.desktop_only||!data?.management_authorized||!data?.workflows?.some(r=>!["closed","rejected"].includes(r.state))} onClick={()=>setCloseTarget({all:true})}>全部平仓</button></div>
      {closeTarget&&<ClosePositionDialog live target={closeTarget} busy={busy} onCancel={()=>setCloseTarget(null)} onConfirm={closePositions}/>}
      {!data?.workflows?.length?<div className="paper-empty">尚无实盘执行记录，启用后收到的新信号才会进入执行。</div>:<div className="paper-table-wrap"><table><thead><tr><th>币种 / 方向 / 杠杆</th><th>状态</th><th>成交 / 参考</th><th>数量 / 开仓保证金</th><th>止损 / 全部止盈</th><th>已实现净收益</th><th>操作</th><th>详情</th></tr></thead><tbody>{data.workflows.map(r=><Fragment key={r.signal_id}><tr>
        <td><strong>{r.symbol}</strong><span className={`direction compact ${r.side}`}>{r.side==='long'?'多':'空'} <b className="order-leverage">{r.leverage?`${r.leverage}x`:'—'}</b></span><small>{orderTime(r.created_at)}</small></td>
        <td><span className={`trade-status ${r.state==='closed'?'closed':r.state==='rejected'?'rejected':'open'}`}>{r.state==='closed'&&r.entry_price?'已平仓':names[r.state]||r.state}</span><small className="live-row-detail">{r.detail}</small></td>
        <td><strong>{orderPrice(r.entry_price)}</strong><small>博主参考 {orderPrice(r.entry_low)}</small></td>
        <td><strong>{r.remaining_qty??'—'}</strong><small>{liveMoney(r.margin)} USDT</small></td>
        <td><strong className="loss">SL {orderPrice(r.current_stop)}</strong>{r.awaiting_protection?<small>等待保护回复 · {orderTime(r.protection_deadline)}</small>:r.take_profits?.map((tp,i)=><small key={i}>TP{i+1} {orderPrice(tp)}{r.take_profits.length===3?` · ${r.tp_percentages[i]}%`:''}{r.protection_states?.[`tp${i+1}`]==='success'?' · 已触发':''}</small>)}</td>
        <td><strong className={Number(r.realized_after_fees)<0?'loss':'profit'}>{liveMoney(r.realized_after_fees)}</strong><small>USDT · 已核对成交</small></td>
        <td>{r.state==='closed'?<span className="position-closed">{r.entry_price?'已平仓':'未成交'}</span>:r.state!=='rejected'&&<button className="position-close" disabled={busy||data.desktop_only||!data.management_authorized||r.manual_close_requested} onClick={()=>setCloseTarget(r)}>{r.manual_close_requested?'平仓核对中':'手动平仓'}</button>}</td>
        <td><button className="order-detail-toggle" aria-expanded={expanded===r.signal_id} onClick={()=>setExpanded(expanded===r.signal_id?null:r.signal_id)}>{expanded===r.signal_id?'收起':'展开'}</button></td>
      </tr>{expanded===r.signal_id&&<tr><td colSpan={8} className="history-cell"><OrderHistory mode="uta" id={r.signal_id} revision={r.updated_at}/></td></tr>}</Fragment>)}</tbody></table></div>}
    </section>
    <div className="uta-profit"><div className="uta-heading"><h3>跟单收益曲线 · 已实现收益</h3><select aria-label="收益曲线时间范围" value={days} onChange={e=>{setDays(Number(e.target.value));setHover(null);}}>{[7,30,90,365,0].map(d=><option key={d} value={d}>{d===0?'全部':`最近 ${d} 天`}</option>)}</select></div><strong>{curve?.realized_after_fees??'—'} USDT</strong><p>{curve?.detail}</p>
      {points.length?<><svg viewBox="0 0 760 190" role="img" aria-label="实盘跟单已实现收益曲线"><line x1="30" y1={y(0)} x2="730" y2={y(0)} stroke="#dbe3ef"/><polyline points={points.map((p,i)=>`${x(i)},${y(Number(p.cumulative))}`).join(' ')} fill="none" stroke="#4669c9" strokeWidth="2"/>{points.map((p,i)=><circle key={p.day} cx={x(i)} cy={y(Number(p.cumulative))} r="5" fill="#4669c9" tabIndex="0" onMouseEnter={()=>setHover(p)} onFocus={()=>setHover(p)}><title>{p.day} · 当日 {p.daily} · 累计 {p.cumulative} USDT</title></circle>)}</svg><p>{hover?`${hover.day} UTC · 当日 ${hover.daily} · 累计 ${hover.cumulative} USDT`:'移动鼠标至数据点查看每日收益'}</p></>:<p>暂无已核对成交，不生成虚构收益曲线。</p>}
    </div><AccountPerformance/>
  </section></div>;
}
