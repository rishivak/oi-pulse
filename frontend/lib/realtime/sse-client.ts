"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SSEEventType, SSEMessage } from "@/lib/types";

type Listener<T = unknown> = (msg: SSEMessage<T>) => void;

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

class SSEClient {
  private es: EventSource | null = null;
  private listeners = new Map<SSEEventType | "*", Set<Listener>>();
  private status: ConnectionStatus = "disconnected";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 3000;
  private maxReconnectDelay = 30_000;
  private statusCallbacks = new Set<(s: ConnectionStatus) => void>();

  connect() {
    if (this.es?.readyState === EventSource.OPEN) return;
    this._setStatus("connecting");

    this.es = new EventSource("/api/stream/events", { withCredentials: true });

    this.es.addEventListener("connected", () => {
      this._setStatus("connected");
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
      this._setStatus("error");
      this._scheduleReconnect();
    };
  }

  disconnect() {
    this.es?.close();
    this.es = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
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
