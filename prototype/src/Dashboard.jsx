import { useState } from "react";
import {
  Bell, CaretDown, Coins, Database, FileText, Gauge, Gear, Link,
  ListChecks, TelegramLogo, Wallet,
} from "@phosphor-icons/react";
import "./dashboard.css";
import { SignalTrackingView } from "./SignalTracking";
import { PaperAccountView } from "./PaperAccount";
import { ConnectionManagement } from "./ConnectionManagement";
import { BitgetAccountView } from "./BitgetAccount";
import { LeverageOverridesView } from "./LeverageOverrides";

const navItems = [
  { id: "signals", icon: TelegramLogo, label: "信号追踪" },
  { id: "leverage", icon: Gauge, label: "币种杠杆" },
  { id: "positions", icon: Database, label: "持仓管理" },
  { id: "orders", icon: FileText, label: "历史订单" },
  { id: "strategy", icon: Gear, label: "策略配置" },
  { id: "paper", icon: Wallet, label: "实盘模拟" },
  { id: "account", icon: Coins, label: "资金管理" },
  { id: "connections", icon: Link, label: "连接管理" },
  { id: "notifications", icon: Bell, label: "通知设置" },
  { id: "audit", icon: ListChecks, label: "日志审计" },
];

export function Dashboard() {
  const [activeNav, setActiveNav] = useState("signals");
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
          <span><strong>User123</strong><small>个人账户</small></span>
          <CaretDown size={15} />
        </div>
      </aside>

      <main className="workspace">
        {activeNav === "signals" ? <SignalTrackingView /> : null}
        {activeNav === "leverage" ? <LeverageOverridesView /> : null}
        {activeNav === "paper" ? <PaperAccountView /> : null}
        {activeNav === "account" ? <BitgetAccountView /> : null}
        {activeNav === "connections" ? <ConnectionManagement /> : null}
        {!new Set(["signals", "leverage", "paper", "account", "connections"]).has(activeNav) ? (
          <div className="empty-module" aria-label={`${activeItem.label}页面`} />
        ) : null}
      </main>
    </div>
  );
}
