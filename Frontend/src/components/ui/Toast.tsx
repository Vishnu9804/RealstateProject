import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { IconAlert, IconCheckCircle, IconInfo, IconX } from "./Icons";

/**
 * Toasts replace the old pattern of parking a "Saved." string next to the
 * button that caused it. Two reasons that pattern hurt:
 *   - the confirmation appeared wherever the button happened to be, so on a
 *     long form you could save and never see it;
 *   - it never went away, so a stale "Saved." sat next to edits that were
 *     no longer saved, actively lying to the user.
 * A toast is a single, consistent, self-expiring place for "what just
 * happened", with a visible timer so its disappearance is never a surprise.
 */

export type ToastTone = "ok" | "bad" | "warn" | "info";

interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  message?: string;
  durationMs: number;
  leaving?: boolean;
}

interface ToastApi {
  push: (toast: { tone?: ToastTone; title: string; message?: string; durationMs?: number }) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside <ToastProvider>");
  return context;
}

const TONE_ICON = {
  ok: IconCheckCircle,
  bad: IconAlert,
  warn: IconAlert,
  info: IconInfo,
} as const;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    // Mark as leaving first so the exit animation can run, then unmount.
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    const exit = window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timers.current.delete(id);
    }, 260);
    timers.current.set(-id, exit);
  }, []);

  const push = useCallback<ToastApi["push"]>(
    ({ tone = "info", title, message, durationMs = 4200 }) => {
      const id = nextId.current++;
      setToasts((prev) => {
        // Cap the stack — a burst of failing polls must not bury the page.
        const next = [...prev, { id, tone, title, message, durationMs }];
        return next.slice(-4);
      });
      const timer = window.setTimeout(() => dismiss(id), durationMs);
      timers.current.set(id, timer);
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((id) => window.clearTimeout(id));
      pending.clear();
    };
  }, []);

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toaster" role="region" aria-live="polite" aria-label="Notifications">
        {toasts.map((toast) => {
          const Icon = TONE_ICON[toast.tone];
          return (
            <div key={toast.id} className={`toast toast--${toast.tone}${toast.leaving ? " toast--leaving" : ""}`}>
              <span className="toast__icon">
                <Icon size={18} />
              </span>
              <div className="toast__body">
                <div className="toast__title">{toast.title}</div>
                {toast.message && <div className="toast__msg">{toast.message}</div>}
              </div>
              <button type="button" className="toast__close" onClick={() => dismiss(toast.id)} aria-label="Dismiss">
                <IconX size={14} />
              </button>
              <span className="toast__life" style={{ animationDuration: `${toast.durationMs}ms` }} />
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
