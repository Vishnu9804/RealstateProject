import { createContext, useCallback, useContext, useRef, useState } from "react";
import { ApiError, getStoredAuthToken } from "../api/client";
import type { OwnerVerificationGrant } from "../api/types";
import OwnerVerificationDialog from "../components/OwnerVerificationDialog";

export class VerificationCancelledError extends Error {}

type RunVerified = <T>(reason: string, action: (grant: string) => Promise<T>) => Promise<T>;

const OwnerVerificationContext = createContext<RunVerified | null>(null);

export function useOwnerVerification(): RunVerified {
  const run = useContext(OwnerVerificationContext);
  if (!run) throw new Error("useOwnerVerification must be used inside <OwnerVerificationProvider>");
  return run;
}

interface HeldGrant {
  token: string;
  expiresAt: number;
  session: string | null;
}

/**
 * Runs a credential change with owner verification: reuses a still-valid
 * grant for this same session, otherwise asks for one, and asks again once
 * if the server says it has expired (428). Grants live in memory only.
 */
export function OwnerVerificationProvider({ children }: { children: React.ReactNode }) {
  const grantRef = useRef<HeldGrant | null>(null);
  const [prompt, setPrompt] = useState<{ reason: string; resolve: (grant: string | null) => void } | null>(null);

  const ask = useCallback((reason: string) => new Promise<string | null>((resolve) => setPrompt({ reason, resolve })), []);

  const run = useCallback(
    async <T,>(reason: string, action: (grant: string) => Promise<T>): Promise<T> => {
      const held = grantRef.current;
      const reusable = held !== null && held.session === getStoredAuthToken() && held.expiresAt - Date.now() > 15_000;
      const grant = reusable && held ? held.token : await ask(reason);
      if (!grant) throw new VerificationCancelledError();
      try {
        return await action(grant);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 428)) throw err;
        grantRef.current = null;
        const fresh = await ask(reason);
        if (!fresh) throw new VerificationCancelledError();
        return action(fresh);
      }
    },
    [ask],
  );

  function finish(grant: OwnerVerificationGrant | null) {
    if (!prompt) return;
    if (grant) {
      grantRef.current = {
        token: grant.verification_token,
        expiresAt: Date.now() + grant.expires_in_seconds * 1000,
        session: getStoredAuthToken(),
      };
    }
    prompt.resolve(grant?.verification_token ?? null);
    setPrompt(null);
  }

  return (
    <OwnerVerificationContext.Provider value={run}>
      {children}
      {prompt && <OwnerVerificationDialog reason={prompt.reason} onDone={finish} />}
    </OwnerVerificationContext.Provider>
  );
}
