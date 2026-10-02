import { useEffect, useState } from 'react';
import { getTakeProfitAllocation, saveTakeProfitAllocation } from './api';
import './take-profit-allocation.css';

export function TakeProfitAllocation() {
  const [values, setValues] = useState([40,40,20]);
  const [saved, setSaved] = useState(null);
  const [stage, setStage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    const abort = new AbortController();
    getTakeProfitAllocation(abort.signal).then(data => { setValues(data.percentages); setSaved(data.percentages); })
      .catch(error => { if (error.name !== 'AbortError') setMessage(error.message); });
    return () => abort.abort();
  }, []);
  const change = (index, input) => {
    const next = [...values];
    const maximum = 100 - next.slice(0,index).reduce((sum,v) => sum+v,0);
    next[index] = Math.max(0,Math.min(maximum,Number(input)));
    for (let i=index+1;i<3;i++) next[i]=Math.min(next[i],100-next.slice(0,i).reduce((sum,v)=>sum+v,0));
    setValues(next); setStage(index); setMessage('');
  };
  const total = values.reduce((sum,v)=>sum+v,0);
  const save = async () => {
    setBusy(true); setMessage('');
    try {
      const data = await saveTakeProfitAllocation(values);
      setSaved(data.percentages); setStage(0); setMessage('止盈比例已保存，仅对新单生效。');
    } catch(error) { setMessage(error.message); }
    finally { setBusy(false); }
  };
  return <section className="tp-allocation" aria-label="分批止盈设置">
    <h2>分批止盈设置</h2>
    <p>按初始成交数量分配，仅用于三档止盈。本地模拟与 UTA 自动跟单共用；已有订单不变。</p>
    <p>已保存：{saved ? saved.map((value,i)=>`TP${i+1} ${value}%`).join(' / ') : '读取中…'}</p>
    {values.map((value,index) => {
      const available = 100-values.slice(0,index).reduce((sum,v)=>sum+v,0);
      const locked = !saved || busy || index>stage;
      return <div className={`tp-step ${locked ? 'locked' : ''}`} key={index}>
        <label htmlFor={`tp-allocation-${index}`}>TP{index+1} <strong>{value}%</strong><span>{locked ? '锁定 · 请先确认上一档' : `可选 0%–${available}%`}</span></label>
        <div className="tp-track" style={{background: `linear-gradient(to right, ${locked ? '#cbd5e1' : '#2563eb'} 0%, ${locked ? '#cbd5e1' : '#2563eb'} ${available}%, #e5e7eb ${available}%, #e5e7eb 100%)`}}>
          <input id={`tp-allocation-${index}`} type="range" min="0" max="100" step="1" value={value} disabled={locked} aria-valuemax={available} aria-valuetext={`${value}%，最多 ${available}%`} onChange={event=>change(index,event.target.value)} />
        </div>
        <div className="tp-scale"><span>0%</span><span>{available<100 ? `${100-available}% 不可选` : '全部可选'}</span><span>100%</span></div>
        <button disabled={locked || stage!==index} onClick={()=>setStage(index+1)}>确认 TP{index+1}{index<2 ? `，解锁 TP${index+2}` : ''}</button>
      </div>;
    })}
    <p>合计 {total}% · 尚余 {100-total}%。0% 表示跳过该档；三档合计必须为 100% 才能保存。修改前一档后，后续档位需要重新确认。</p>
    <button className="tp-save" disabled={!saved || busy || stage!==3 || total!==100} onClick={save}>{busy ? '保存中…' : '保存止盈比例'}</button>
    {message && <p role="status">{message}</p>}
  </section>;
}
