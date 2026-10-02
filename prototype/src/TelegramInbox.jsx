import { useEffect, useRef, useState } from 'react';
import { ArrowsClockwise, CheckCircle, TelegramLogo } from '@phosphor-icons/react';
import { getTelegramInbox, syncTelegramHistory, reparseTelegramCache } from './api';
import './telegram-inbox.css';

const statuses = { duplicate:'重复信号 · 不重复下单', invalid:'参数冲突 · 不下单', management: '持仓管理预览', managed: '持仓已管理', management_rejected: '管理未执行', waiting: '等待止盈止损', received: '接收中', parsed: '完整交易信号', unparsed: '未形成信号', media: '附件消息', stale: '过期信号', error: '处理失败' };
const executionStatuses = { pending_review: '尚未执行', approved_dry_run: '试运行', submitted: '已提交交易所', rejected: '执行失败', ignored: '已忽略', unknown: '结果待核对', submitting: '提交中 / 待核对' };
const time = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚无记录';

function MessageCard({ message, onViewSignal }) {
  const [photo, setPhoto] = useState(false);
  const [photoError, setPhotoError] = useState(false);
  const parsed = message.parsed_signal;
  return <article className={`inbox-message ${message.status === 'parsed' ? 'is-signal' : ''}`}>
    <header><strong>{message.source_name}</strong><span>{time(message.sent_at || message.received_at)} · #{message.message_id}</span></header>
    <div className="inbox-badges"><b className={message.status === 'parsed' ? 'signal-badge' : ''}>{statuses[message.status] || message.status}</b>
      {message.origin === 'history' && <b>历史补读 · 不跟单</b>}{message.origin === 'edit' && <b>消息已编辑 · 不重下单</b>}
      {message.reply_to_message_id && <b>回复 #{message.reply_to_message_id}</b>}
      {message.duplicate_of && <b>归并至开单 #{message.duplicate_of}</b>}
      {message.display_only && <b>旧消息重新解析 · 不补单</b>}
    </div>
    {message.text && <pre>{message.text}</pre>}
    {message.media_kind && <div className="inbox-attachment">{message.media_kind}
      {message.media_kind === '图片' && !photo && <button onClick={() => setPhoto(true)}>加载图片</button>}
      {photo && !photoError && <img loading="lazy" alt="Telegram 频道图片" src={`/api/telegram/media/${message.chat_id}/${message.message_id}`} onError={() => setPhotoError(true)} />}
      {photoError && <span>图片加载失败，请检查 Telegram 连接后重新打开页面。</span>}
      {message.media_kind !== '图片' && <small>已同步消息及文字说明；暂不在桌面内播放或下载附件。</small>}
    </div>}
    <p className="inbox-reason">{message.detail}</p>
    {!!message.merged_messages?.length && <details open><summary>合并来源（按 Telegram 回复关系）</summary>{message.merged_messages.map(source => <div key={source.message_id}><strong>消息 #{source.message_id}</strong><pre>{source.text}</pre></div>)}</details>}
    {parsed && <section className="inbox-parsed" aria-label="信号解析对照">
      <div><small>交易对</small><strong>{parsed.symbol}</strong></div>
      <div><small>方向</small><strong>{parsed.side === 'long' ? '做多' : '做空'}</strong></div>
      <div><small>入场区间</small><strong>{parsed.entry_low} — {parsed.entry_high}</strong></div>
      <div><small>止损</small><strong>{parsed.awaiting_protection ? '临时保证金亏损 100%（执行后核对）' : parsed.stop_loss}</strong></div>
      <div><small>止盈</small><strong>{parsed.awaiting_protection ? '等待回复，最多 5 分钟' : parsed.take_profits.map((p,i)=>`TP${i+1}: ${p}${parsed.take_profits.length===3 ? `（${[40,40,20][i]}%）` : ''}`).join(' / ')}</strong></div>
      {parsed.entry_correction && <div><small>入场参考校验（非成交价）</small><strong>{parsed.entry_correction.original} → {parsed.entry_correction.corrected} · 行情 {parsed.entry_correction.market}</strong></div>}
    </section>}
    <footer><span>{message.status === 'managed' ? message.detail : message.execution ? executionStatuses[message.execution.status] : message.execution_note}</span>
      {message.execution && <button onClick={() => onViewSignal(message.execution.id)}>查看跟单详情 →</button>}
    </footer>
    {message.execution_note && message.execution && <p className="inbox-reason">{message.execution_note}</p>}
    {message.execution?.bitget_order_id && <small>交易所订单：{message.execution.bitget_order_id}</small>}
    {message.origin === 'edit' && message.execution && <p className="inbox-reason">上方是编辑后的解析预览；原订单按首次收到的信号执行。请点“查看跟单详情”核对原始下单参数。</p>}
    {!!message.audit?.length && <details><summary>执行过程与未跟单原因</summary>{message.audit.map((entry, index) => <p key={index}>{time(entry.created_at)} · {entry.detail}</p>)}</details>}
  </article>;
}

export function TelegramInbox({ onManage, onViewSignal }) {
  const [inbox, setInbox] = useState({ channels: [], messages: [], connected: false });
  const [chatId, setChatId] = useState(null);
  const [filter, setFilter] = useState('all');
  const [showDuplicates,setShowDuplicates] = useState(false);
  const [error, setError] = useState('');
  const [stream, setStream] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [feedback, setFeedback] = useState('');
  const synced = useRef(new Set());
  const initializedChannel = useRef(false);
  const currentChat = useRef(chatId);
  currentChat.current = chatId;
  useEffect(() => {
    if (!initializedChannel.current && inbox.channels.length) {
      initializedChannel.current = true;
      setChatId(inbox.channels[0].id);
    }
  }, [inbox.channels]);

  useEffect(() => {
    let stopped = false, socket, reconnect, pending = false;
    const controller = new AbortController();
    const apply = data => { if (!stopped) { setInbox(data); setError(''); } };
    const refresh = async () => {
      if (pending) return; pending = true;
      try { apply(await getTelegramInbox(chatId, controller.signal)); }
      catch (err) { if (!stopped) setError(err.message); }
      finally { pending = false; }
    };
    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${protocol}://${window.location.host}/api/telegram/stream${chatId ? `?chat_id=${chatId}` : ''}`);
      socket.onopen = () => { if (!stopped) setStream(true); };
      socket.onmessage = event => { try { apply(JSON.parse(event.data)); } catch { setError('消息同步格式异常'); } };
      socket.onclose = () => { if (!stopped) { setStream(false); reconnect = setTimeout(connect, 2000); } };
      socket.onerror = () => socket.close();
    };
    refresh(); connect();
    const fallback = setInterval(refresh, 10000);
    return () => { stopped = true; controller.abort(); clearTimeout(reconnect); clearInterval(fallback); socket?.close(); };
  }, [chatId]);

  const sync = async id => {
    if (!id) return;
    setSyncing(true); setFeedback('');
    try { const result = await syncTelegramHistory(id); if (currentChat.current === id) { setFeedback(`已补读 ${result.synced} 条消息，历史消息不会下单。`); const latest = await getTelegramInbox(id); if (currentChat.current === id) setInbox(latest); } }
    catch (err) { setFeedback(err.message); }
    finally { setSyncing(false); }
  };
  useEffect(() => {
    if (chatId && inbox.connected && !synced.current.has(chatId)) {
      synced.current.add(chatId); sync(chatId);
    }
  }, [chatId, inbox.connected]);

  const selectedChannel = inbox.channels.find(channel => channel.id === chatId);
  const matching = inbox.messages.filter(message => (!chatId || message.chat_id === chatId) && (filter === 'all' || (filter === 'signal' ? !!message.parsed_signal : !message.parsed_signal)));
  const duplicates = matching.filter(message=>message.duplicate_of).length;
  const messages = matching.filter(message=>showDuplicates||!message.duplicate_of);
  const reparse = async()=>{setSyncing(true);setFeedback('');try{const result=await reparseTelegramCache();setFeedback(`已重新解析 ${result.reparsed} 条缓存消息，没有补发订单。`);setInbox(await getTelegramInbox(currentChat.current));}catch(e){setFeedback(e.message);}finally{setSyncing(false);}};
  return <div className="telegram-inbox-page">
    <header className="inbox-page-heading"><div><h1>Telegram 消息</h1><p>查看频道原文，对照信号解析，了解每笔跟单的来由。</p></div><button onClick={onManage}>管理监听频道</button></header>
    <section className="inbox-health"><span className={inbox.connected ? 'healthy' : ''}><i />{inbox.connected ? 'Telegram 已连接' : 'Telegram 未连接'}</span><span>{inbox.channels.length} 个频道正在配置为监听来源</span><span>{stream ? '桌面实时推送已连接' : '桌面推送重连中 · 10 秒轮询兜底'}</span><span>{inbox.auto_execution_enabled ? '自动跟单已启用' : '自动跟单未启用'}</span></section>
    <div className="inbox-explainer"><CheckCircle /><span><strong>什么是交易信号？</strong>明确币种、方向和参考价的实时市价消息可以先开仓；仅在启用本地模拟或 UTA 实盘且行情校验通过时执行，带初始保证金 100% 的临时止损预算。等待同频道回复补齐止盈止损，最多 5 分钟；三档按开仓时保存的分批止盈比例执行（币种杠杆页设置）。下方保留原文及纠错依据，实际成交与保护状态请查看订单。历史和编辑不补单。</span></div>
    {error && <p className="inbox-alert" role="alert">{error}</p>}
    {!inbox.connected && <p className="inbox-alert">{inbox.detail}。已缓存消息仍可查看，新消息需恢复 Telegram 连接。</p>}
    <div className="inbox-layout"><aside className="inbox-channels"><h2>当前监听频道</h2><button className={chatId === null ? 'selected' : ''} onClick={() => setChatId(null)}>全部频道<small>显示最新 100 条记录</small></button>
      {inbox.channels.map(channel => <button key={channel.id} className={chatId === channel.id ? 'selected' : ''} onClick={() => { setChatId(channel.id); setFeedback(''); }}><TelegramLogo /><strong>{channel.title}</strong><small>{channel.id}</small><small>最新消息：{time(channel.last_message_at)}</small></button>)}
      {!inbox.channels.length && <p>尚未保存监听频道。请到连接管理勾选频道，并点击“保存监听频道”。</p>}
    </aside><section className="inbox-feed"><header className="inbox-feed-header"><div><h2>{selectedChannel?.title || '全部频道消息'}</h2><small>新消息自动出现 · 按发送时间倒序</small></div><button disabled={!chatId || !inbox.connected || syncing} onClick={() => sync(chatId)}><ArrowsClockwise />{syncing ? '正在补读…' : '补读最近 30 条'}</button></header>
      <div className="inbox-filters">{[['all', '所有消息'], ['signal', '有信号参数'], ['other', '普通消息 / 未识别']].map(([value, label]) => <button key={value} className={filter === value ? 'selected' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div>
      <div className="inbox-filters"><label><input type="checkbox" checked={showDuplicates} onChange={e=>setShowDuplicates(e.target.checked)}/>显示重复信号原文（{duplicates} 条；默认折叠，未删除）</label><button disabled={syncing} onClick={reparse}>重新解析缓存（不补单）</button></div>
      {feedback && <p className="inbox-feedback" role="status">{feedback}</p>}
      {!messages.length && <div className="inbox-empty"><TelegramLogo size={36} /><h3>暂无消息记录</h3><p>点击左侧已监听频道会补读最近消息；后续新消息会自动同步到这里。非交易消息也会保留。</p></div>}
      {messages.map(message => <MessageCard key={`${message.chat_id}:${message.message_id}`} message={message} onViewSignal={onViewSignal} />)}
    </section></div>
  </div>;
}
