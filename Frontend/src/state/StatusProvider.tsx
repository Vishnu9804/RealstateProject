import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { whatsappApi } from "../api/whatsappApi";
import type { WhatsAppStatusResponse } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { friendlyError } from "../lib/apiError";

/**
 * One poll of /whatsapp/status for the entire app.
 *
 * The header pill, the command palette and the connection screen all need
 * the same live status. Polling it once here and sharing the result means
 * the backend sees a single stream of requests no matter how many places
 * display it, and — more importantly for the user — every one of those
 * places always agrees with the others instead of drifting a few seconds
 * apart.
 */

interface StatusContextValue {
  status: WhatsAppStatusResponse | null;
  error: string | null;
  /** True until the very first response lands, so screens can show a
   *  skeleton once instead of flashing one on every poll. */
  initialLoading: boolean;
  /** Consecutive failed polls — lets the UI distinguish "one dropped tick"
   *  from "the backend is genuinely down" and only alarm the user for the
   *  latter. */
  failures: number;
  lastUpdated: Date | null;
  refresh: () => void;
}

const StatusContext = createContext<StatusContextValue | null>(null);

const POLL_INTERVAL_MS = 3000;

export function useAppStatus(): StatusContextValue {
  const context = useContext(StatusContext);
  if (!context) throw new Error("useAppStatus must be used inside <StatusProvider>");
  return context;
}

export function StatusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<WhatsAppStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState(0);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    // A slow backend must not queue up overlapping polls; skipping a tick is
    // always better than piling requests on something already struggling.
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const data = await whatsappApi.getStatus();
      setStatus(data);
      setError(null);
      setFailures(0);
      setLastUpdated(new Date());
    } catch (err) {
      setError(friendlyError(err));
      setFailures((count) => count + 1);
    } finally {
      inFlight.current = false;
      setInitialLoading(false);
    }
  }, []);

  usePolling(load, POLL_INTERVAL_MS);

  const value = useMemo(
    () => ({ status, error, initialLoading, failures, lastUpdated, refresh: () => void load() }),
    [status, error, initialLoading, failures, lastUpdated, load],
  );

  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}
