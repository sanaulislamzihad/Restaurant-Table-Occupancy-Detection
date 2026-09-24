import { useEffect, useState } from "react";

import { api, wsUrl } from "../api/client";
import type { LiveMessage, PipelineStatus, TableEvent } from "../api/types";

const MAX_EVENTS = 100;
const STALE_AFTER_MS = 5000; // the backend sends a status at least every second
const MAX_RETRY_DELAY_MS = 10000;

export interface LiveStatus {
  status: PipelineStatus | null;
  /** Newest first. */
  events: TableEvent[];
  socketOpen: boolean;
  /** Failed connection attempts since the last successful one. */
  failedAttempts: number;
  /** Goes up each time the connection is (re)established, e.g. to reload the video. */
  connectionCount: number;
}

/**
 * Live table status and events from /ws/status.
 *
 * Reconnects by itself with a growing delay (up to 10 s), and also when no
 * message arrives for 5 s (a silently dead connection). After each connect the
 * recent events are fetched once, so nothing that happened meanwhile is missed.
 */
export function useLiveStatus(): LiveStatus {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [events, setEvents] = useState<TableEvent[]>([]);
  const [socketOpen, setSocketOpen] = useState(false);
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [connectionCount, setConnectionCount] = useState(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let watchdog: number | undefined;
    let attempts = 0;
    let stopped = false;

    const addEvents = (incoming: TableEvent[]) =>
      setEvents((current) => {
        const known = new Set(current.map((event) => event.id));
        return [...incoming.filter((event) => !known.has(event.id)), ...current]
          .sort((a, b) => b.id - a.id)
          .slice(0, MAX_EVENTS);
      });

    const restartWatchdog = () => {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(() => socket?.close(), STALE_AFTER_MS);
    };

    const connect = () => {
      socket = new WebSocket(wsUrl("/ws/status"));
      socket.onopen = () => {
        attempts = 0;
        setFailedAttempts(0);
        setSocketOpen(true);
        setConnectionCount((count) => count + 1);
        restartWatchdog();
        api.events(50).then(addEvents).catch(() => undefined);
      };
      socket.onmessage = (message: MessageEvent<string>) => {
        restartWatchdog();
        const parsed = JSON.parse(message.data) as LiveMessage;
        if (parsed.type === "status") setStatus(parsed.data);
        else addEvents([parsed.data]);
      };
      socket.onclose = () => {
        window.clearTimeout(watchdog);
        setSocketOpen(false);
        if (stopped) return;
        attempts += 1;
        setFailedAttempts(attempts);
        retryTimer = window.setTimeout(connect, Math.min(500 * 2 ** attempts, MAX_RETRY_DELAY_MS));
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      window.clearTimeout(watchdog);
      socket?.close();
    };
  }, []);

  return { status, events, socketOpen, failedAttempts, connectionCount };
}
