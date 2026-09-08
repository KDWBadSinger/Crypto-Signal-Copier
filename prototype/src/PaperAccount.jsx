import { useCallback, useEffect, useState } from "react";
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
} from "./api";
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
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (refresh, abortSignal) => {
    const [nextAccount, signal, sources] = await Promise.all([
      getPaperAccount(refresh, abortSignal),
      getLatestSignal(abortSignal),
      getPaperSources(abortSignal),
    ]);
    setAccount(nextAccount);
    setLatestSignal(signal);
    setSourceOptions(sources);
    setSelectedSources(nextAccount.selected_sources || []);
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
    if (account?.initialized && !window.confirm("重新设置本金会清空所有本地模拟订单和盈亏记录。确认继续？")) return;
    setSaving(true);
    setError("");
    try {
      setAccount(await resetPaperAccount(initialBalance, Number(leverage), "0.0006", selectedSources));
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
    if (!account?.initialized) return;
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
      setAccount(await setPaperAutoExecute(!account.auto_execute));
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
        <button className="paper-refresh" disabled={loading} onClick={() => load(true)}>
          <ArrowsClockwise size={18} className={loading ? "spin" : ""} />刷新行情
        </button>
      </header>

      <div className="paper-safety">
        <ShieldCheck size={23} weight="fill" />
        <div><strong>仅使用公开实盘行情</strong><span>此功能不读取账户资产、不使用交易 API Key，也不会向 Bitget 提交任何订单。</span></div>
        <span className="paper-live-dot"><i />实盘价格</span>
      </div>

      {error ? <div className="api-error paper-error"><Warning weight="fill" />{error}</div> : null}

      {!account?.initialized ? (
        <section className="paper-setup">
          <div className="setup-visual"><Wallet size={42} weight="duotone" /></div>
          <div className="setup-copy">
            <span>第一步</span><h2>创建你的程序内模拟账户</h2>
            <p>输入一笔虚拟 USDT 本金。系统会根据每条信号的风险比例和止损距离计算仓位。</p>
          </div>
          <label>初始本金（USDT）<input type="number" min="1" step="100" value={initialBalance} onChange={(event) => setInitialBalance(event.target.value)} /></label>
          <label>模拟杠杆<select value={leverage} onChange={(event) => setLeverage(event.target.value)}>
            {[1, 2, 3, 5, 10, 20].map((value) => <option value={value} key={value}>{value}x</option>)}
          </select></label>
          <button disabled={saving || Number(initialBalance) <= 0} onClick={initializeAccount}>{saving ? "正在创建…" : "创建模拟账户"}</button>
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
                <button className="paper-primary" disabled={saving || latestAlreadyAdded} onClick={executeLatest}>
                  {latestAlreadyAdded ? <><CheckCircle weight="fill" />已加入模拟账户</> : <><Play weight="fill" />使用实盘行情模拟</>}
                </button>
              </> : <p className="paper-empty-copy">监听到 Telegram 信号后会显示在这里。</p>}
            </article>

            <article className="paper-panel paper-settings">
              <div className="paper-panel-title"><div><span>跟单策略</span><h2>博主与执行设置</h2></div><ShieldCheck size={25} /></div>
              <SourcePicker sources={sourceOptions} selectedSources={selectedSources} disabled={saving} onToggle={toggleSource} />
              <div className="setting-row"><div><strong>已选博主新信号自动模拟</strong><small>仅为上述博主的新消息创建本地挂单</small></div><button role="switch" aria-checked={account.auto_execute} className={account.auto_execute ? "switch on" : "switch"} disabled={saving} onClick={toggleAutoExecute}><span /></button></div>
              <div className="setting-values"><span>杠杆<strong>{account.leverage}x</strong></span><span>模拟手续费<strong>{money(Number(account.fee_rate) * 100, 3)}%</strong></span><span>已付手续费<strong>{money(account.fees_paid)}</strong></span></div>
              <div className="reset-row"><input type="number" min="1" value={initialBalance} onChange={(event) => setInitialBalance(event.target.value)} /><button disabled={saving} onClick={initializeAccount}>重置本金</button></div>
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
                  <td><strong className="loss">SL {money(trade.stop_loss)}</strong><small>{nextTarget ? `TP${trade.next_take_profit + 1} ${money(nextTarget)}` : "止盈完成"}</small></td>
                  <td><strong className={totalPnl >= 0 ? "profit" : "loss"}>{signedMoney(totalPnl)}</strong><small>手续费 {money(trade.fees)}</small></td>
                </tr>;
              })}</tbody></table></div> : <div className="paper-empty"><ChartLineUp size={34} /><strong>还没有模拟订单</strong><span>加入最新信号，或开启新信号自动模拟。</span></div>}
          </section>
        </div>
      )}
    </>
  );
}
