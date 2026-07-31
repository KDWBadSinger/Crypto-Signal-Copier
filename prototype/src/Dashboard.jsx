import { useState } from "react";
import {
  ArrowLeft, Bell, CaretDown, CaretRight, CheckCircle, Clock, Coins,
  Database, FileText, Gear, Link, ListChecks, NotePencil, Pause, Play,
  Prohibit, ShieldCheck, TelegramLogo, TrendDown, UserCircle, Wallet,
  Warning, X,
} from "@phosphor-icons/react";
import "./dashboard.css";

const navItems = [
  [TelegramLogo, "信号追踪"], [Database, "持仓管理"], [FileText, "历史订单"],
  [Gear, "策略配置"], [ShieldCheck, "风控规则"], [Wallet, "资金管理"],
  [Link, "连接管理"], [Bell, "通知设置"], [ListChecks, "日志审计"],
  [FileText, "使用文档"],
];

const steps = [
  ["已接收消息", "2026-07-31 15:42:01"], ["已解析字段", "2026-07-31 15:42:02"],
  ["风控校验", "2026-07-31 15:42:06"], ["等待执行", "2026-07-31 15:42:18"],
];

const baseLogs = [
  ["15:42:01", "消息接收", "成功接收 Telegram 消息", "频道：CryptoAlpha Premium"],
  ["15:42:02", "解析引擎", "字段解析完成", "解析置信度：91%"],
  ["15:42:03", "数据标准化", "数值标准化完成", "价格单位：USDT，精度校验：通过"],
  ["15:42:06", "风控校验", "风控检查通过", "风险敞口 1.00% ≤ 限制 2.00%"],
  ["15:42:18", "执行队列", "已加入执行队列", "等待手动审核"],
];

function StatusDot({ warning = false }) {
  return <span className={`status-dot ${warning ? "warning" : ""}`} />;
}

export function Dashboard() {
  const [activeStep, setActiveStep] = useState(1);
  const [paused, setPaused] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [executed, setExecuted] = useState(false);
  const [ignored, setIgnored] = useState(false);
  const [showAllLogs, setShowAllLogs] = useState(false);
  const [entry, setEntry] = useState("3,680 – 3,720");
  const logs = showAllLogs
    ? [...baseLogs, ["15:39:44", "连接管理", "Bitget 模拟盘心跳正常", "响应时间：86ms"]]
    : baseLogs;

  const runSimulation = () => {
    setExecuted(true);
    setIgnored(false);
    setActiveStep(3);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Coins weight="fill" /></div>
          <div><strong>量化跟单控制台</strong><span>自动化加密货币跟单</span></div>
        </div>
        <nav aria-label="主导航">
          {navItems.map(([Icon, label], index) => (
            <button className={index === 0 ? "nav-item active" : "nav-item"} key={label}
              onClick={() => index !== 0 && window.alert(`${label}将在后续版本开放`)}>
              <Icon size={19} weight={index === 0 ? "fill" : "regular"} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <button className="account">
          <span className="avatar">U</span><span><strong>User123</strong><small>个人账户</small></span><CaretDown size={15} />
        </button>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <button className="back-link"><ArrowLeft size={18} />返回信号列表</button>
          <div className="headline-row">
            <div>
              <div className="title-line">
                <h1>信号追踪详情</h1>
                <span className={`status-pill ${executed ? "done" : ""}`}>
                  {ignored ? "已忽略" : executed ? "模拟已提交" : paused ? "全局暂停" : "等待执行"}
                </span>
              </div>
              <p>信号 ID：SIG-20260731-154201 <i /> 来源：TG 频道 <b>CryptoAlpha Premium</b></p>
            </div>
            <div className="connection-panel">
              <div><StatusDot />Telegram<small>已连接</small></div>
              <div><StatusDot />Bitget 模拟盘<small>已连接</small></div>
              <div className="time"><Clock size={19} />2026-07-31 15:42:18<small>Asia/Shanghai</small></div>
            </div>
            <button className={`pause-btn ${paused ? "active" : ""}`} onClick={() => setPaused(!paused)}>
              {paused ? <Play size={19} weight="fill" /> : <Pause size={19} weight="fill" />}
              {paused ? "恢复跟单" : "全部暂停"}
            </button>
          </div>
        </header>

        <div className="content-grid">
          <section className="trace-panel">
            <div className="process">
              {steps.map(([label, time], index) => (
                <button className={`process-step ${activeStep === index ? "active" : ""} ${index < activeStep ? "complete" : ""}`}
                  key={label} onClick={() => setActiveStep(index)}>
                  <span className="step-number">{index < activeStep ? <CheckCircle weight="fill" /> : index + 1}</span>
                  <span><strong>{label}</strong><small>{time}</small></span>
                </button>
              ))}
            </div>

            <div className="detail-pane">
              <div className="comparison">
                <article className="message-column">
                  <h2>原始 Telegram 消息</h2>
                  <div className="message-box">
                    <div className="sender">
                      <TelegramLogo size={30} weight="fill" /><strong>CryptoAlpha Premium</strong><time>2026-07-31 15:42:01</time>
                    </div>
                    <p className="signal-main">ETHUSDT <TrendDown size={18} weight="fill" /> 做空</p>
                    <p>Entry: 3680 – 3720</p><p>SL: 3785</p><p>TP1: 3560&nbsp;&nbsp; TP2: 3440</p><p>Risk: 1–2%</p>
                    <a>#ETH</a><footer>消息 ID：1345678901 <FileText size={14} /></footer>
                  </div>
                </article>

                <article className="parsed-column">
                  <div className="section-heading"><h2>解析出的字段</h2>
                    <button className="text-action" onClick={() => setEditOpen(true)}><NotePencil size={16} />编辑</button>
                  </div>
                  <dl className="fields">
                    <div><dt>币种</dt><dd>ETHUSDT</dd></div>
                    <div><dt>方向</dt><dd><span className="short-tag">做空</span></dd></div>
                    <div><dt>入场价</dt><dd>{entry} USDT</dd></div>
                    <div><dt>止损</dt><dd>3,785 USDT</dd></div>
                    <div><dt>止盈 1</dt><dd>3,560 USDT</dd></div>
                    <div><dt>止盈 2</dt><dd>3,440 USDT</dd></div>
                    <div><dt>仓位风险</dt><dd>1–2%</dd></div>
                    <div><dt>解析置信度</dt><dd className="confidence">91%</dd></div>
                  </dl>
                  <div className="warning-box"><Warning size={22} weight="fill" />
                    <div><strong>入场价字段存在不确定性</strong>
                      <p>检测到价格范围，系统将采用区间中位价 3,700 USDT 作为入场参考。</p>
                      <button onClick={() => setEditOpen(true)}>查看详情</button>
                    </div>
                  </div>
                </article>
              </div>
            </div>
          </section>

          <aside className="summary-panel">
            <h2>执行摘要</h2>
            <div className="summary-row"><span>交易所</span><strong>Bitget 模拟盘</strong></div>
            <div className="summary-row"><span>账户</span><strong>模拟账户 01</strong></div><hr />
            <div className="summary-row"><span>合约类型</span><strong>USDT 永续</strong></div>
            <div className="summary-row"><span>杠杆</span><strong>10x</strong></div><hr />
            <div className="summary-row"><span>入场参考价</span><strong>3,700 USDT<small>（区间中位价）</small></strong></div>
            <div className="summary-row"><span>预计仓位价值</span><strong>3,700.00 USDT</strong></div>
            <div className="summary-row"><span>预计保证金</span><strong>370.00 USDT</strong></div>
            <div className="summary-row"><span>预计最大亏损</span><strong className="loss">370.00 USDT<small>（1.00%）</small></strong></div><hr />
            <div className="allocation-title">止盈分配（按仓位价值）<ShieldCheck size={16} /></div>
            <div className="allocation"><span>TP1&nbsp;&nbsp; 3,560 USDT</span><b>50%</b><i><em /></i></div>
            <div className="allocation"><span>TP2&nbsp;&nbsp; 3,440 USDT</span><b>50%</b><i><em /></i></div>
            <div className="summary-row ratio"><span>预期盈亏比（R:R）</span><strong>2.00 : 1</strong></div>
            <div className="summary-row"><span>策略</span><strong>默认跟单策略</strong></div>
            <button className="strategy-link">查看详情 <CaretRight size={15} /></button>
            {executed ? <div className="success-state"><CheckCircle size={22} weight="fill" />模拟订单已提交，正在等待成交</div> :
              <button className="primary-cta" disabled={paused || ignored} onClick={runSimulation}><Play size={20} weight="fill" />审核并模拟</button>}
            <div className="secondary-actions">
              <button onClick={() => setEditOpen(true)}><NotePencil size={17} />编辑订单</button>
              <button onClick={() => { setIgnored(true); setExecuted(false); }}><Prohibit size={17} />忽略此信号</button>
            </div>
            <p className="disclaimer">提交后将进行模拟下单，不会使用真实资金。</p>
          </aside>

          <section className="audit">
            <h2>审计日志</h2>
            <div className="log-table">
              <div className="log-row log-head"><span>时间</span><span>模块</span><span>事件</span><span>详情</span></div>
              {logs.map((row, index) => <div className="log-row" key={`${row[0]}-${index}`}>
                <span>2026-07-31 {row[0]}</span><span>{row[1]}</span>
                <span><StatusDot warning={index === 4 && !executed} />{executed && index === 4 ? "模拟订单已提交" : row[2]}</span>
                <span>{executed && index === 4 ? "Bitget demo order ID: DEMO-87321" : row[3]}</span>
              </div>)}
            </div>
            <button className="expand-logs" onClick={() => setShowAllLogs(!showAllLogs)}>
              {showAllLogs ? "收起日志" : "展开更多日志"} <CaretDown className={showAllLogs ? "rotate" : ""} />
            </button>
          </section>
        </div>
      </main>

      {editOpen && <div className="modal-backdrop" role="presentation" onMouseDown={() => setEditOpen(false)}>
        <div className="modal" role="dialog" aria-modal="true" aria-labelledby="edit-title" onMouseDown={(event) => event.stopPropagation()}>
          <div className="modal-header"><div><span>订单草稿</span><h2 id="edit-title">确认解析字段</h2></div>
            <button onClick={() => setEditOpen(false)} aria-label="关闭"><X /></button></div>
          <label>入场价格范围<input value={entry} onChange={(event) => setEntry(event.target.value)} /></label>
          <div className="field-pair"><label>止损<input defaultValue="3,785" /></label><label>仓位风险<input defaultValue="1.00%" /></label></div>
          <div className="modal-note"><ShieldCheck size={19} />所有更改都会记录在审计日志中。</div>
          <div className="modal-actions"><button onClick={() => setEditOpen(false)}>取消</button><button className="save" onClick={() => setEditOpen(false)}>保存修改</button></div>
        </div>
      </div>}
    </div>
  );
}
