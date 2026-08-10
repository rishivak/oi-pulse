"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SSEEventType, SSEMessage } from "@/lib/types";

type Listener<T = unknown> = (msg: SSEMessage<T>) => void;

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error" | "paused";

const IST_TIMEZONE = "Asia/Kolkata";
const MARKET_OPEN_HOUR = 9;
const MARKET_OPEN_MINUTE = 15;
const MARKET_CLOSE_HOUR = 15;
const MARKET_CLOSE_MINUTE = 30;
const MAX_AFTER_HOURS_RETRIES = 3;

class SSEClient {
  private es: EventSource | null = null;
  private listeners = new Map<SSEEventType | "*", Set<Listener>>();
  private status: ConnectionStatus = "disconnected";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 3000;
  private maxReconnectDelay = 30_000;
  private consecutiveFailures = 0;
  private statusCallbacks = new Set<(s: ConnectionStatus) => void>();

  connect() {
    if (this.status === "paused") {
      this._scheduleResumeAtMarketOpen();
      return;
    }
    if (this.es && this.es.readyState !== EventSource.CLOSED) return;
    this._setStatus("connecting");

    this.es = new EventSource("/api/stream/events", { withCredentials: true });

    this.es.addEventListener("connected", () => {
      this._setStatus("connected");
      this.consecutiveFailures = 0;
      this.reconnectDelay = 3000;
    });

    // Listen to all named events by registering a generic message handler
    const handleMsg = (event: MessageEvent) => {
      try {
        const parsed: SSEMessage = JSON.parse(event.data);
        this._dispatch(parsed.event_type as SSEEventType, parsed);
        this._dispatch("*", parsed);
      } catch {
        // Ignore malformed messages
      }
    };

    // Named events from the server
    const eventTypes: SSEEventType[] = [
      "snapshot_created",
      "market_price_updated",
      "collector_status_changed",
      "auth_required",
      "oi_alert",
    ];
    eventTypes.forEach((t) => this.es!.addEventListener(t, handleMsg));
    this.es.onmessage = handleMsg;

    this.es.onerror = () => {
      this.es?.close();
      this.es = null;
      this.consecutiveFailures += 1;

      if (!this._isMarketOpen() && this.consecutiveFailures >= MAX_AFTER_HOURS_RETRIES) {
        this._enterPausedMode();
        return;
      }

      this._setStatus("error");
      this._scheduleReconnect();
    };
  }

  disconnect() {
    this.es?.close();
    this.es = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this._setStatus("disconnected");
  }

  on<T = unknown>(event: SSEEventType | "*", fn: Listener<T>) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(fn as Listener);
    return () => {
      this.listeners.get(event)?.delete(fn as Listener);
    };
  }

  onStatusChange(fn: (s: ConnectionStatus) => void) {
    this.statusCallbacks.add(fn);
    return () => {
      this.statusCallbacks.delete(fn);
    };
  }

  getStatus() {
    return this.status;
  }

  private _dispatch(event: SSEEventType | "*", msg: SSEMessage) {
    this.listeners.get(event)?.forEach((fn) => fn(msg));
  }

  private _setStatus(s: ConnectionStatus) {
    this.status = s;
    this.statusCallbacks.forEach((fn) => fn(s));
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
    }, this.reconnectDelay);
  }

  private _enterPausedMode() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._setStatus("paused");
    this._scheduleResumeAtMarketOpen();
  }

  private _scheduleResumeAtMarketOpen() {
    if (this.reconnectTimer) return;
    const delay = this._msUntilNextMarketOpen();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.consecutiveFailures = 0;
      this.reconnectDelay = 3000;
      this._setStatus("disconnected");
      this.connect();
    }, delay);
  }

  private _isMarketOpen() {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: IST_TIMEZONE,
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date());

    const weekday = parts.find((part) => part.type === "weekday")?.value;
    const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
    const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
    const totalMinutes = hour * 60 + minute;
    const openMinutes = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE;
    const closeMinutes = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE;

    if (weekday === "Sat" || weekday === "Sun") return false;
    return totalMinutes >= openMinutes && totalMinutes <= closeMinutes;
  }

  private _msUntilNextMarketOpen() {
    const now = new Date();

    for (let daysAhead = 0; daysAhead <= 7; daysAhead += 1) {
      const candidate = new Date(now.getTime() + daysAhead * 24 * 60 * 60 * 1000);
      const dateParts = new Intl.DateTimeFormat("en-CA", {
        timeZone: IST_TIMEZONE,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).formatToParts(candidate);
      const weekday = new Intl.DateTimeFormat("en-GB", {
        timeZone: IST_TIMEZONE,
        weekday: "short",
      }).format(candidate);

      if (weekday === "Sat" || weekday === "Sun") continue;

      const year = dateParts.find((part) => part.type === "year")?.value;
      const month = dateParts.find((part) => part.type === "month")?.value;
      const day = dateParts.find((part) => part.type === "day")?.value;

      if (!year || !month || !day) continue;

      const openUtc = new Date(`${year}-${month}-${day}T03:45:00.000Z`);
      if (openUtc.getTime() > now.getTime()) {
        return Math.max(openUtc.getTime() - now.getTime(), 60_000);
      }
    }

    return 12 * 60 * 60 * 1000;
  }
}

// Singleton client — shared across all hooks/components
export const sseClient = new SSEClient();


// ── React hook ────────────────────────────────────────────────────────────────

export function useSSE<T = unknown>(
  event: SSEEventType | "*",
  onMessage: (msg: SSEMessage<T>) => void,
  enabled = true,
) {
  const cbRef = useRef(onMessage);
  cbRef.current = onMessage;

  useEffect(() => {
    if (!enabled) return;
    const unsub = sseClient.on<T>(event, (msg) => cbRef.current(msg));
    return unsub;
  }, [event, enabled]);
}

export function useSSEStatus(): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>(sseClient.getStatus());

  useEffect(() => {
    const unsub = sseClient.onStatusChange(setStatus);
    return unsub;
  }, []);

  return status;
}
