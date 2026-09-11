import { useState, useEffect, useCallback, useRef } from 'react';
import type { AuditEvent } from './useAPI';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function useSSE() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<AuditEvent | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);

  const connect = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
    }

    const source = new EventSource(`${API_BASE}/events`);
    sourceRef.current = source;

    source.onopen = () => {
      setConnected(true);
      console.log('🟢 SSE connected');
    };

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'connected') {
          setConnected(true);
          return;
        }

        if (data.type === 'audit_event' && data.data) {
          const auditEvent = data.data as AuditEvent;
          setLastEvent(auditEvent);
          setEvents((prev) => [auditEvent, ...prev].slice(0, 200)); // Keep max 200
        }

        if (data.type === 'human_decision' && data.data) {
          // Update existing event with human decision
          setEvents((prev) =>
            prev.map((e) =>
              e.id === data.data.event_id
                ? { ...e, human_decision: data.data.decision }
                : e
            )
          );
        }
      } catch (err) {
        // Heartbeat or invalid JSON — ignore
      }
    };

    source.onerror = () => {
      setConnected(false);
      source.close();

      // Auto-reconnect after 3 seconds
      reconnectTimeoutRef.current = window.setTimeout(() => {
        console.log('🔄 SSE reconnecting...');
        connect();
      }, 3000);
    };
  }, []);

  const disconnect = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    setConnected(false);
  }, []);

  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);

  return { events, connected, lastEvent, setEvents };
}
