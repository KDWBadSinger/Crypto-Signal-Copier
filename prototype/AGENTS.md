# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Selected product direction

- Put “市场行情” in its own sidebar module as the first navigation item, immediately before “信号追踪”; do not render it globally above every page. Show mainstream symbols and symbols with active positions/orders together with recent price trends.
- Use the bright, audit-first desktop console selected as Product Design option 2.
- Keep the signal lifecycle visible: authorized message receipt, parsing, risk checks, and execution review.
- Keep Bitget demo trading and the local paper account visibly separate. The local paper account reads production Bitget market prices but never submits exchange orders.
- Let users initialize the local paper account with virtual USDT principal and show equity, available balance, margin, fees, realized/unrealized P&L, and return.
- Keep manual approval prominent for exchange demo execution; local paper auto-execution is an independent account setting.
- Put the local paper account under a dedicated sidebar module named “实盘模拟”; do not attach it to the user avatar.
- Do not show a separate “风控规则” sidebar module. Configure the paper follow strategy by selecting Telegram bloggers/channels inside “实盘模拟”.
- Use “连接管理” as the single place to enter, validate, and review masked Bitget and Telegram API configuration; never expose saved secrets back to the frontend.
- Use “资金管理” for the read-only Bitget UTA account view: equity, margin risk, assets, positions, and open orders. Keep it separate from the local “实盘模拟” ledger and omit identity, IP, and raw credential data.
- Keep Bitget account data live with a read-only UTA private WebSocket feeding safe REST-calibrated snapshots to the browser; show connection/reconnect state and retain manual refresh as a fallback.
- Treat “信号追踪” as an automatic execution monitor, not a manual approval workflow. Remove the receive/parse/risk/wait stepper and any per-signal approval CTA.
- Show the currently tracked Telegram channel prominently in “信号追踪”. Let users choose market versus limit execution and size each automatic order by fixed USDT or by 0%–10% of account equity.
- Preserve the original Telegram message view and make room for multi-message correlation: a blogger may post a market-entry message first, then send stop-loss and take-profit details in a later message for the same symbol.
- Use the selected compact “自动订单监视器” direction for the signal screen: settings band, expandable signal list, merged-message detail, and automatic execution result.
- Contract execution uses crossed margin. Let users set a default leverage in “信号追踪” and manage per-symbol leverage overrides in a dedicated sidebar module. Always cap the requested leverage at Bitget's live per-symbol maximum before an automatic order.
- Treat fixed USDT and the 0%–10% account-equity option as margin actually committed by the user, not final position notional. Calculate order notional as committed margin multiplied by the effective leverage.
- Communicate with the user in Chinese.
- Ship a Windows desktop application with in-app Telegram authorization, selectable joined channels, real receipt logs, and Bitget demo automatic execution. Do not display fabricated orders, merged messages, or execution times. Real-money order execution remains disabled for this iteration.
- Give Telegram a dedicated message inbox with the actual selected channel names, live receipt/connection state, original messages (including non-signal and attachment messages), parsed fields beside the original, and a link to the corresponding execution record. Backfilled history and edited messages must never trigger new orders.
- Correlate split blogger entries and subsequent stop-loss/take-profit replies by exact same-channel Telegram reply ID. Show the original sources together, wait for complete protection before execution, and retain a single root-message order identity; never guess associations by symbol or proximity.
- Local paper simulations use a user-defined ID, persist across app restarts, and run in the backend without the page open. Closing the app pauses observations but does not settle. Explicit stop settles positions and archives an immutable report; preserve older IDs when creating the next simulation. Show zoomable daily equity, hover balance/net P&L, calendar duration and online duration, with honest offline gaps and simulation limitations.
- Commercial readiness excludes payments for now: prioritize verified signal execution, fresh public-market push with fallback, operational diagnostics, data backups, and locally testable account/device authorization. Do not call a release production-ready without real-account acceptance, trusted distribution and explicit support for the blogger's management-message formats.
- Make paper sizing visible and editable: fixed USDT margin, equity percentage margin, or legacy stop-distance risk sizing; leverage and sizing changes affect only newly received orders. Keep real-account equity observations explicitly separate from attributed copier P&L; never chart deposits or paper results as real follow profit.
- User has confirmed Bitget Unified Trading Account (UTA) and requested eventual real-money automatic copying. Target V3 USDT perpetual APIs. Keep execution disabled until explicit in-app activation and verified protection/recovery; never reuse classic V2 order fields for UTA, silently change holding mode, or claim readiness based only on a successful API preflight.
