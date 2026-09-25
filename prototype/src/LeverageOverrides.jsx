import { useEffect, useMemo, useState } from "react";
import { CheckCircle, Gauge, Plus, Trash } from "@phosphor-icons/react";
import {
  getAutoExecutionSettings,
  getLeverageOverrides,
  getSymbolLeverageLimit,
  saveLeverageOverrides,
} from "./api";
import "./leverage-overrides.css";
import { TakeProfitAllocation } from './TakeProfitAllocation';

function normalizeSymbol(value) {
  const normalized = value.trim().toUpperCase().replaceAll("/", "").replaceAll("-", "");
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

export function LeverageOverridesView() {
  const [percent, setPercent] = useState(50);
  const [savedPercent, setSavedPercent] = useState(50);
  const [loaded, setLoaded] = useState(false);
  const [items, setItems] = useState([]);
  const [symbol, setSymbol] = useState("");
  const [leverage, setLeverage] = useState("10");
  const [limits, setLimits] = useState({});
  const [feedback, setFeedback] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      getAutoExecutionSettings(controller.signal),
      getLeverageOverrides(controller.signal),
    ]).then(([settings, overrides]) => {
      setPercent(overrides.default_max_percent);
      setSavedPercent(overrides.default_max_percent);
      setLoaded(true);
      setLeverage(String(settings.default_leverage));
      setItems(overrides.items);
    }).catch((error) => {
      if (error.name !== "AbortError") setFeedback(error.message);
    });
    return () => controller.abort();
  }, []);

  const itemMap = useMemo(() => new Map(items.map((item) => [item.symbol, item])), [items]);

  const persist = async (nextItems, successMessage, nextPercent = savedPercent) => {
    setSaving(true);
    setFeedback("");
    try {
      const saved = await saveLeverageOverrides(nextItems, nextPercent);
      setItems(saved.items);
      setSavedPercent(saved.default_max_percent);
      setFeedback(successMessage);
    } catch (error) {
      setFeedback(error.message);
    } finally {
      setSaving(false);
    }
  };

  const addOverride = async (event) => {
    event.preventDefault();
    const normalized = normalizeSymbol(symbol);
    const requested = Math.min(150, Math.max(1, Number(leverage)));
    if (!/^[A-Z0-9]+USDT$/.test(normalized)) {
      setFeedback("请输入正确的 USDT 合约交易对，例如 BTC 或 BTCUSDT");
      return;
    }
    try {
      const limit = await getSymbolLeverageLimit(normalized);
      setLimits((current) => ({ ...current, [normalized]: limit }));
      const next = [...items.filter((item) => item.symbol !== normalized), { symbol: normalized, leverage: requested }];
      await persist(next, `${normalized} 杠杆覆盖已保存`);
      setSymbol("");
    } catch (error) {
      setFeedback(error.message);
    }
  };

  return (
    <div className="leverage-page">
      <header className="leverage-header">
        <div><h1>币种杠杆覆盖</h1><p>单币种配置优先；未配置币种按保存的默认比例计算新单杠杆，向下取整。</p></div>
        <div className="margin-mode"><CheckCircle weight="fill" /><span><small>保证金模式</small><strong>全仓</strong></span></div>
      </header>

      <section className="leverage-summary">
        <div><Gauge size={28} /><span><small>未配置币种默认规则 · 已保存</small><strong>最大杠杆 × {savedPercent}%</strong></span></div>
        <p>适用于本地模拟及交易所自动跟单，仅影响新单，不修改已有仓位。0% 暂停未配置币种的新开仓；计算结果低于交易所最小杠杆或读取限制失败时拒单，不自动提高杠杆。</p>
      </section>
      <section className="leverage-percent">
        <label htmlFor="default-leverage-percent">默认最大杠杆比例 <strong>{percent}%</strong></label>
        <input id="default-leverage-percent" type="range" min="0" max="100" step="1" value={percent} disabled={!loaded || saving} onChange={event => setPercent(Number(event.target.value))} />
        <div className="percent-scale"><span>0% · 暂停新开仓</span><span>50%</span><span>100% · 最大杠杆</span></div>
        <p>{percent === 0 ? '未配置币种不再新开仓；已有仓位保护和单币种覆盖继续生效。' : `预览：交易所最大 100x → ${percent}x；最大 125x → ${Math.floor(125 * percent / 100)}x。`}</p>
        <button disabled={!loaded || saving || percent === savedPercent} onClick={() => persist(items, '默认杠杆比例已保存，仅对新单生效', percent)}>{saving ? '保存中…' : '保存默认比例'}</button>
        {percent !== savedPercent && <span> 尚未保存</span>}
      </section>

      <TakeProfitAllocation />
      <form className="override-form" onSubmit={addOverride}>
        <label><span>交易对</span><div><input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 BTC 或 BTCUSDT" /><b>USDT 永续</b></div></label>
        <label><span>目标杠杆</span><div><input type="number" min="1" max="150" value={leverage} onChange={(event) => setLeverage(event.target.value)} /><b>x</b></div></label>
        <button type="submit" disabled={!loaded || saving || !symbol.trim()}><Plus />添加覆盖</button>
      </form>

      {feedback ? <div className={feedback.includes("已保存") || feedback.includes("已删除") ? "override-feedback success" : "override-feedback"}>{feedback}</div> : null}

      <section className="override-table" aria-label="单币种杠杆覆盖列表">
        <div className="override-row override-head"><span>交易对</span><span>覆盖杠杆</span><span>Bitget 当前上限</span><span>实际执行</span><span>操作</span></div>
        {items.map((item) => {
          const limit = limits[item.symbol];
          const effective = limit ? Math.min(item.leverage, limit.max_leverage) : item.leverage;
          return <div className="override-row" key={item.symbol}>
            <strong>{item.symbol}</strong>
            <span>{item.leverage}x</span>
            <span>{limit ? `${limit.max_leverage}x` : "下单时实时查询"}</span>
            <span className="effective-leverage">≤ {effective}x · 全仓</span>
            <button type="button" aria-label={`删除 ${item.symbol} 覆盖`} disabled={saving} onClick={() => persist(items.filter((entry) => entry.symbol !== item.symbol), `${item.symbol} 杠杆覆盖已删除`)}><Trash /></button>
          </div>;
        })}
        {itemMap.size === 0 ? <div className="override-empty"><Gauge size={30} /><strong>还没有单币种覆盖</strong><span>所有交易对按已保存的 {savedPercent}% 默认比例处理</span></div> : null}
      </section>
    </div>
  );
}
