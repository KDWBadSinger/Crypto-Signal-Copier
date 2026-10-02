import { useEffect, useState } from 'react';
import { getConnections, getTelegramChannels, getTelegramMessages, saveTelegramChannels, sendTelegramCode, signInTelegram } from './api';

export function TelegramSetup({ overview, onOverview }) {
  const [stage, setStage] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [channels, setChannels] = useState([]);
  const [selected, setSelected] = useState([]);
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');

  const run = async (action) => {
    setBusy(true); setFeedback('');
    try { await action(); } catch (error) { setFeedback(error.message); }
    finally { setBusy(false); }
  };
  const loadChannels = async () => {
    const items = await getTelegramChannels();
    setChannels(items); setSelected(items.filter(item => item.selected).map(item => item.id));
  };
  useEffect(() => {
    if (overview?.telegram?.connected) run(loadChannels);
  }, [overview?.telegram?.connected]);
  useEffect(() => {
    const controller = new AbortController();
    const load = () => getTelegramMessages(controller.signal).then(setMessages).catch(error => {
      if (error.name !== 'AbortError') setFeedback(error.message);
    });
    load(); const interval = setInterval(load, 2000);
    return () => { controller.abort(); clearInterval(interval); };
  }, []);

  const loginResult = async (result) => {
    setStage(result.state);
    if (result.state === 'authorized') {
      setCode(''); setPassword(''); onOverview(await getConnections()); await loadChannels();
      setFeedback('登录成功，请勾选并保存要监听的频道。');
    } else if (result.state === 'password_required') setFeedback('该账号开启了两步验证，请输入 Telegram 两步验证密码。');
    else setFeedback('验证码已发送，请查看 Telegram 应用中的登录通知。');
  };
  return <section className="connection-card telegram-workflow">
    <h2>Telegram 登录与频道订阅</h2>
    <p>先保存上方 API 配置，再登录并选择当前账号已加入的频道。仅处理选择完成后收到的新消息。</p>
    {!overview?.telegram?.connected && <div className="telegram-login">
      <button type="button" className="save-connection" disabled={busy || !overview?.telegram?.configured} onClick={() => run(async () => loginResult(await sendTelegramCode()))}>发送 / 重发验证码</button>
      {(stage === 'code_sent' || stage === 'password_required') && <form onSubmit={event => { event.preventDefault(); run(async () => loginResult(await signInTelegram(stage === 'password_required' ? { password } : { code }))); }}>
        <label className="connection-field"><span>{stage === 'password_required' ? '两步验证密码' : 'Telegram 验证码'}</span>
          <input type="password" autoComplete="off" required value={stage === 'password_required' ? password : code} onChange={event => stage === 'password_required' ? setPassword(event.target.value) : setCode(event.target.value)} />
        </label><button className="save-connection" disabled={busy}>确认登录</button>
      </form>}
    </div>}
    {overview?.telegram?.connected && <>
      <div className="telegram-actions"><button type="button" disabled={busy} onClick={() => run(loadChannels)}>刷新频道列表</button><span>已选择 {selected.length} 个频道 / 群组</span></div>
      <div className="telegram-channels">{channels.map(channel => <label key={channel.id}>
        <input type="checkbox" checked={selected.includes(channel.id)} onChange={event => setSelected(current => event.target.checked ? [...current, channel.id] : current.filter(id => id !== channel.id))} />
        <span>{channel.title}<small>{channel.id}</small></span>
      </label>)}{!channels.length && <p>暂无频道；先在 Telegram 中加入频道，再刷新列表。</p>}</div>
      <button className="save-connection" disabled={busy} onClick={() => run(async () => { onOverview(await saveTelegramChannels(selected)); setFeedback('监听频道已保存并立即生效。'); })}>保存监听频道</button>
    </>}
    {feedback && <p role="status" className="connection-detail">{feedback}</p>}
    <h3>实时接收记录</h3><p>市价开仓消息可以与保护回复分开发送。已启用的本地模拟及 UTA 实盘会先校验行情、带临时止损开仓，再等待同频道回复补齐保护；未启用时仅查看，不下单。</p>
    <div className="telegram-messages">{messages.map(message => <article key={`${message.chat_id}:${message.message_id}`}>
      <header><strong>{message.source_name}</strong><span>{new Date(message.received_at).toLocaleString()} · {{ parsed: '已解析', unparsed: '未形成交易信号', stale: '已过期', error: '处理失败' }[message.status] || message.status}</span></header>
      <pre>{message.text}</pre>{message.detail && <small>{message.detail}</small>}
    </article>)}{!messages.length && <p>尚未收到消息。登录并保存频道后，这里会显示实时接收记录。</p>}</div>
  </section>;
}
