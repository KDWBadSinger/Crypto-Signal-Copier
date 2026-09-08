import { useEffect, useMemo, useState } from "react";
import {
  CalendarBlank, CaretDown, CaretLeft, CaretRight, CheckCircle,
  GearSix, PaperPlaneTilt, SlidersHorizontal,
} from "@phosphor-icons/react";
import {
  getAutoExecutionSettings,
  getLeverageOverrides,
  getSignals,
  getSystemStatus,
  saveAutoExecutionSettings,
} from "./api";
import "./signal-tracking.css";

const demoRows = [
  { id: "demo-btc", time: "15:35:18", symbol: "BTCUSDT", side: "long", raw_text: "BTCUSDT 市价做多", status: "submitted", source_name: "CryptoAlpha Premium" },
  { id: "demo-sol", time: "15:21:47", symbol: "SOLUSDT", side: "long", raw_text: "SOLUSDT 市价做多", status: "submitted", source_name: "CryptoAlpha Premium" },
  { id: "demo-xrp", time: "15:05:33", symbol: "XRPUSDT", side: "short", raw_text: "XRPUSDT 市价做空", status: "submitted", source_name: "CryptoAlpha Premium" },
  { id: "demo-bnb", time: "14:50:11", symbol: "BNBUSDT", side: "long", raw_text: "BNBUSDT 市价做多", status: "submitted", source_name: "CryptoAlpha Premium" },
  { id: "demo-doge", time: "14:32:09", symbol: "DOGEUSDT", side: "long", raw_text: "DOGEUSDT 市价做多", status: "submitted", source_name: "CryptoAlpha Premium" },
];

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

function SignalDetail({ signal, settings, effectiveLeverage }) {
  const stopLoss = signal.stop_loss || "3,785";
  const takeProfits = signal.take_profits?.length ? signal.take_profits : ["3,560", "3,440"];
  const entry = signal.reference_entry || signal.entry_low || "3,689.42";
  const submitted = signal.status === "submitted";
  const numeric = (value) => Number(String(value).replaceAll(",", ""));
  const committedMargin = settings.sizing_mode === "fixed_usdt"
    ? `${settings.fixed_usdt} USDT`
    : `账户权益的 ${settings.position_percent}%`;
  const fixedMargin = Number(settings.fixed_usdt);
  const estimatedNotional = settings.sizing_mode === "fixed_usdt"
    ? fixedMargin * effectiveLeverage
    : null;
  const estimatedQuantity = estimatedNotional
    ? `${(estimatedNotional / numeric(entry)).toFixed(4)} ${signal.symbol.replace("USDT", "")}`
    : "按投入保证金 × 杠杆计算";
  const leverage = `${effectiveLeverage}x`;

  return (
    <div className="signal-detail" role="region" aria-label={`${signal.symbol}信号详情`}>
      <section className="original-thread">
        <h3>原始消息（来自 Telegram）</h3>
        <div className="message-step">
          <span className="message-number">1</span>
          <time>{signal.time}</time>
          <PaperPlaneTilt size={21} weight="fill" />
          <div className="telegram-bubble">
            <strong>{signal.symbol} 市价{signal.side === "long" ? "做多" : "做空"}</strong>
            <small>消息 ID：{signal.source_message_id || "63242101"}</small>
          </div>
        </div>
        <div className="thread-link"><span />已自动关联同一交易对的后续消息</div>
        <div className="message-step">
          <span className="message-number">2</span>
          <time>15:41:06</time>
          <PaperPlaneTilt size={21} weight="fill" />
          <div className="telegram-bubble">
            <strong>SL: {stopLoss} / TP1: {takeProfits[0]} / TP2: {takeProfits[1] || takeProfits[0]}</strong>
            <small>消息 ID：63242107</small>
          </div>
        </div>
      </section>

      <section className="parsed-result">
        <h3>解析结果（已合并）</h3>
        <dl>
          <div><dt>交易对</dt><dd>{signal.symbol}</dd></div>
          <div><dt>方向</dt><dd className={signal.side === "long" ? "long" : "short"}>{signal.side === "long" ? "做多" : "做空"}</dd></div>
          <div><dt>入场方式</dt><dd>{settings.execution_method === "market" ? "市价" : "挂单"}</dd></div>
          <div><dt>止损 SL</dt><dd>{numeric(stopLoss).toLocaleString()} USDT</dd></div>
          <div><dt>止盈 TP1</dt><dd>{numeric(takeProfits[0]).toLocaleString()} USDT</dd></div>
          <div><dt>止盈 TP2</dt><dd>{numeric(takeProfits[1] || takeProfits[0]).toLocaleString()} USDT</dd></div>
          <div><dt>信号类型</dt><dd>开仓</dd></div>
          <div><dt>合并消息数</dt><dd>2 条</dd></div>
          <div><dt>解析时间</dt><dd>15:41:06</dd></div>
        </dl>
      </section>

      <section className="execution-result">
        <h3>自动执行结果</h3>
        <dl>
          <div><dt>执行状态</dt><dd><span className={submitted ? "executed-tag" : "waiting-tag"}>{submitted ? "已执行" : "等待自动执行"}</span></dd></div>
          <div><dt>订单类型</dt><dd>{settings.execution_method === "market" ? "市价单" : "限价单"}</dd></div>
          <div><dt>方向</dt><dd className={signal.side === "long" ? "long" : "short"}>{signal.side === "long" ? "买入 / 做多" : "卖出 / 做空"}</dd></div>
          <div><dt>参考价格</dt><dd>{numeric(entry).toLocaleString()} USDT</dd></div>
          <div><dt>成交数量</dt><dd>{submitted ? estimatedQuantity : "—"}</dd></div>
          <div><dt>投入保证金</dt><dd>{committedMargin}</dd></div>
          <div><dt>名义仓位</dt><dd>{estimatedNotional ? `${estimatedNotional} USDT` : "按账户权益计算"}</dd></div>
          <div><dt>目标杠杆</dt><dd>{leverage}</dd></div>
          <div><dt>保证金模式</dt><dd>全仓</dd></div>
          <div><dt>订单 ID</dt><dd className="order-id">{signal.bitget_order_id || "等待交易所返回"}</dd></div>
          <div><dt>执行时间</dt><dd>{submitted ? signal.time : "—"}</dd></div>
        </dl>
      </section>
    </div>
  );
}

export function SignalTrackingView() {
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
      setExpandedId(nextSignals[0]?.id || "demo-btc");
    }).catch((error) => {
      if (error.name !== "AbortError") setFeedback(error.message);
    }).finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const rows = useMemo(() => {
    const serverRows = signals.map(formatSignal);
    const usedSymbols = new Set(serverRows.map((item) => item.symbol));
    return [...serverRows, ...demoRows.filter((item) => !usedSymbols.has(item.symbol))].slice(0, 6);
  }, [signals]);

  const sourceName = rows[0]?.source_name || "CryptoAlpha Premium";
  const leverageBySymbol = useMemo(
    () => new Map(leverageOverrides.map((item) => [item.symbol, item.leverage])),
    [leverageOverrides],
  );
  const leverageFor = (symbol) => leverageBySymbol.get(symbol) || Number(settings.default_leverage);
  const activeCount = rows.filter((item) => item.status === "submitted").length;
  const visibleRows = rows.filter((item) => {
    if (signalFilter === "executed") return item.status === "submitted";
    if (signalFilter === "pending") return item.status !== "submitted";
    if (signalFilter === "skipped" || signalFilter === "failed") return false;
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
            <button
              type="button"
              className={settings.enabled ? "automation-status enabled" : "automation-status"}
              aria-pressed={settings.enabled}
              onClick={() => updateSetting("enabled", !settings.enabled)}
            >
              <span />{settings.enabled ? "自动执行中" : "自动执行未启用"}
            </button>
          </div>
          <p>信号源：Telegram · <strong>{sourceName}</strong><i />更新时间：2026-08-06 15:41:28（Asia/Shanghai）</p>
        </div>
        <button className="save-settings" type="button" onClick={saveSettings} disabled={saving}>
          <GearSix size={17} />{saving ? "保存中…" : "保存设置"}
        </button>
      </header>

      <section className="execution-settings" aria-label="自动交易设置">
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
      </section>

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
          <button type="button"><CalendarBlank />2026-08-06</button>
        </div>
      </div>

      {feedback ? <div className={feedback.includes("已保存") ? "monitor-feedback success" : "monitor-feedback"}>{feedback}</div> : null}

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
                <span className="summary-cell">{signal.symbol === "ETHUSDT" ? "2 条消息（已合并）" : signal.raw_text?.split("\n")[0]}</span>
                <span className="parse-cell"><CheckCircle weight="fill" />解析成功</span>
                <span><b className={submitted ? "executed-tag" : "waiting-tag"}>{submitted ? "已执行" : "待执行"}</b></span>
                <span>{settings.sizing_mode === "fixed_usdt" ? `${settings.fixed_usdt} USDT` : `${settings.position_percent}%`}</span>
                <span>{leverageFor(signal.symbol)}x</span>
                <span>{submitted ? signal.time.replace(/\d{2}$/, (value) => String(Number(value) + 1).padStart(2, "0")) : "—"}</span>
              </button>
              {expanded ? <SignalDetail signal={signal} settings={settings} effectiveLeverage={leverageFor(signal.symbol)} /> : null}
            </div>
          );
        })}
        {visibleRows.length === 0 ? <div className="signal-empty">当前筛选条件下没有信号</div> : null}
        <footer className="table-footer"><span>共 {visibleRows.length} 条</span><div><button disabled><CaretLeft /></button><button className="active">1</button><button>2</button><button>3</button><button><CaretRight /></button><button>10 条/页 <CaretDown /></button></div></footer>
      </section>
    </div>
  );
}
