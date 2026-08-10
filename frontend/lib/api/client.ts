import axios from "axios";
import type {
  CollectorJob,
  FuturesOIRow,
  HeatmapRow,
  OIHistoryBar,
  OISnapshot,
  OISnapshotSummary,
  OptionsOISummary,
  StrikeSnapshot,
  TimeframeValue,
  TrendingOIRow,
  User,
  UserPreferences,
} from "@/lib/types";

// API base URL is /api — proxied to backend by Next.js rewrites
const api = axios.create({
  baseURL: "/api",
  withCredentials: true,  // include session cookie on all requests
});

// Redirect to login on 401
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && typeof window !== "undefined") {
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// ── Auth ──────────────────────────────────────────────────────────────────────

export const authApi = {
  me: () => api.get<User>("/auth/me").then((r) => r.data),
  logout: () => api.post("/auth/logout"),
  loginUrl: "/api/auth/login",
};

// ── OI Data ───────────────────────────────────────────────────────────────────

export const oiApi = {
  underlyings: () =>
    api.get<{ underlyings: string[] }>("/oi/underlyings").then((r) => r.data.underlyings),

  expiries: (underlying: string) =>
    api.get<{ expiries: string[] }>("/oi/expiries", { params: { underlying } }).then((r) => r.data.expiries),

  latest: (underlying: string, expiryDate: string, intervalMin = 5) =>
    api
      .get<{ snapshot: OISnapshot | null }>("/oi/latest", {
        params: { underlying, expiry_date: expiryDate, interval_min: intervalMin },
      })
      .then((r) => r.data.snapshot),

  history: (underlying: string, expiryDate: string, intervalMin = 5, limit = 50) =>
    api
      .get<{ snapshots: OISnapshotSummary[] }>("/oi/history", {
        params: { underlying, expiry_date: expiryDate, interval_min: intervalMin, limit },
      })
      .then((r) => r.data.snapshots),

  trending: (underlying: string, expiryDate: string, intervalMin = 5) =>
    api
      .get<{ rows: TrendingOIRow[] }>("/oi/trending", {
        params: { underlying, expiry_date: expiryDate, interval_min: intervalMin },
      })
      .then((r) => r.data.rows),

  strikes: (underlying: string, expiryDate: string, intervalMin = 5, atmRange = 10) =>
    api
      .get<{
        strikes: StrikeSnapshot[];
        spot_price: number | null;
        bucket_ts: string | null;
      }>("/oi/strikes", {
        params: { underlying, expiry_date: expiryDate, interval_min: intervalMin, atm_range: atmRange },
      })
      .then((r) => r.data),

  heatmap: (underlying: string, expiryDate: string, intervalMin = 5) =>
    api
      .get<{ rows: HeatmapRow[]; spot_price: number | null }>("/oi/heatmap", {
        params: { underlying, expiry_date: expiryDate, interval_min: intervalMin },
      })
      .then((r) => r.data),

  timeframes: () =>
    api
      .get<{ default: TimeframeValue; values: TimeframeValue[] }>("/oi/timeframes")
      .then((r) => r.data),

  historyBars: (
    underlying: string,
    expiryDate: string,
    timeframe: TimeframeValue,
    forDate?: string,
    limit = 300,
  ) =>
    api
      .get<{ rows: OIHistoryBar[] }>("/oi/history-bars", {
        params: {
          underlying,
          expiry_date: expiryDate,
          timeframe,
          for_date: forDate,
          limit,
        },
      })
      .then((r) => r.data.rows),

  futures: (
    underlying: string,
    timeframe: TimeframeValue,
    expiryDate?: string,
    limit = 200,
  ) =>
    api
      .get<{ rows: FuturesOIRow[] }>("/oi/futures", {
        params: {
          underlying,
          timeframe,
          expiry_date: expiryDate,
          limit,
        },
      })
      .then((r) => r.data.rows),

  optionsSummary: (
    underlying: string,
    expiryDate: string,
    timeframe: TimeframeValue,
  ) =>
    api
      .get<{ summary: OptionsOISummary | null }>("/oi/options/summary", {
        params: {
          underlying,
          expiry_date: expiryDate,
          timeframe,
        },
      })
      .then((r) => r.data.summary),
};

// ── Collector ─────────────────────────────────────────────────────────────────

export const collectorApi = {
  status: () =>
    api.get<{ jobs: CollectorJob[] }>("/collector/status").then((r) => r.data.jobs),

  start: (underlying: string, intervalMin: number) =>
    api.post("/collector/start", { underlying, interval_min: intervalMin }),

  stop: (underlying: string, intervalMin: number) =>
    api.post("/collector/stop", { underlying, interval_min: intervalMin }),
};

// ── Settings ──────────────────────────────────────────────────────────────────

export const settingsApi = {
  get: () => api.get<UserPreferences>("/settings").then((r) => r.data),
  update: (patch: Partial<UserPreferences>) => api.patch("/settings", patch),
};
