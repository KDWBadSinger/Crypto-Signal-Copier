import { useState } from 'react';
import { getUtaReadiness } from './api';

export function UtaReadiness() {
  const [result,setResult] = useState(null),[busy,setBusy] = useState(false),[error,setError] = useState('');
  const check = async () => { setBusy(true); setError(''); setResult(null); try { setResult(await getUtaReadiness()); } catch(e) { setError(e.message); } finally { setBusy(false); } };
  return <section className="connection-card"><h2>UTA 实盘接入检查</h2><p>只读检查账户类型、持仓模式和 API 权限；不会下单、调整杠杆或切换持仓模式。</p>
    <button onClick={check} disabled={busy}>{busy ? '检查中…' : '检查 UTA 账户'}</button>
    {error && <p role="alert">{error}</p>}
    {result && <><p>此处仅检查连接；请到信号追踪单独设置参数并启用实盘。</p>{result.hold_mode && <p>持仓模式：{result.hold_mode === 'hedge_mode' ? '双向持仓' : '单向持仓'}</p>}<ul>{result.checks.map(c => <li key={c.name}>{c.ok ? '通过' : '未满足'} · {c.name}</li>)}</ul><ul>{result.blockers.map(b => <li key={b}>{b}</li>)}</ul></>}
    <p>API 只在本机输入；跟单不需要提现权限。现有手动仓位不会被本功能修改。</p>
  </section>;
}
