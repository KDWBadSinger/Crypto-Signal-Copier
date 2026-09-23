import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowsClockwise, ChartLineUp, CheckCircle, Clock, Coins, Play,
  ShieldCheck, TrendDown, TrendUp, UsersThree, Wallet, Warning,
} from "@phosphor-icons/react";
import {
  executePaperSignal,
  getLatestSignal,
  getPaperAccount,
  getPaperSources,
  resetPaperAccount,
  setPaperAutoExecute,
  setPaperStrategy,
  stopPaperAccount,
  savePaperSizing,
} from "./api";
import { PaperPerformance, duration } from './PaperPerformance';
import "./paper-account.css";

const statusLabels = {
  pending: "等待入场",
  open: "持仓中",
  closed: "已平仓",
  rejected: "已拒绝",
};

const money = (value, digits = 2) => Number(value || 0).toLocaleString("en-US", {
  minimumFractionDigits: digits,
  maximumFractionDigits: digits,
});

const signedMoney = (value) => {
  const numeric = Number(value || 0);
  return `${numeric > 0 ? "+" : ""}${money(numeric)}`;
};

function Metric({ label, value, tone = "", detail, icon: Icon }) {
  return (
    <article className="paper-metric">
      <div><span>{label}</span>{Icon ? <Icon size={19} /> : null}</div>
      <strong className={tone}>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </article>
  );
}

function SourcePicker({ sources, selectedSources, disabled, onToggle }) {
  return (
    <div className="paper-source-picker">
      <div className="source-picker-heading"><UsersThree size={20} /><div><strong>选择跟单博主</strong><small>自动模拟只处理已选择博主的 Telegram 信号</small></div></div>
      {sources.length ? <div className="source-options">{sources.map((source) => {
        const selected = selectedSources.includes(source);
        return <label className={selected ? "source-option selected" : "source-option"} key={source}>
          <input type="checkbox" checked={selected} disabled={disabled} onChange={() => onToggle(source)} />
          <span className="source-avatar">{source.slice(0, 1).toUpperCase()}</span>
          <span><strong>{source}</strong><small>Telegram 监听来源</small></span>
          <i>{selected ? "跟单中" : "未选择"}</i>
        </label>;
      })}</div> : <div className="source-empty">监听到博主频道消息后，可在这里选择跟单来源。</div>}
    </div>
  );
}

export function PaperAccountView() {
  const [account, setAccount] = useState(null);
  const [latestSignal, setLatestSignal] = useState(null);
  const [sourceOptions, setSourceOptions] = useState([]);
  const [selectedSources, setSelectedSources] = useState([]);
  const [initialBalance, setInitialBalance] = useState("10000");
  const [leverage, setLeverage] = useState("10");
  const [sizingMode, setSizingMode] = useState('risk');
  const [fixedUsdt, setFixedUsdt] = useState('100');
  const [positionPercent, setPositionPercent] = useState('5');
  const sizingLoaded = useRef(false);
  const sizing = { leverage: Number(leverage), sizing_mode: sizingMode, fixed_usdt: fixedUsdt, position_percent: positionPercent };
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [simulationId, setSimulationId] = useState('');
  const [creating, setCreating] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const creatingRef = useRef(creating);
  creatingRef.current = creating;

  const load = useCallback(async (refresh, abortSignal) => {
    const [nextAccount, signal, sources] = await Promise.all([
      getPaperAccount(refresh, abortSignal),
      getLatestSignal(abortSignal).catch(err => { if (err.status === 404) return null; throw err; }),
      getPaperSources(abortSignal),
    ]);
    setAccount(nextAccount);
    if (nextAccount.initialized && !sizingLoaded.current) {
      setLeverage(String(nextAccount.leverage)); setSizingMode(nextAccount.sizing_mode);
      setFixedUsdt(String(nextAccount.fixed_usdt)); setPositionPercent(String(nextAccount.position_percent));
      sizingLoaded.current = true;
    }
    setLatestSignal(signal);
    setSourceOptions(sources);
    if (nextAccount.initialized && !creatingRef.current) setSelectedSources(nextAccount.selected_sources || []);
    setError("");
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(true, controller.signal)
      .catch((loadError) => {
        if (loadError.name !== "AbortError") setError(loadError.message);
      })
      .finally(() => setLoading(false));
    const timer = window.setInterval(() => {
      load(true, controller.signal).catch((loadError) => {
        if (loadError.name !== "AbortError") setError(loadError.message);
      });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  const initializeAccount = async () => {
    setSaving(true);
    setError("");
    try {
      setAccount(await resetPaperAccount(initialBalance, Number(leverage), "0.0006", selectedSources, simulationId, sizing));
      setCreating(false);
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const toggleSource = async (source) => {
    const nextSources = selectedSources.includes(source)
      ? selectedSources.filter((item) => item !== source)
      : [...selectedSources, source].sort();
    setSelectedSources(nextSources);
    if (!account?.initialized || creating) return;
    setSaving(true);
    setError("");
    try {
      setAccount(await setPaperStrategy(nextSources));
    } catch (saveError) {
      setSelectedSources(account.selected_sources || []);
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const toggleAutoExecute = async () => {
    setSaving(true);
    try {
      setAccount(await stopPaperAccount());
      setConfirmStop(false);
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const executeLatest = async () => {
    if (!latestSignal) return;
    setSaving(true);
    setError("");
    try {
      setAccount(await executePaperSignal(latestSignal.id));
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const latestAlreadyAdded = account?.trades?.some((trade) => trade.signal_id === latestSignal?.id);
  const returnTone = Number(account?.return_percent || 0) >= 0 ? "profit" : "loss";

  return (
    <>
      <header className="paper-header">
        <div>
          <span className="paper-eyebrow">LOCAL PAPER TRADING</span>
          <h1>实盘行情模拟账户</h1>
          <p>读取 Bitget 真实合约标记价，本金、订单、持仓与盈亏全部保存在本程序内。</p>
        </div>
        <button className="paper-refresh" disabled={loading} onClick={() => load(true).catch(err => setError(err.message))}>
          <ArrowsClockwise size={18} className={loading ? "spin" : ""} />刷新行情
        </button>
      </header>

      <div className="paper-safety">
        <ShieldCheck size={23} weight="fill" />
        <div><strong>仅使用公开实盘行情</strong><span>此功能不读取账户资产、不使用交易 API Key，也不会向 Bitget 提交任何订单。</span></div>
        <span className="paper-live-dot"><i />实盘价格</span>
      </div>

      {error ? <div className="api-error paper-error"><Warning weight="fill" />{error}</div> : null}
      {account?.market_error && <div className="api-error paper-error">{account.market_error}</div>}
      {account?.initialized && <section className="paper-run-banner"><div><strong>{account.simulation_id}</strong><span>{account.lifecycle === 'stopped' ? '已停止 · 结算已保存' : '持续跟单 · 重启自动恢复'}</span><small>累计在线 {duration(account.active_seconds)} · 最近有效行情 {account.market_updated_at ? new Date(account.market_updated_at).toLocaleString('zh-CN') : '尚无记录'}</small></div>{account.lifecycle === 'stopped' ? <button onClick={() => { setCreating(!creating); setSimulationId(''); }}>{creating ? '返回结算' : '创建新的模拟 ID'}</button> : <button className="paper-stop" disabled={saving} onClick={() => setConfirmStop(true)}>{saving ? '处理中…' : '停止跟单并结算'}</button>}</section>}
      {confirmStop && <section className="paper-stop-confirm" role="alertdialog" aria-label="确认停止模拟"><h2>停止并结算 {account?.simulation_id}？</h2><p>按最新公开行情平掉模拟持仓（计入手续费），取消未成交订单。该 ID 结算后不再继续，报告将保留。行情不可用时不强行结算，可恢复网络后重试。</p><button disabled={saving} onClick={toggleAutoExecute}>{saving ? '正在结算…' : '确认停止并结算'}</button><button disabled={saving} onClick={() => setConfirmStop(false)}>继续跟单</button></section>}

      {account?.lifecycle === 'stopped' && <PaperPerformance revision={`${account.simulation_id}:${account.updated_at}`} />}
      {(account?.lifecycle !== 'stopped' || creating) && <section className="paper-panel paper-sizing" aria-label="模拟开仓设置">
        <h2>模拟开仓设置</h2>
        <p>保证金是投入本金；持仓名义金额 = 保证金 × 杠杆。设置只影响保存后收到的新订单，已有挂单与持仓保持原参数。</p>
        <div className="paper-sizing-fields">
          <label>定仓方式<select value={sizingMode} onChange={e => setSizingMode(e.target.value)}><option value="fixed_usdt">每单固定保证金</option><option value="position_percent">账户权益百分比保证金</option><option value="risk">按信号风险与止损距离</option></select></label>
          {sizingMode === 'fixed_usdt' && <label>每单保证金（USDT）<input type="number" min="0.01" step="0.01" value={fixedUsdt} onChange={e => setFixedUsdt(e.target.value)} /></label>}
          {sizingMode === 'position_percent' && <label>每单权益比例（%，最多 10）<input type="number" min="0.01" max="10" step="0.1" value={positionPercent} onChange={e => setPositionPercent(e.target.value)} /></label>}
          <div>新单杠杆：币种杠杆页配置优先；未配置采用交易所最大杠杆的 50%，不再使用旧全局倍数。</div>
          {account?.initialized && !creating && <button disabled={saving || loading} onClick={async () => { setSaving(true); setError(''); try { setAccount(await savePaperSizing(sizing)); } catch(e) { setError(e.message); } finally { setSaving(false); } }}>保存新单设置</button>}
        </div>
        <p>{sizingMode === 'risk' ? '完整信号按止损距离定仓；市价先开仓时将权益 × 风险比例作为保证金。风险比例缺省为 1%。' : sizingMode === 'fixed_usdt' ? `每单保证金 ${fixedUsdt} USDT，名义持仓 = 保证金 × 该币种实际杠杆；另扣开仓手续费。` : '以成交时权益计算保证金；余额不足则拒绝开仓，不擅自缩小订单。'}</p>
        {account?.initialized && !creating && <small>当前生效：{account.sizing_mode === 'risk' ? '风险定仓' : account.sizing_mode === 'fixed_usdt' ? `${account.fixed_usdt} USDT 保证金/单` : `权益 ${account.position_percent}% 保证金/单`} · 每笔成交记录显示实际杠杆</small>}
      </section>}
      {!account?.initialized || creating ? (
        <section className="paper-setup">
          <div className="setup-visual"><Wallet size={42} weight="duotone" /></div>
          <div className="setup-copy">
            <span>第一步</span><h2>创建你的程序内模拟账户</h2>
            <p>输入虚拟本金并设置每单保证金；杠杆遵循“币种杠杆”页规则。下方选择需要跟随的频道。</p>
          </div>
          <label>自定义模拟 ID<input maxLength={64} placeholder="例如 Mia-30天测试（留空自动生成）" value={simulationId} onChange={event => setSimulationId(event.target.value)} /></label>
          <label>初始本金（USDT）<input type="number" min="1" step="100" value={initialBalance} onChange={(event) => setInitialBalance(event.target.value)} /></label>
          <button disabled={saving || loading || Number(initialBalance) <= 0} onClick={initializeAccount}>{saving ? "正在创建…" : "创建并持续跟单"}</button>
          <div className="setup-source-picker"><SourcePicker sources={sourceOptions} selectedSources={selectedSources} disabled={saving} onToggle={toggleSource} /></div>
        </section>
      ) : (
        <div className="paper-content">
          <section className="paper-metrics">
            <Metric label="账户权益" value={`${money(account.equity)} USDT`} detail={`初始本金 ${money(account.initial_balance)}`} icon={Wallet} />
            <Metric label="累计收益" value={`${signedMoney(Number(account.equity) - Number(account.initial_balance))} USDT`} tone={returnTone} detail={`收益率 ${signedMoney(account.return_percent)}%`} icon={ChartLineUp} />
            <Metric label="浮动盈亏" value={`${signedMoney(account.unrealized_pnl)} USDT`} tone={Number(account.unrealized_pnl) >= 0 ? "profit" : "loss"} detail={`已实现 ${signedMoney(account.realized_pnl)}`} icon={TrendUp} />
            <Metric label="可用本金" value={`${money(account.available_balance)} USDT`} detail={`占用保证金 ${money(account.used_margin)}`} icon={Coins} />
          </section>

          <section className="paper-control-grid">
            <article className="paper-panel paper-latest">
              <div className="paper-panel-title"><div><span>最新监听信号</span><h2>{latestSignal?.symbol || "暂无信号"}</h2></div>
                {latestSignal ? <span className={`direction ${latestSignal.side}`}>{latestSignal.side === "long" ? <TrendUp /> : <TrendDown />}{latestSignal.side === "long" ? "做多" : "做空"}</span> : null}
              </div>
              {latestSignal ? <>
                <div className="signal-price-grid"><div><span>入场区间</span><strong>{money(latestSignal.entry_low)} – {money(latestSignal.entry_high)}</strong></div><div><span>止损</span><strong>{money(latestSignal.stop_loss)}</strong></div><div><span>风险</span><strong>{latestSignal.risk_percent || 1}%</strong></div></div>
                <button className="paper-primary" disabled={saving || latestAlreadyAdded || account.lifecycle === 'stopped'} onClick={executeLatest}>
                  {latestAlreadyAdded ? <><CheckCircle weight="fill" />已加入模拟账户</> : <><Play weight="fill" />使用实盘行情模拟</>}
                </button>
              </> : <p className="paper-empty-copy">监听到 Telegram 信号后会显示在这里。</p>}
            </article>

            <article className="paper-panel paper-settings">
              <div className="paper-panel-title"><div><span>跟单策略</span><h2>博主与执行设置</h2></div><ShieldCheck size={25} /></div>
              <SourcePicker sources={sourceOptions} selectedSources={selectedSources} disabled={saving || account.lifecycle === 'stopped'} onToggle={toggleSource} />
              <div className="setting-row"><div><strong>{account.lifecycle === 'stopped' ? '本次模拟已结算' : account.auto_execute ? '已选博主新信号持续自动模拟' : '旧账户自动模拟未启用'}</strong><small>关闭应用不结算；重开继续处理新信号。市价信号按当前价模拟，区间信号等待入场。</small></div>{!account.auto_execute && account.lifecycle !== 'stopped' && <button onClick={async () => { try { setAccount(await setPaperAutoExecute(true)); } catch (err) { setError(err.message); } }}>启用持续跟单</button>}</div>
              <div className="setting-values"><span>杠杆<strong>按币种自动选择</strong></span><span>模拟手续费<strong>{money(Number(account.fee_rate) * 100, 3)}%</strong></span><span>已付手续费<strong>{money(account.fees_paid)}</strong></span></div>
              <p className="performance-note">运行中不可重置本金或覆盖 ID。停止结算后，可创建新的独立模拟。</p>
            </article>
          </section>

          <section className="paper-panel paper-trades">
            <div className="paper-panel-title"><div><span>本地账本</span><h2>模拟订单与持仓</h2></div><small><Clock />每 5 秒刷新实盘标记价</small></div>
            {account.trades.length ? <div className="paper-table-wrap"><table><thead><tr><th>币种 / 方向</th><th>状态</th><th>入场 / 实盘价</th><th>数量 / 保证金</th><th>止损 / 下一止盈</th><th>盈亏</th></tr></thead>
              <tbody>{account.trades.map((trade) => {
                const totalPnl = Number(trade.realized_pnl) + Number(trade.unrealized_pnl) - Number(trade.fees);
                const nextTarget = trade.take_profits[trade.next_take_profit];
                return <tr key={trade.id}>
                  <td><strong>{trade.symbol}</strong><span className={`direction compact ${trade.side}`}>{trade.side === "long" ? "多" : "空"}</span><small>{new Date(trade.created_at).toLocaleString("zh-CN", { hour12: false })}</small></td>
                  <td><span className={`trade-status ${trade.status}`}>{statusLabels[trade.status]}</span>{trade.close_reason ? <small>{trade.close_reason}</small> : null}</td>
                  <td><strong>{trade.entry_price ? money(trade.entry_price) : `${money(trade.entry_low)} – ${money(trade.entry_high)}`}</strong><small>实盘 {trade.last_price ? money(trade.last_price) : "等待行情"}</small></td>
                  <td><strong>{trade.remaining_size ?? "—"}</strong><small>保证金 {money(trade.margin)}</small></td>
                  <td><strong className="loss">SL {money(trade.stop_loss)}</strong><small>{trade.awaiting_protection && trade.status === 'open' ? `等待保护回复 · 截止 ${new Date(trade.protection_deadline).toLocaleTimeString()}` : nextTarget ? `TP${trade.next_take_profit + 1} ${money(nextTarget)}` : trade.status === 'closed' ? '已结束' : '暂无止盈'}</small>{trade.take_profits.length === 3 && <small>初始数量 40% / 40% / 20%</small>}</td>
                  <td><strong className={totalPnl >= 0 ? "profit" : "loss"}>{signedMoney(totalPnl)}</strong><small>手续费 {money(trade.fees)}</small></td>
                </tr>;
              })}</tbody></table></div> : <div className="paper-empty"><ChartLineUp size={34} /><strong>还没有模拟订单</strong><span>加入最新信号，或开启新信号自动模拟。</span></div>}
          </section>
        </div>
      )}
      {account?.initialized && account.lifecycle !== 'stopped' && <PaperPerformance revision={`${account.simulation_id}:${account.updated_at}`} />}
    </>
  );
}
