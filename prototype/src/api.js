async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail || `API request failed (${response.status})`);
  }
  return payload;
}

export function getSystemStatus(signal) {
  return apiRequest("/api/status", { signal });
}

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

export function saveLeverageOverrides(items) {
  return apiRequest("/api/settings/leverage-overrides", {
    method: "POST",
    body: JSON.stringify({ items }),
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

export function resetPaperAccount(initialBalance, leverage = 10, feeRate = "0.0006", selectedSources = []) {
  return apiRequest("/api/paper/account/reset", {
    method: "POST",
    body: JSON.stringify({
      initial_balance: initialBalance,
      leverage,
      fee_rate: feeRate,
      selected_sources: selectedSources,
    }),
  });
}

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
