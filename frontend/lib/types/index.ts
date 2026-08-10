// ── Domain types ──────────────────────────────────────────────────────────────

export interface User {
  id: number;
  email: string;
  display_name: string | null;
  is_active: boolean;
}

export interface UserPreferences {
  default_underlying: string;
  default_interval_min: number;
  default_expiry_type: string;
  theme: "dark" | "light";
  strike_window: number;
}

export interface OptionExpiry {
  underlying: string;
  expiry_date: string; // ISO date string
}

// ── Snapshot types ────────────────────────────────────────────────────────────

export interface StrikeSnapshot {
  strike: number;
  call_oi: number | null;
  put_oi: number | null;
  call_ltp: number | null;
  put_ltp: number | null;
  call_volume: number | null;
  put_volume: number | null;
  call_iv: number | null;
  put_iv: number | null;
  call_oi_change: number | null;
  put_oi_change: number | null;
}

export interface OISnapshot {
  id: number;
  underlying: string;
  bucket_ts: string;
  interval_min: number;
  spot_price: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  pcr: number | null;
  strikes: StrikeSnapshot[];
}

export interface OISnapshotSummary {
  id: number;
  bucket_ts: string;
  spot_price: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  pcr: number | null;
}

export interface TrendingOIRow {
  bucket_ts: string;
  spot_price: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  pcr: number | null;
  call_oi_change: number | null;
  put_oi_change: number | null;
}

export interface HeatmapRow {
  strike: number;
  call_oi_change: number | null;
  put_oi_change: number | null;
}

export type TimeframeValue = "1m" | "5m" | "15m" | "30m" | "1h";

export interface OIHistoryBar {
  bucket_start: string;
  bucket_end: string;
  open_ltp: number | null;
  close_ltp: number | null;
  ltp_change: number | null;
  open_oi: number | null;
  close_oi: number | null;
  oi_change: number | null;
  oi_high: number | null;
  oi_low: number | null;
  volume: number | null;
  interpretation: string;
  total_call_oi: number | null;
  total_put_oi: number | null;
  call_oi_change: number | null;
  put_oi_change: number | null;
  pcr: number | null;
  oi_change_pcr: number | null;
}

export interface FuturesOIRow {
  instrument_key: string;
  trading_symbol: string | null;
  expiry_date: string | null;
  bucket_start: string;
  bucket_end: string;
  ltp: number | null;
  ltp_change: number | null;
  oi: number | null;
  oi_change: number | null;
  volume: number | null;
  interpretation: string | null;
}

export interface OptionsOISummary {
  total_call_oi: number;
  total_put_oi: number;
  call_oi_change: number;
  put_oi_change: number;
  pcr: number | null;
  oi_change_pcr: number | null;
}

// ── OI Signal ─────────────────────────────────────────────────────────────────

export type OISignal =
  | "long_buildup"
  | "short_buildup"
  | "long_unwinding"
  | "short_covering"
  | "neutral";

export interface CollectorJob {
  underlying: string;
  interval_min: number;
  is_running: boolean;
  last_run_at: string | null;
  last_status: string | null;
}

// ── SSE Event payloads ────────────────────────────────────────────────────────

export type SSEEventType =
  | "connected"
  | "snapshot_created"
  | "market_price_updated"
  | "collector_status_changed"
  | "auth_required"
  | "oi_alert";

export interface SSEMessage<T = unknown> {
  event_type: SSEEventType;
  schema_version: number;
  user_id: number;
  payload: T;
  ts: string;
}

export interface SnapshotCreatedPayload {
  snapshot_id: number;
  underlying: string;
  expiry_date: string;
  bucket_ts: string;
  interval_min: number;
  spot_price: number | null;
  total_call_oi: number | null;
  total_put_oi: number | null;
  pcr: number | null;
  net_oi_change: number | null;
}
