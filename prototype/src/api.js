async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const error = new Error(Array.isArray(detail) ? detail.map((item) => `${item.loc?.slice(1).join('.')}: ${item.msg}`).join('；') : detail || `API request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

export function getSystemStatus(signal) {
  return apiRequest("/api/status", { signal });
}
export const getAccountPerformance = signal => apiRequest('/api/account/performance', { signal });
export const getUtaReadiness = () => apiRequest('/api/uta/readiness');
export const utaRequest = (path, body) => apiRequest(`/api/uta/${path}`,body===undefined?{}:{method:'POST',body:JSON.stringify(body)});

export const sendTelegramCode = () => apiRequest('/api/telegram/send-code', { method: 'POST' });
export const signInTelegram = (body) => apiRequest('/api/telegram/sign-in', { method: 'POST', body: JSON.stringify(body) });
export const getTelegramChannels = () => apiRequest('/api/telegram/channels');
export const saveTelegramChannels = (chat_ids) => apiRequest('/api/telegram/channels', { method: 'POST', body: JSON.stringify({ chat_ids }) });
export const getTelegramMessages = (signal) => apiRequest('/api/telegram/messages', { signal });
export const getTelegramInbox = (chatId, signal) => apiRequest(`/api/telegram/inbox${chatId ? `?chat_id=${chatId}` : ''}`, { signal });
export const syncTelegramHistory = (chatId) => apiRequest(`/api/telegram/channels/${chatId}/history`, { method: 'POST' });
export const reparseTelegramCache = () => apiRequest('/api/telegram/reparse', { method: 'POST' });

export function getMarketOverview(signal) {
  return apiRequest("/api/market/overview", { signal });
}

export const getMarketQuery = (symbol, signal) => apiRequest(`/api/market/query?symbol=${encodeURIComponent(symbol)}`, { signal });

export function setManualReview(enabled) {
  return apiRequest("/api/settings/manual-review", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export function getAutoExecutionSettings(signal) {
  return apiRequest("/api/settings/auto-execution", { signal });
}

export function saveAutoExecutionSettings(body) {
  return apiRequest("/api/settings/auto-execution", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getLeverageOverrides(signal) {
  return apiRequest("/api/settings/leverage-overrides", { signal });
}

export function saveLeverageOverrides(items, defaultMaxPercent) {
  return apiRequest("/api/settings/leverage-overrides", {
    method: "POST",
    body: JSON.stringify({ items, default_max_percent: defaultMaxPercent }),
  });
}

export function getSymbolLeverageLimit(symbol, signal) {
  return apiRequest(`/api/bitget/contracts/${encodeURIComponent(symbol)}/leverage`, { signal });
}

export function getConnections(signal) {
  return apiRequest("/api/connections", { signal });
}

export function saveBitgetConnection(body) {
  return apiRequest("/api/connections/bitget", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function saveTelegramConnection(body) {
  return apiRequest("/api/connections/telegram", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getBitgetAccount(signal) {
  return apiRequest("/api/bitget/account", { signal });
}

export function getLatestSignal(signal) {
  return apiRequest("/api/signals/latest", { signal });
}

export function getSignals(limit = 20, signal) {
  return apiRequest(`/api/signals?limit=${limit}`, { signal });
}

export function closePaperPositions(positionId) {
  return apiRequest('/api/paper/close-positions', {method:'POST', body:JSON.stringify({position_id:positionId,confirmation:'确认模拟平仓'})});
}

export function getTakeProfitAllocation(signal) {
  return apiRequest('/api/settings/take-profit-allocation', { signal });
}

export function saveTakeProfitAllocation(percentages) {
  return apiRequest('/api/settings/take-profit-allocation', { method: 'POST', body: JSON.stringify({ percentages }) });
}

export const getSignal = (id, signal) => apiRequest(`/api/signals/${encodeURIComponent(id)}`, { signal });

export function getSignalAudit(signalId, signal) {
  return apiRequest(`/api/signals/${signalId}/audit`, { signal });
}

export function approveSignal(signalId, body) {
  return apiRequest(`/api/signals/${signalId}/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function ignoreSignal(signalId) {
  return apiRequest(`/api/signals/${signalId}/ignore`, {
    method: "POST",
  });
}

export function getPaperAccount(refresh = true, signal) {
  return apiRequest(`/api/paper/account?refresh=${refresh}`, { signal });
}

export function resetPaperAccount(initialBalance, leverage = 10, feeRate = "0.0006", selectedSources = [], simulationId = "", sizing = {}) {
  return apiRequest("/api/paper/account/reset", {
    method: "POST",
    body: JSON.stringify({
      initial_balance: initialBalance,
      simulation_id: simulationId,
      leverage,
      fee_rate: feeRate,
      selected_sources: selectedSources,
      ...sizing,
    }),
  });
}

export const stopPaperAccount = () => apiRequest('/api/paper/account/stop', { method: 'POST' });
export const savePaperSizing = values => apiRequest('/api/paper/account/sizing', { method: 'POST', body: JSON.stringify(values) });
export const getPaperReport = (id = '', signal) => apiRequest(`/api/paper/report${id ? `?simulation_id=${encodeURIComponent(id)}` : ''}`, { signal });
export const getPaperReports = signal => apiRequest('/api/paper/reports', { signal });

export function setPaperAutoExecute(enabled) {
  return apiRequest("/api/paper/account/auto-execute", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export function getPaperSources(signal) {
  return apiRequest("/api/paper/sources", { signal });
}

export function setPaperStrategy(selectedSources) {
  return apiRequest("/api/paper/account/strategy", {
    method: "POST",
    body: JSON.stringify({ selected_sources: selectedSources }),
  });
}

export function executePaperSignal(signalId) {
  return apiRequest(`/api/paper/signals/${signalId}/execute`, { method: "POST" });
}
