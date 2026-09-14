import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { IconCheck, IconInfo } from "./Icons";

type ToastTone = "ok" | "bad" | "info";

interface ToastInput {
  tone: ToastTone;
  title: string;
  message?: string;
}

interface ToastItem extends ToastInput {
  id: number;
}

interface ToastContextValue {
  push: (toast: ToastInput) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const push = useCallback((toast: ToastInput) => {
    const id = ++counter.current;
    setToasts((current) => [...current, { ...toast, id }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 4200);
  }, []);

  // Memoised so consumers (e.g. useEffect deps keyed on `toast`) don't see a
  // new context value — and re-run — every time a toast is added or expires.
  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <span className="toast__icon">{toast.tone === "bad" ? <IconInfo size={16} /> : <IconCheck size={16} />}</span>
            <span className="toast__body">
              <strong>{toast.title}</strong>
              {toast.message && <span>{toast.message}</span>}
            </span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}
