import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { authApi } from "../api/authApi";
import { ApiError, AUTH_TOKEN_STORAGE_KEY, clearStoredAuthToken, getStoredAuthToken, setStoredAuthToken } from "../api/client";
import type { LoginResult, UserSummary } from "../api/types";
import { useToast } from "../components/ui/Toast";

/**
 * Session + role for the whole app. Hiding admin-only buttons is cosmetic;
 * the backend enforces every rule.
 *
 * How long a sign-in lasts, for every account: until Sign out is pressed,
 * for as long as the user keeps working, and 12 hours after their last
 * interaction once they stop. A token itself lives a day (JWT_EXPIRY_HOURS);
 * while the user is active it is swapped for a fresh one every 30 minutes,
 * so an active user's token never runs out. Activity is shared by every tab
 * (localStorage), so working in one tab keeps them all signed in.
 */

const IDLE_LIMIT_MS = 12 * 60 * 60 * 1000;
const REFRESH_AFTER_MS = 30 * 60 * 1000;
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

/** When the server issued this token (its `iat` claim), or null if it can't be read. */
function tokenIssuedAt(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.iat === "number" ? payload.iat * 1000 : null;
  } catch {
    return null;
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
  const signedIn = user !== null;
  // Last refresh attempt in this tab. Throttles refreshes by the local clock
  // too, so a device clock that disagrees with the server's can never turn
  // "token older than 30 minutes" into a refresh on every interaction.
  const lastRefreshAttemptRef = useRef(0);

  useEffect(() => {
    if (!signedIn) return;
    let lastWrite = 0;
    const maybeRefresh = () => {
      const token = getStoredAuthToken();
      const now = Date.now();
      if (!token || now - lastRefreshAttemptRef.current < REFRESH_AFTER_MS) return;
      const issuedAt = tokenIssuedAt(token);
      // A negative age means this device's clock runs behind the server's —
      // the age can't be trusted, so the local throttle above decides alone.
      const age = issuedAt === null ? null : now - issuedAt;
      if (age !== null && age >= 0 && age < REFRESH_AFTER_MS) return;
      lastRefreshAttemptRef.current = now;
      authApi
        .refresh()
        .then((result) => {
          // Only if nothing replaced this session meanwhile (sign-out,
          // sign-in as someone else, a password change).
          if (getStoredAuthToken() === token) replaceSession(result);
        })
        // A 401 already ends the session app-wide (api/client.ts); anything
        // else (offline, server restarting) just waits for the next try —
        // the current token is still valid for hours.
        .catch(() => {});
    };
    const markActive = () => {
      const now = Date.now();
      if (now - lastWrite > 15_000) {
        lastWrite = now;
        writeActivity(now);
        maybeRefresh();
      }
    };
    const check = () => {
      const last = readActivity();
      if (last && Date.now() - last > IDLE_LIMIT_MS) {
        logout("You were signed out after 12 hours without activity.");
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
  }, [signedIn, logout, replaceSession]);

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
