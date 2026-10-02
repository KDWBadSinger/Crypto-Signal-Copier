import { useEffect, useState } from 'react';

async function request(path, body) {
  const response = await fetch(path, body ? { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) } : {});
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '请求失败');
  return data;
}

export function SystemPanel() {
  const [diagnostics, setDiagnostics] = useState(null), [license, setLicense] = useState(null);
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const refresh = async () => { const [d,l] = await Promise.all([request('/api/system/diagnostics'), request('/api/account/license')]); setDiagnostics(d); setLicense(l); };
  useEffect(() => { refresh().catch(error => setNotice(error.message)); }, []);
  const action = async register => {
    setBusy(true); setNotice('');
    try { const result = await request('/api/account/login', {username,password,register}); setPassword(''); setNotice(result.detail || (result.valid ? '账号已登录并绑定本设备' : '登录完成，请检查授权状态')); await refresh(); }
    catch(error) { setNotice(error.message); } finally { setBusy(false); }
  };
  const downloadDiagnostics = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(diagnostics,null,2)], {type:'application/json'}));
    const anchor = document.createElement('a'); anchor.href=url; anchor.download='copier-diagnostics.json'; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  return <section className="system-panel">
    <h2>账号授权与系统维护</h2><p>账号服务只接收登录信息和随机设备标识，不上传交易所密钥或 Telegram 会话。</p>
    <p><strong>{license?.mode || '读取中…'}</strong> · {license?.valid ? `已授权至 ${new Date(license.expires_at*1000).toLocaleString('zh-CN')}` : license?.detail}</p>
    <small>账号服务：{license?.server}。本地验收先启动账号服务；是否强制授权由部署配置控制。到期仅阻止新开单，不强制平仓。</small>
    <div className="system-login"><label>账号<input value={username} onChange={event=>setUsername(event.target.value)} autoComplete="username" /></label><label>密码<input type="password" value={password} onChange={event=>setPassword(event.target.value)} autoComplete="current-password" /></label><button disabled={busy||!username||password.length<12} onClick={()=>action(false)}>登录并绑定设备</button><button disabled={busy||!username||password.length<12} onClick={()=>action(true)}>注册试用</button><button disabled={busy} onClick={async()=>{try{await request('/api/account/logout',{});await refresh();}catch(error){setNotice(error.message);}}}>退出</button></div>
    {notice && <p role="status">{notice}</p>}
    {diagnostics && <><hr/><h3>运行诊断 · {diagnostics.version}</h3><p>账本：{diagnostics.database_ok?'校验正常':'需要检查'} · Telegram：{diagnostics.telegram_connected?'已连接':'未连接'} · 行情：{diagnostics.market.detail}</p><p>公开行情重连次数 {diagnostics.market.reconnects} · 拒绝无效行情 {diagnostics.market.rejected_ticks} · 实盘资金执行：禁用</p><div className="system-actions"><button onClick={()=>refresh().catch(error=>setNotice(error.message))}>刷新诊断</button><button onClick={downloadDiagnostics}>导出脱敏诊断</button><a href="/api/system/backup" download="copier-ledger-backup.sqlite3">下载账本备份</a></div><small>账本备份包含频道消息和交易记录，请妥善保存，不要公开上传；不包含 API 密钥和 Telegram 登录会话。升级前也会在本机数据目录自动备份。</small></>}
  </section>;
}
