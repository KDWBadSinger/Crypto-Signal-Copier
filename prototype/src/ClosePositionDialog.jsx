import { useState } from 'react';
import './close-position-dialog.css';

export function ClosePositionDialog({live=false,target,busy,onCancel,onConfirm}) {
  const [confirmation,setConfirmation]=useState('');
  const required='确认实盘平仓';
  return <div className="close-overlay"><section className="close-dialog" role="dialog" aria-modal="true" aria-labelledby="close-heading">
    <h2 id="close-heading">{live?'实盘':'模拟'} · {target.all?'全部平仓':`${target.symbol} 手动平仓`}</h2>
    <p>{target.all?'平掉本程序当前跟单持仓，并暂停新开仓。':'按市价平掉这笔跟单的全部剩余仓位，其他仓位不受影响。'}</p>
    <p>{live?'仅处理本程序 UTA 跟单仓位，不操作交易所其他手动持仓。实际成交价、手续费和滑点以交易所回执为准，全部平仓是逐笔执行，并非原子操作。':'采用当前公开行情模拟成交并计入手续费，不向交易所发送订单。全部平仓也会撤销本地待入场订单，但不结束模拟 ID。'}</p>
    {live&&<label>请输入「{required}」<input autoFocus value={confirmation} onChange={e=>setConfirmation(e.target.value)} disabled={busy}/></label>}
    <div><button disabled={busy} onClick={onCancel}>取消</button><button className="close-danger" disabled={busy||(live&&confirmation!==required)} onClick={onConfirm}>{busy?'处理中…':'确认平仓'}</button></div>
  </section></div>;
}
