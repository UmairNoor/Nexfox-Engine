"use client";
import { useEffect, useRef, useState } from "react";
import { API_URL, api, tokens } from "./api";

export type LiveEvent = { event: string; data: any };

/** Subscribes to the workspace's live channel with a short-lived ticket. Reconnects with backoff. */
export function useLive(onEvent: (e: LiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let delay = 1000;
    let timer: ReturnType<typeof setTimeout>;
    const retry = () => { if (!stop) { timer = setTimeout(open, delay); delay = Math.min(delay * 2, 15000); } };
    const open = async () => {
      if (!tokens.access() || stop) return;
      let ticket: string;
      try { ticket = (await api<{ ticket: string }>("/api/v1/auth/ws-ticket", { method: "POST" })).ticket; } catch { retry(); return; }
      if (stop) return;
      ws = new WebSocket(`${API_URL.replace(/^http/, "ws")}/ws?ticket=${encodeURIComponent(ticket)}`);
      ws.onopen = () => { setConnected(true); delay = 1000; };
      ws.onmessage = (m) => { try { const e = JSON.parse(m.data); if (e.event !== "ping") handler.current(e); } catch { /* ignore */ } };
      ws.onclose = () => { setConnected(false); retry(); };
    };
    open();
    return () => { stop = true; clearTimeout(timer); ws?.close(); };
  }, []);
  return connected;
}
