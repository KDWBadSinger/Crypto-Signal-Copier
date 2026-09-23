import { useState } from "react";
import {
  Bell, CaretDown, ChartLineUp, Coins, Database, FileText, Gauge, Gear, Link,
  ListChecks, TelegramLogo, Wallet,
} from "@phosphor-icons/react";
import "./dashboard.css";
import { SignalTrackingView } from "./SignalTracking";
import { PaperAccountView } from "./PaperAccount";
import { ConnectionManagement } from "./ConnectionManagement";
import { BitgetAccountView } from "./BitgetAccount";
import { LeverageOverridesView } from "./LeverageOverrides";
import { MarketOverview } from "./MarketOverview";
import { TelegramInbox } from './TelegramInbox';

const navItems = [
  { id: "market", icon: ChartLineUp, label: "市场行情" },
  { id: "signals", icon: TelegramLogo, label: "信号追踪" },
  { id: "telegram", icon: TelegramLogo, label: "Telegram 消息" },
  { id: "leverage", icon: Gauge, label: "币种杠杆" },
  { id: "paper", icon: Wallet, label: "实盘模拟" },
  { id: "account", icon: Coins, label: "资金管理" },
  { id: "connections", icon: Link, label: "连接管理" },
];

export function Dashboard() {
  const [activeNav, setActiveNav] = useState("market");
  const [focusedSignal, setFocusedSignal] = useState(null);
  const activeItem = navItems.find((item) => item.id === activeNav) || navItems[0];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Coins weight="fill" /></div>
          <div><strong>量化跟单控制台</strong><span>自动化加密货币跟单</span></div>
        </div>
        <nav aria-label="主导航">
          {navItems.map(({ id, icon: Icon, label }) => (
            <button
              className={activeNav === id ? "nav-item active" : "nav-item"}
              key={label}
              aria-label={label}
              onClick={() => setActiveNav(id)}
            >
              <Icon size={19} weight={activeNav === id ? "fill" : "regular"} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="account">
          <span className="avatar">U</span>
          <span><strong>本机工作区</strong><small>个人桌面版 · 本地运行</small></span>
          <CaretDown size={15} />
        </div>
      </aside>

      <main className="workspace">
        {activeNav === "market" ? <MarketOverview /> : null}
        {activeNav === "signals" ? <SignalTrackingView focusSignalId={focusedSignal} /> : null}
        {activeNav === "telegram" ? <TelegramInbox onManage={() => setActiveNav('connections')} onViewSignal={id => { setFocusedSignal(id); setActiveNav('signals'); }} /> : null}
        {activeNav === "leverage" ? <LeverageOverridesView /> : null}
        {activeNav === "paper" ? <PaperAccountView /> : null}
        {activeNav === "account" ? <BitgetAccountView /> : null}
        {activeNav === "connections" ? <ConnectionManagement /> : null}
        {!new Set(["market", "signals", "telegram", "leverage", "paper", "account", "connections"]).has(activeNav) ? (
          <div className="empty-module" aria-label={`${activeItem.label}页面`} />
        ) : null}
      </main>
    </div>
  );
}
