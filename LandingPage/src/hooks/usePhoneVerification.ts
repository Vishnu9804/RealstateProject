import { useCallback, useState } from "react";
import {
  clearVerifiedPhone,
  readVerifiedPhone,
  samePhone,
  saveVerifiedPhone,
  type VerifiedPhone,
} from "../lib/verifiedPhone";

/**
 * The number-confirmation state both public forms share — the requirements
 * form at the bottom of the home page, and the short enquiry form on a
 * property page. They ask for the same thing (a WhatsApp number nobody has
 * proven yet), so they behave identically about it: one confirmation, and
 * every form on the site is satisfied from then on.
 *
 * Three states, and each one means something different on the page:
 *
 *  - `verified` set   -> a number this browser owns. Prefilled and locked
 *                        everywhere, and the token goes with every submit.
 *  - `bypassed` true  -> we could not send a code at all (no linked
 *                        WhatsApp number on our side). Submissions go
 *                        through unverified, exactly as they did before any
 *                        of this existed — our outage is not the visitor's
 *                        problem, and a lost enquiry is worse than an
 *                        unconfirmed one.
 *  - neither          -> not confirmed yet. The gate is closed.
 *
 * `pendingPhone` being non-null is what renders the dialog; setting it via
 * `startVerification` is the only way to open one, which keeps "a code was
 * requested" and "a dialog is visible" from ever disagreeing.
 */
export function usePhoneVerification() {
  const [verified, setVerified] = useState<VerifiedPhone | null>(() => readVerifiedPhone());
  const [pendingPhone, setPendingPhone] = useState<string | null>(null);
  const [bypassed, setBypassed] = useState(false);

  const startVerification = useCallback((phone: string) => setPendingPhone(phone.trim()), []);
  const cancelVerification = useCallback(() => setPendingPhone(null), []);

  const completeVerification = useCallback((phone: string, token: string, expiresInSeconds: number) => {
    setVerified(saveVerifiedPhone(phone, token, expiresInSeconds));
    setPendingPhone(null);
    setBypassed(false);
  }, []);

  /** Called when the backend says it has no way to send a code right now. */
  const markUnavailable = useCallback(() => {
    setBypassed(true);
    setPendingPhone(null);
  }, []);

  /** The stored token stopped resolving (the server was restarted, or 30
   *  days passed). The number stays on screen — it is still almost
   *  certainly theirs — but it goes back to needing confirmation. */
  const forgetVerification = useCallback(() => {
    clearVerifiedPhone();
    setVerified(null);
  }, []);

  /** Is THIS number the confirmed one? A different number typed over a
   *  confirmed one is unconfirmed again, which is exactly the case this
   *  whole flow exists to catch. */
  const isVerified = useCallback(
    (phone: string) => verified !== null && samePhone(verified.phone, phone),
    [verified],
  );

  return {
    verified,
    pendingPhone,
    bypassed,
    isVerified,
    startVerification,
    cancelVerification,
    completeVerification,
    markUnavailable,
    forgetVerification,
  };
}
