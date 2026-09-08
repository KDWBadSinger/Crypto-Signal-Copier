import { useEffect, useState } from "react";
import {
  CheckCircle, Eye, EyeSlash, Key, Link, LockKey, ShieldCheck,
  TelegramLogo, Warning,
} from "@phosphor-icons/react";
import {
  getConnections, getSystemStatus, saveBitgetConnection, saveTelegramConnection,
} from "./api";
import "./connection-management.css";

function SecretInput({ label, value, onChange, placeholder, help }) {
  const [visible, setVisible] = useState(false);
  return <label className="connection-field">
    <span>{label}</span>
    <div className="secret-input">
      <input
        type={visible ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
      />
      <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "隐藏" : "显示"}>
        {visible ? <EyeSlash /> : <Eye />}
      </button>
    </div>
    {help ? <small>{help}</small> : null}
  </label>;
}

function ConnectionBadge({ state }) {
  return <span className={`connection-badge ${state?.connected ? "connected" : "offline"}`}>
    {state?.connected ? <CheckCircle weight="fill" /> : <Warning weight="fill" />}
    {state?.connected ? "已连接" : state?.configured ? "待授权" : "未连接"}
  </span>;
}

export function ConnectionManagement({ onStatusChange }) {
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState("");
  const [message, setMessage] = useState(null);
  const [bitget, setBitget] = useState({ api_key: "", api_secret: "", passphrase: "", environment: "live" });
  const [telegram, setTelegram] = useState({ api_id: "", api_hash: "", phone: "", allowed_chat_ids: "" });

  useEffect(() => {
    const controller = new AbortController();
    getConnections(controller.signal).then((data) => {
      setOverview(data);
      setBitget((current) => ({ ...current, environment: data.bitget_environment || "live" }));
      setTelegram((current) => ({
        ...current,
        api_id: data.telegram_api_id ? String(data.telegram_api_id) : "",
        allowed_chat_ids: (data.telegram_allowed_chat_ids || []).join(", "),
      }));
    }).catch((error) => {
      if (error.name !== "AbortError" && !error.message?.includes("aborted")) {
        setMessage({ type: "error", text: error.message });
      }
    })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const refreshAppStatus = async () => {
    if (onStatusChange) onStatusChange(await getSystemStatus());
  };

  const submitBitget = async (event) => {
    event.preventDefault();
    setSaving("bitget");
    setMessage(null);
    try {
      const payload = { environment: bitget.environment };
      if (bitget.api_key) payload.api_key = bitget.api_key;
      if (bitget.api_secret) payload.api_secret = bitget.api_secret;
      if (bitget.passphrase) payload.passphrase = bitget.passphrase;
      const data = await saveBitgetConnection(payload);
      setOverview(data);
      setBitget((current) => ({ ...current, api_key: "", api_secret: "", passphrase: "" }));
      setMessage({ type: "success", text: "Bitget 配置已验证并保存在本机。" });
      await refreshAppStatus();
    } catch (error) {
      setMessage({ type: "error", text: error.message });
    } finally {
      setSaving("");
    }
  };

  const submitTelegram = async (event) => {
    event.preventDefault();
    setSaving("telegram");
    setMessage(null);
    try {
      const payload = {
        api_id: Number(telegram.api_id),
        phone: telegram.phone || undefined,
        allowed_chat_ids: telegram.allowed_chat_ids.split(",").map((item) => item.trim()).filter(Boolean).map(Number),
      };
      if (telegram.api_hash) payload.api_hash = telegram.api_hash;
      const data = await saveTelegramConnection(payload);
      setOverview(data);
      setTelegram((current) => ({ ...current, api_hash: "" }));
      setMessage({ type: "success", text: data.telegram.connected ? "Telegram 已连接。" : data.telegram.detail });
      await refreshAppStatus();
    } catch (error) {
      setMessage({ type: "error", text: error.message });
    } finally {
      setSaving("");
    }
  };

  return <div className="connections-page">
    <header className="connections-header">
      <div className="connections-title-icon"><Link weight="bold" /></div>
      <div><span>系统设置</span><h1>连接管理</h1><p>集中管理交易所与信号来源。凭据只保存在这台电脑，不会进入前端代码或 Git。</p></div>
    </header>

    {message ? <div className={`connection-message ${message.type}`}>
      {message.type === "success" ? <CheckCircle weight="fill" /> : <Warning weight="fill" />}{message.text}
    </div> : null}

    <div className="connections-grid">
      <form className="connection-card" onSubmit={submitBitget}>
        <div className="connection-card-head">
          <div className="service-icon bitget"><Key weight="fill" /></div>
          <div><h2>Bitget API</h2><p>账户状态、资产与行情连接</p></div>
          <ConnectionBadge state={overview?.bitget} />
        </div>
        <div className="connection-detail">{loading ? "正在读取配置…" : overview?.bitget?.detail}</div>
        <div className="environment-picker" role="group" aria-label="Bitget 环境">
          <button type="button" className={bitget.environment === "live" ? "active" : ""} onClick={() => setBitget({ ...bitget, environment: "live" })}>实盘只读</button>
          <button type="button" className={bitget.environment === "demo" ? "active" : ""} onClick={() => setBitget({ ...bitget, environment: "demo" })}>模拟盘</button>
        </div>
        <SecretInput label="API Key" value={bitget.api_key} onChange={(value) => setBitget({ ...bitget, api_key: value })} placeholder={overview?.bitget_api_key_hint || "输入 API Key"} help="留空将保留当前 API Key" />
        <SecretInput label="Secret Key" value={bitget.api_secret} onChange={(value) => setBitget({ ...bitget, api_secret: value })} placeholder={overview?.bitget?.configured ? "已安全保存，留空不修改" : "输入 Secret Key"} />
        <SecretInput label="Passphrase" value={bitget.passphrase} onChange={(value) => setBitget({ ...bitget, passphrase: value })} placeholder={overview?.bitget?.configured ? "已安全保存，留空不修改" : "输入 Passphrase"} />
        <div className="security-note"><ShieldCheck weight="fill" /><span><strong>实盘安全保护</strong>实盘模式仅允许读取，程序会在网络请求前阻止真实下单。</span></div>
        <button className="save-connection" disabled={saving === "bitget"}>{saving === "bitget" ? "正在验证…" : "验证并保存 Bitget"}</button>
      </form>

      <form className="connection-card" onSubmit={submitTelegram}>
        <div className="connection-card-head">
          <div className="service-icon telegram"><TelegramLogo weight="fill" /></div>
          <div><h2>Telegram API</h2><p>接收白名单频道中的交易信号</p></div>
          <ConnectionBadge state={overview?.telegram} />
        </div>
        <div className="connection-detail">{loading ? "正在读取配置…" : overview?.telegram?.detail}</div>
        <label className="connection-field"><span>API ID</span><input type="number" min="1" required value={telegram.api_id} onChange={(event) => setTelegram({ ...telegram, api_id: event.target.value })} placeholder="例如 12345678" /></label>
        <SecretInput label="API Hash" value={telegram.api_hash} onChange={(value) => setTelegram({ ...telegram, api_hash: value })} placeholder={overview?.telegram_api_hash_configured ? "已安全保存，留空不修改" : "输入 API Hash"} />
        <label className="connection-field"><span>手机号</span><input value={telegram.phone} onChange={(event) => setTelegram({ ...telegram, phone: event.target.value })} placeholder={overview?.telegram_phone_hint || "+86…"} /><small>包含国家区号；留空保留当前手机号</small></label>
        <label className="connection-field"><span>允许的频道 / 群组 ID</span><input required value={telegram.allowed_chat_ids} onChange={(event) => setTelegram({ ...telegram, allowed_chat_ids: event.target.value })} placeholder="-1001234567890, -1009876543210" /><small>多个数字 ID 使用英文逗号分隔</small></label>
        <div className="security-note telegram-note"><LockKey weight="fill" /><span><strong>首次登录</strong>保存 API 后，如未授权，系统会提示进行一次验证码登录并在本机生成 session。</span></div>
        <button className="save-connection" disabled={saving === "telegram"}>{saving === "telegram" ? "正在连接…" : "保存并连接 Telegram"}</button>
      </form>
    </div>
  </div>;
}
