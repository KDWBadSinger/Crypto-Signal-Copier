import { useCallback, useEffect, useState } from "react";
import {
  ArrowsClockwise, ChartLineUp, CheckCircle, Coins, Database,
  ShieldCheck, TrendDown, TrendUp, Wallet, Warning,
} from "@phosphor-icons/react";
import { getBitgetAccount } from "./api";
import "./bitget-account.css";

const accountLabels = {
  unified: "统一账户", hybrid: "混合账户", basic: "基础模式",
  advanced: "高级模式", isolated: "逐仓模式", multi_assets: "多资产",
  one_way_mode: "单向持仓", hedge_mode: "双向持仓",
  read_only: "只读", read_and_write: "读写",
};

function number(value, digits = 2) {
  const numeric = Number(value || 0);
  return numeric.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function precise(value) {
  const numeric = Number(value || 0);
  return numeric.toLocaleString("en-US", { maximumFractionDigits: 8 });
}

function percent(value) {
  return `${number(Number(value || 0) * 100, 2)}%`;
}

function EmptyState({ children }) {
  return <div className="bitget-empty"><CheckCircle weight="fill" />{children}</div>;
}

export function BitgetAccountView() {
  const [account, setAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [localStreamConnected, setLocalStreamConnected] = useState(false);

  const load = useCallback(async (signal) => {
    setLoading(true);
    setError("");
    try {
      setAccount(await getBitgetAccount(signal));
    } catch (requestError) {
      if (requestError.name !== "AbortError" && !requestError.message?.includes("aborted")) setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    let socket;
    let retryTimer;
    let stopped = false;
    let retryDelay = 1000;

    const connectStream = () => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const currentSocket = new WebSocket(`${protocol}//${window.location.host}/api/bitget/account/stream`);
      socket = currentSocket;
      currentSocket.onopen = () => {
        retryDelay = 1000;
        setLocalStreamConnected(true);
      };
      currentSocket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "heartbeat") return;
          if (payload.error) {
            setError(payload.error);
            return;
          }
          setAccount(payload);
          setError("");
        } catch {
          setError("收到无法识别的实时账户数据");
        }
      };
      currentSocket.onerror = () => currentSocket.close();
      currentSocket.onclose = () => {
        if (socket !== currentSocket) return;
        setLocalStreamConnected(false);
        if (!stopped) {
          retryTimer = window.setTimeout(connectStream, retryDelay);
          retryDelay = Math.min(retryDelay * 2, 15000);
        }
      };
    };

    connectStream();
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  const pnl = Number(account?.unrealised_pnl_usd || 0);
  const updatedAt = account?.updated_at
    ? new Date(account.updated_at).toLocaleString("zh-CN", { hour12: false })
    : "—";
  const realtimeConnected = localStreamConnected && account?.realtime_connected;
  const realtimeEventAt = account?.last_realtime_event_at
    ? new Date(account.last_realtime_event_at).toLocaleString("zh-CN", { hour12: false })
    : "等待账户变化";

  return <div className="bitget-account-page">
    <header className="bitget-account-header">
      <div className="account-title-icon"><Wallet weight="fill" /></div>
      <div className="account-heading"><span>Bitget · 实盘只读</span><h1>资金管理</h1><p>统一查看账户资产、保证金风险、当前持仓与未成交订单。</p></div>
      <div className={`realtime-account-state ${realtimeConnected ? "online" : "connecting"}`} title={account?.realtime_detail}>
        <i /><span><strong>{realtimeConnected ? "实时连接" : "正在重连"}</strong><small>{realtimeConnected ? realtimeEventAt : account?.realtime_detail || "连接实时频道"}</small></span>
      </div>
      <div className="account-sync"><small>最后校准</small><strong>{updatedAt}</strong></div>
      <button className="refresh-account" onClick={() => load()} disabled={loading}><ArrowsClockwise className={loading ? "spinning" : ""} />{loading ? "同步中" : "刷新数据"}</button>
    </header>

    {error ? <div className="account-error"><Warning weight="fill" /><div><strong>无法读取 Bitget 账户</strong><span>{error}</span></div></div> : null}

    <section className="account-metrics" aria-label="账户概要">
      <article><div className="metric-icon blue"><Wallet weight="fill" /></div><span>账户总权益</span><strong>${number(account?.account_equity_usd)}</strong><small>{precise(account?.account_equity_btc)} BTC</small></article>
      <article><div className="metric-icon green"><Coins weight="fill" /></div><span>有效可用权益</span><strong>${number(account?.effective_equity_usd)}</strong><small>{number(account?.account_equity_usdt)} USDT 总权益</small></article>
      <article><div className={`metric-icon ${pnl < 0 ? "red" : "green"}`}>{pnl < 0 ? <TrendDown weight="fill" /> : <TrendUp weight="fill" />}</div><span>未实现盈亏</span><strong className={pnl < 0 ? "negative" : "positive"}>{pnl >= 0 ? "+" : ""}${number(pnl)}</strong><small>当前持仓浮动盈亏</small></article>
      <article><div className="metric-icon amber"><ShieldCheck weight="fill" /></div><span>保证金率</span><strong>{percent(account?.margin_ratio)}</strong><small>维持保证金 ${number(account?.maintenance_margin_usd)}</small></article>
    </section>

    <section className="account-profile-strip">
      <div><span>账户模式</span><strong>{accountLabels[account?.account_mode] || account?.account_mode || "—"}</strong></div>
      <div><span>账户等级</span><strong>{accountLabels[account?.account_level] || account?.account_level || "—"}</strong></div>
      <div><span>资产模式</span><strong>{accountLabels[account?.asset_mode] || account?.asset_mode || "—"}</strong></div>
      <div><span>持仓模式</span><strong>{accountLabels[account?.hold_mode] || account?.hold_mode || "—"}</strong></div>
      <div><span>API 权限</span><strong>{accountLabels[account?.permission_type] || account?.permission_type || "—"}</strong></div>
      <div><span>账户杠杆</span><strong>{Number(account?.leverage) > 0 ? `${precise(account.leverage)}x` : "—"}</strong></div>
    </section>

    <div className="account-data-grid">
      <section className="account-panel assets-panel">
        <div className="account-panel-head"><div><Database weight="fill" /><span><strong>资产明细</strong><small>仅显示非零余额币种</small></span></div><b>{account?.assets?.length || 0} 种资产</b></div>
        <div className="account-table-scroll"><table><thead><tr><th>币种</th><th>权益</th><th>可用</th><th>锁定</th><th>负债</th><th>折合 USD</th></tr></thead><tbody>
          {(account?.assets || []).map((asset) => <tr key={asset.coin}><td><span className="coin-badge">{asset.coin.slice(0, 1)}</span><strong>{asset.coin}</strong></td><td>{precise(asset.equity)}</td><td>{precise(asset.available)}</td><td>{precise(asset.locked)}</td><td className={Number(asset.debt) > 0 ? "negative" : ""}>{precise(asset.debt)}</td><td><strong>${number(asset.usd_value)}</strong></td></tr>)}
        </tbody></table></div>
        {!loading && !account?.assets?.length ? <EmptyState>当前没有非零资产</EmptyState> : null}
      </section>

      <aside className="account-panel risk-panel">
        <div className="account-panel-head"><div><ShieldCheck weight="fill" /><span><strong>风险概览</strong><small>UTA 保证金指标</small></span></div></div>
        <dl>
          <div><dt>持仓价值</dt><dd>${number(account?.position_value_usd)}</dd></div>
          <div><dt>初始保证金</dt><dd>${number(account?.initial_margin_usd)}</dd></div>
          <div><dt>维持保证金</dt><dd>${number(account?.maintenance_margin_usd)}</dd></div>
          <div><dt>当前持仓</dt><dd>{account?.positions?.length || 0} 个</dd></div>
          <div><dt>未成交订单</dt><dd>{account?.open_orders?.length || 0} 笔</dd></div>
        </dl>
        <div className="read-only-seal"><ShieldCheck weight="fill" /><span><strong>只读数据面板</strong>本页面没有转账、提现或下单操作。</span></div>
      </aside>

      <section className="account-panel wide-panel">
        <div className="account-panel-head"><div><ChartLineUp weight="fill" /><span><strong>当前持仓</strong><small>USDT 永续合约</small></span></div><b>{account?.positions?.length || 0} 个持仓</b></div>
        {account?.positions?.length ? <div className="account-table-scroll"><table><thead><tr><th>合约</th><th>方向</th><th>数量</th><th>杠杆 / 模式</th><th>开仓均价</th><th>标记价格</th><th>强平价格</th><th>未实现盈亏</th></tr></thead><tbody>
          {account.positions.map((position) => <tr key={`${position.symbol}-${position.side}`}><td><strong>{position.symbol}</strong></td><td><span className={`side-tag ${position.side}`}>{position.side === "long" ? "多" : "空"}</span></td><td>{precise(position.total)}</td><td>{precise(position.leverage)}x · {position.margin_mode === "crossed" ? "全仓" : "逐仓"}</td><td>{number(position.average_price)}</td><td>{number(position.mark_price)}</td><td>{position.liquidation_price ? number(position.liquidation_price) : "—"}</td><td className={Number(position.unrealised_pnl) >= 0 ? "positive" : "negative"}>{Number(position.unrealised_pnl) >= 0 ? "+" : ""}{number(position.unrealised_pnl)} {position.margin_coin}</td></tr>)}
        </tbody></table></div> : <EmptyState>当前没有合约持仓</EmptyState>}
      </section>

      <section className="account-panel wide-panel">
        <div className="account-panel-head"><div><ArrowsClockwise weight="bold" /><span><strong>未成交订单</strong><small>未成交或部分成交</small></span></div><b>{account?.open_orders?.length || 0} 笔订单</b></div>
        {account?.open_orders?.length ? <div className="account-table-scroll"><table><thead><tr><th>合约</th><th>方向</th><th>类型</th><th>价格</th><th>数量</th><th>已成交</th><th>状态</th><th>创建时间</th></tr></thead><tbody>
          {account.open_orders.map((order, index) => <tr key={`${order.symbol}-${order.created_at}-${index}`}><td><strong>{order.symbol}</strong></td><td>{order.side === "buy" ? "买入" : "卖出"}</td><td>{order.order_type === "limit" ? "限价" : "市价"}</td><td>{number(order.price)}</td><td>{precise(order.quantity)}</td><td>{precise(order.filled_quantity)}</td><td><span className="order-live">{order.status === "live" ? "挂单中" : "部分成交"}</span></td><td>{order.created_at ? new Date(order.created_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td></tr>)}
        </tbody></table></div> : <EmptyState>当前没有未成交订单</EmptyState>}
      </section>
    </div>
  </div>;
}
