import { useEffect, useMemo, useState } from "react";
import {
  CalendarBlank, CaretDown, CaretLeft, CaretRight, CheckCircle,
  GearSix, PaperPlaneTilt, SlidersHorizontal,
} from "@phosphor-icons/react";
import {
  getAutoExecutionSettings,
  getLeverageOverrides,
  getSignals,
  getSignal,
  getSignalAudit,
  getSystemStatus,
  saveAutoExecutionSettings,
} from "./api";
import "./signal-tracking.css";
import { AccountPerformance } from './AccountPerformance';
import { UtaExecution } from './UtaExecution';

const defaultSettings = {
  enabled: false,
  execution_method: "market",
  sizing_mode: "fixed_usdt",
  fixed_usdt: "200",
  position_percent: "5",
  default_leverage: "10",
};

function formatSignal(signal, index) {
  const receivedAt = signal.received_at ? new Date(signal.received_at) : null;
  return {
    ...signal,
    time: signal.time || (receivedAt && !Number.isNaN(receivedAt.valueOf())
      ? receivedAt.toLocaleTimeString("zh-CN", { hour12: false }).slice(0, 8)
      : `15:${String(41 - index).padStart(2, "0")}:02`),
  };
}

function SettingChoice({ value, current, children, onSelect }) {
  return (
    <button
      type="button"
      className={current === value ? "setting-choice active" : "setting-choice"}
      aria-pressed={current === value}
      onClick={() => onSelect(value)}
    >
      {children}
    </button>
  );
}

const statusLabel = { submitted: '已提交模拟盘', pending_review: '待执行', approved_dry_run: '试运行', rejected: '执行失败', ignored: '已忽略', submitting: '提交中 / 待核对', unknown: '结果待核对' };

function SignalDetail({ signal }) {
  const [audit, setAudit] = useState([]);
  useEffect(() => { let active = true; getSignalAudit(signal.id).then(data => { if (active) setAudit(data); }).catch(() => {}); return () => { active = false; }; }, [signal.id, signal.status]);
  return <div className="signal-detail">
    <section className="original-thread"><h3>Telegram 原始消息</h3><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 13 }}>{signal.raw_text}</pre><small>消息 ID：{signal.source_message_id || '本地测试'} · {signal.source_name}</small></section>
    <section className="parsed-result"><h3>解析结果</h3><dl>
      <div><dt>交易对</dt><dd>{signal.symbol}</dd></div>
      <div><dt>方向</dt><dd>{signal.side === 'long' ? '做多' : '做空'}</dd></div>
      <div><dt>入场区间</dt><dd>{signal.entry_low} — {signal.entry_high}</dd></div>
      <div><dt>止损</dt><dd>{signal.stop_loss}</dd></div>
      <div><dt>止盈</dt><dd>{signal.take_profits.join(' / ')}</dd></div>
    </dl><small>模拟盘订单预设止损和第一个止盈目标。</small></section>
    <section className="execution-result"><h3>执行记录</h3><dl>
      <div><dt>状态</dt><dd>{statusLabel[signal.status] || signal.status}</dd></div>
      <div><dt>提交数量</dt><dd>{signal.execution_size || '—'}</dd></div>
      <div><dt>交易所订单 ID</dt><dd>{signal.bitget_order_id || '—'}</dd></div>
    </dl><small>已提交不代表已成交；成交以交易所回执为准。</small>
    {audit.map((item, index) => <p key={index} style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{new Date(item.created_at).toLocaleTimeString()} · {item.detail}</p>)}</section>
  </div>;
}

export function SignalTrackingView({ focusSignalId }) {
  const [focusedSignal, setFocusedSignal] = useState(null);
  const [settings, setSettings] = useState(defaultSettings);
  const [signals, setSignals] = useState([]);
  const [leverageOverrides, setLeverageOverrides] = useState([]);
  const [systemStatus, setSystemStatus] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [signalFilter, setSignalFilter] = useState("all");
  const [feedback, setFeedback] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getAutoExecutionSettings(controller.signal),
      getLeverageOverrides(controller.signal),
      getSignals(20, controller.signal),
      getSystemStatus(controller.signal),
    ]).then(([nextSettings, nextOverrides, nextSignals, nextStatus]) => {
      setSettings({
        ...nextSettings,
        fixed_usdt: String(nextSettings.fixed_usdt),
        position_percent: String(nextSettings.position_percent),
        default_leverage: String(nextSettings.default_leverage),
      });
      setSignals(nextSignals);
      setLeverageOverrides(nextOverrides.items);
      setSystemStatus(nextStatus);
      setExpandedId(focusSignalId || nextSignals[0]?.id || null);
    }).catch((error) => {
      if (error.name !== "AbortError") setFeedback(error.message);
    }).finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try { const [nextSignals, nextStatus] = await Promise.all([getSignals(50, controller.signal), getSystemStatus(controller.signal)]); setSignals(nextSignals); setSystemStatus(nextStatus); }
      catch (error) { if (error.name !== 'AbortError') setFeedback(error.message); }
      finally { pending = false; }
    };
    const timer = setInterval(refresh, 2000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (!focusSignalId) return;
    const controller = new AbortController();
    getSignal(focusSignalId, controller.signal).then(signal => { setFocusedSignal(signal); setExpandedId(signal.id); })
      .catch(error => { if (error.name !== 'AbortError') setFeedback(error.message); });
    return () => controller.abort();
  }, [focusSignalId]);

  const rows = useMemo(() => {
    const items = focusedSignal && !signals.some(item => item.id === focusedSignal.id) ? [focusedSignal, ...signals] : signals;
    return items.map(formatSignal);
  }, [signals, focusedSignal]);

  const sourceName = systemStatus?.telegram_selected_channels?.map(channel => channel.title).join('、') || "尚未选择监听频道";
  const leverageBySymbol = useMemo(
    () => new Map(leverageOverrides.map((item) => [item.symbol, item.leverage])),
    [leverageOverrides],
  );
  const leverageFor = (symbol) => leverageBySymbol.get(symbol) || Number(settings.default_leverage);
  const activeCount = rows.filter((item) => item.status === "submitted").length;
  const visibleRows = rows.filter((item) => {
    if (signalFilter === "executed") return item.status === "submitted";
    if (signalFilter === "pending") return ["pending_review", "approved_dry_run"].includes(item.status);
    if (signalFilter === "skipped") return item.status === "ignored";
    if (signalFilter === "failed") return item.status === "rejected";
    return true;
  });

  const updateSetting = (key, value) => {
    setSettings((current) => ({ ...current, [key]: value }));
    setFeedback("");
  };

  const saveSettings = async () => {
    setSaving(true);
    setFeedback("");
    try {
      const saved = await saveAutoExecutionSettings({
        ...settings,
        fixed_usdt: Number(settings.fixed_usdt),
        position_percent: Number(settings.position_percent),
        default_leverage: Number(settings.default_leverage),
      });
      setSettings({
        ...saved,
        fixed_usdt: String(saved.fixed_usdt),
        position_percent: String(saved.position_percent),
        default_leverage: String(saved.default_leverage),
      });
      setFeedback("自动执行设置已保存");
    } catch (error) {
      setFeedback(error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="signal-monitor">
      <header className="monitor-header">
        <div>
          <div className="monitor-title-line">
            <h1>信号追踪</h1>
            {systemStatus?.bitget_environment === 'demo' && <button
              type="button"
              className={settings.enabled ? "automation-status enabled" : "automation-status"}
              aria-pressed={settings.enabled}
              onClick={() => updateSetting("enabled", !settings.enabled)}
            >
              <span />{settings.enabled ? "自动执行已启用" : "自动执行未启用"}
            </button>}
          </div>
          <p>信号源：Telegram · <strong>{sourceName}</strong><i />每 2 秒刷新 · {systemStatus?.bitget_environment === "demo" ? "Bitget 模拟盘" : "UTA 实盘（需单独启用）"}</p>
        </div>
        {systemStatus?.bitget_environment === 'demo' && <button className="save-settings" type="button" onClick={saveSettings} disabled={saving}>
          <GearSix size={17} />{saving ? "保存中…" : "保存设置"}
        </button>}
      </header>
      <AccountPerformance />
      {systemStatus?.bitget_environment !== 'demo' && <UtaExecution />}

      {systemStatus?.bitget_environment === 'demo' && <section className="execution-settings" aria-label="自动交易设置">
        <div className="source-setting">
          <span className="setting-label">信号源（{systemStatus?.telegram?.connected ? "已连接" : "未连接"}）</span>
          <div className="channel-row"><PaperPlaneTilt size={33} weight="fill" /><span><strong>{sourceName}</strong><small>Telegram 频道</small></span></div>
          <small className="source-health"><i />{systemStatus?.telegram?.connected ? "监听正常" : "等待 Telegram 连接"}</small>
        </div>

        <div className="setting-group">
          <span className="setting-label">执行方式</span>
          <div className="segmented">
            <SettingChoice value="limit" current={settings.execution_method} onSelect={(value) => updateSetting("execution_method", value)}>挂单</SettingChoice>
            <SettingChoice value="market" current={settings.execution_method} onSelect={(value) => updateSetting("execution_method", value)}>市价</SettingChoice>
          </div>
        </div>

        <div className="setting-group">
          <span className="setting-label">投入方式</span>
          <div className="segmented">
            <SettingChoice value="fixed_usdt" current={settings.sizing_mode} onSelect={(value) => updateSetting("sizing_mode", value)}>固定保证金</SettingChoice>
            <SettingChoice value="position_percent" current={settings.sizing_mode} onSelect={(value) => updateSetting("sizing_mode", value)}>保证金比例</SettingChoice>
          </div>
        </div>

        <label className="amount-setting">
          <span className="setting-label">投入保证金</span>
          <span className="number-input"><input type="number" min="1" value={settings.fixed_usdt} disabled={settings.sizing_mode !== "fixed_usdt"} onChange={(event) => updateSetting("fixed_usdt", event.target.value)} /><b>USDT</b></span>
        </label>

        <label className="percent-setting">
          <span className="setting-label">保证金比例（权益的 0%–10%）</span>
          <span className="percent-input"><input type="number" min="0" max="10" step="0.1" value={settings.position_percent} disabled={settings.sizing_mode !== "position_percent"} onChange={(event) => updateSetting("position_percent", Math.min(10, Math.max(0, Number(event.target.value))))} /><b>%</b></span>
          <input className="range-input" type="range" min="0" max="10" step="0.5" value={settings.position_percent} disabled={settings.sizing_mode !== "position_percent"} onChange={(event) => updateSetting("position_percent", event.target.value)} />
          <span className="range-labels"><small>0%</small><small>5%</small><small>10%</small></span>
        </label>

        <label className="leverage-setting">
          <span className="setting-label">默认杠杆 · 全仓</span>
          <span className="number-input"><input type="number" min="1" max="150" step="1" value={settings.default_leverage} onChange={(event) => updateSetting("default_leverage", Math.min(150, Math.max(1, Number(event.target.value))))} /><b>x</b></span>
          <small>币种覆盖优先，超出上限自动下调</small>
        </label>
      </section>}

      <div className="monitor-toolbar">
        <div className="signal-tabs" role="tablist" aria-label="信号状态">
          <button className={signalFilter === "all" ? "active" : ""} role="tab" aria-selected={signalFilter === "all"} onClick={() => setSignalFilter("all")}>全部信号 <span>{rows.length}</span></button>
          <button className={signalFilter === "executed" ? "active" : ""} role="tab" aria-selected={signalFilter === "executed"} onClick={() => setSignalFilter("executed")}>已执行 <span>{activeCount}</span></button>
          <button className={signalFilter === "pending" ? "active" : ""} role="tab" aria-selected={signalFilter === "pending"} onClick={() => setSignalFilter("pending")}>待执行 <span>{rows.length - activeCount}</span></button>
          <button className={signalFilter === "skipped" ? "active" : ""} role="tab" aria-selected={signalFilter === "skipped"} onClick={() => setSignalFilter("skipped")}>跳过 <span>0</span></button>
          <button className={signalFilter === "failed" ? "active" : ""} role="tab" aria-selected={signalFilter === "failed"} onClick={() => setSignalFilter("failed")}>失败 <span>0</span></button>
        </div>
        <div className="toolbar-filters">
          <button type="button">全部交易对 <CaretDown /></button>
          <span>最近 50 条信号</span>
        </div>
      </div>

      {feedback ? <div className={feedback.includes("已保存") ? "monitor-feedback success" : "monitor-feedback"}>{feedback}</div> : null}
      {systemStatus?.bitget_environment === 'demo' && settings.enabled && (!systemStatus?.demo_order_execution_enabled || !systemStatus?.bitget?.connected || !systemStatus?.telegram?.connected) ? <div className="monitor-feedback">尚未具备自动跟单条件：请在连接管理完成 Telegram 登录和频道选择、连接 Bitget 模拟盘，并允许提交模拟盘订单。</div> : null}

      <section className="signal-table" aria-label="自动信号列表" aria-busy={loading}>
        <div className="signal-row signal-head">
          <span>时间</span><span>交易对</span><span>方向</span><span>信号摘要（原始消息）</span><span>解析结果</span><span>执行状态</span><span>投入保证金</span><span>杠杆</span><span>执行时间</span>
        </div>
        {visibleRows.map((signal) => {
          const expanded = expandedId === signal.id;
          const submitted = signal.status === "submitted";
          return (
            <div className="signal-record" key={signal.id}>
              <button className="signal-row signal-data" type="button" onClick={() => setExpandedId(expanded ? null : signal.id)} aria-expanded={expanded}>
                <span className="time-cell">{expanded ? <CaretDown /> : <CaretRight />}{signal.time}</span>
                <strong>{signal.symbol}</strong>
                <span><b className={signal.side === "long" ? "direction-tag long" : "direction-tag short"}>{signal.side === "long" ? "做多" : "做空"}</b></span>
                <span className="summary-cell">{signal.raw_text?.split("\n")[0]}</span>
                <span className="parse-cell"><CheckCircle weight="fill" />解析成功</span>
                <span><b className={submitted ? "executed-tag" : "waiting-tag"}>{statusLabel[signal.status] || signal.status}</b></span>
                <span>{systemStatus?.bitget_environment !== 'demo' ? '详见实盘记录' : settings.sizing_mode === "fixed_usdt" ? `${settings.fixed_usdt} USDT` : `${settings.position_percent}%`}</span>
                <span>{systemStatus?.bitget_environment !== 'demo' ? '详见实盘记录' : `${leverageFor(signal.symbol)}x`}</span>
                <span>{signal.execution_at ? new Date(signal.execution_at).toLocaleTimeString() : "—"}</span>
              </button>
              {expanded ? <SignalDetail signal={signal} settings={settings} effectiveLeverage={leverageFor(signal.symbol)} /> : null}
            </div>
          );
        })}
        {visibleRows.length === 0 ? <div className="signal-empty">当前筛选条件下没有信号</div> : null}
        <footer className="table-footer"><span>当前显示 {visibleRows.length} 条真实记录</span></footer>
      </section>
    </div>
  );
}
