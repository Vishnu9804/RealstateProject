import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { authApi } from "../api/authApi";
import { ApiError, AUTH_TOKEN_STORAGE_KEY, clearStoredAuthToken, getStoredAuthToken, setStoredAuthToken } from "../api/client";
import type { LoginResult, UserSummary } from "../api/types";
import { useToast } from "../components/ui/Toast";

/**
 * Session + role for the whole app. Hiding admin-only buttons is cosmetic;
 * the backend enforces every rule. Admin sessions end after 30 minutes with
 * no interaction, so an owner's unattended browser can't be used by staff.
 */

const ADMIN_IDLE_LIMIT_MS = 30 * 60 * 1000;
const ACTIVITY_STORAGE_KEY = "authLastActivity";
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

type SessionState = "restoring" | "offline" | "ready";

interface AuthContextValue {
  user: UserSummary | null;
  isRestoring: boolean;
  /** A saved session exists but the server couldn't be reached to check it. */
  isOffline: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: (reason?: string) => void;
  /** Stores a token the server just re-issued (password change, ending other sessions). */
  replaceSession: (result: LoginResult) => void;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}

function readActivity(): number {
  try {
    return Number(localStorage.getItem(ACTIVITY_STORAGE_KEY)) || 0;
  } catch {
    return 0;
  }
}

function writeActivity(at: number): void {
  try {
    localStorage.setItem(ACTIVITY_STORAGE_KEY, String(at));
  } catch {
    // Storage unavailable: idle tracking just won't survive a reload.
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const toast = useToast();
  const [user, setUser] = useState<UserSummary | null>(null);
  const [state, setState] = useState<SessionState>("restoring");
  const [offlineAttempt, setOfflineAttempt] = useState(0);

  const restore = useCallback(async () => {
    if (!getStoredAuthToken()) {
      setUser(null);
      setState("ready");
      return;
    }
    try {
      setUser(await authApi.getMe());
      setState("ready");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setUser(null);
        setState("ready");
      } else {
        setState("offline");
        setOfflineAttempt((attempt) => attempt + 1);
      }
    }
  }, []);

  useEffect(() => {
    void restore();
  }, [restore]);

  useEffect(() => {
    if (state !== "offline") return;
    const id = window.setTimeout(() => void restore(), 5000);
    return () => window.clearTimeout(id);
  }, [state, offlineAttempt, restore]);

  const logout = useCallback(
    (reason?: string) => {
      clearStoredAuthToken();
      setUser(null);
      if (reason) toast.push({ tone: "warn", title: "Signed out", message: reason });
    },
    [toast],
  );

  const replaceSession = useCallback((result: LoginResult) => {
    setStoredAuthToken(result.access_token);
    writeActivity(Date.now());
    setUser(result.user);
    setState("ready");
  }, []);

  const login = useCallback(
    async (username: string, password: string) => replaceSession(await authApi.login({ username, password })),
    [replaceSession],
  );

  // Any 401 anywhere, or signing in/out in another tab, updates this tab too.
  useEffect(() => {
    const onUnauthorized = () => setUser(null);
    const onStorage = (event: StorageEvent) => {
      if (event.key === AUTH_TOKEN_STORAGE_KEY || event.key === null) void restore();
    };
    window.addEventListener("auth:unauthorized", onUnauthorized);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("auth:unauthorized", onUnauthorized);
      window.removeEventListener("storage", onStorage);
    };
  }, [restore]);

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    if (!isAdmin) return;
    let lastWrite = 0;
    const markActive = () => {
      const now = Date.now();
      if (now - lastWrite > 15_000) {
        lastWrite = now;
        writeActivity(now);
      }
    };
    const check = () => {
      const last = readActivity();
      if (last && Date.now() - last > ADMIN_IDLE_LIMIT_MS) {
        logout("You were signed out after 30 minutes without activity.");
      }
    };
    if (readActivity()) check();
    else markActive();
    const onVisible = () => {
      if (!document.hidden) check();
    };
    ACTIVITY_EVENTS.forEach((name) => window.addEventListener(name, markActive, { passive: true }));
    document.addEventListener("visibilitychange", onVisible);
    const id = window.setInterval(check, 30_000);
    return () => {
      ACTIVITY_EVENTS.forEach((name) => window.removeEventListener(name, markActive));
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(id);
    };
  }, [isAdmin, logout]);

  const value = useMemo(
    () => ({
      user,
      isRestoring: state === "restoring",
      isOffline: state === "offline",
      isAdmin,
      login,
      logout,
      replaceSession,
      retry: () => void restore(),
    }),
    [user, state, isAdmin, login, logout, replaceSession, restore],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
