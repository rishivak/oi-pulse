export const queryKeys = {
  me: ["me"] as const,
  underlyings: ["underlyings"] as const,
  timeframes: ["oi", "timeframes"] as const,
  expiries: (underlying: string) => ["expiries", underlying] as const,
  latest: (underlying: string, expiry: string, interval: number) =>
    ["oi", "latest", underlying, expiry, interval] as const,
  history: (underlying: string, expiry: string, interval: number) =>
    ["oi", "history", underlying, expiry, interval] as const,
  trending: (underlying: string, expiry: string, interval: number) =>
    ["oi", "trending", underlying, expiry, interval] as const,
  strikes: (underlying: string, expiry: string, interval: number, atmRange: number) =>
    ["oi", "strikes", underlying, expiry, interval, atmRange] as const,
  heatmap: (underlying: string, expiry: string, interval: number) =>
    ["oi", "heatmap", underlying, expiry, interval] as const,
  historyBars: (underlying: string, expiry: string, timeframe: string, forDate?: string) =>
    ["oi", "history-bars", underlying, expiry, timeframe, forDate ?? "live"] as const,
  futures: (underlying: string, timeframe: string, expiry?: string) =>
    ["oi", "futures", underlying, timeframe, expiry ?? "auto"] as const,
  optionsSummary: (underlying: string, expiry: string, timeframe: string) =>
    ["oi", "options-summary", underlying, expiry, timeframe] as const,
  collectorStatus: ["collector", "status"] as const,
  settings: ["settings"] as const,
};
