import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { authApi } from "../api/authApi";
import type { OwnerVerificationGrant, OwnerVerificationStatus } from "../api/types";
import { plainError } from "../lib/apiError";
import { Button, Note } from "./ui/Primitives";
import { IconLock, IconSend, IconX } from "./ui/Icons";

const labelStyle = { fontWeight: 560, color: "var(--ink-2)" };

function formatWait(seconds: number): string {
  return seconds < 90 ? `${seconds}s` : `${Math.ceil(seconds / 60)} min`;
}

export default function OwnerVerificationDialog({
  reason,
  onDone,
}: {
  reason: string;
  onDone: (grant: OwnerVerificationGrant | null) => void;
}) {
  const [status, setStatus] = useState<OwnerVerificationStatus | null>(null);
  const [codeSent, setCodeSent] = useState(false);
  const [phoneHint, setPhoneHint] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    authApi
      .getOwnerVerification()
      .then((result) => {
        setStatus(result);
        setPhoneHint(result.phone_hint);
      })
      .catch((err) => setError(plainError(err)));
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = window.setTimeout(() => setCooldown((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(id);
  }, [cooldown]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.stopPropagation();
        onDone(null);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onDone]);

  const byWhatsApp = status?.method === "whatsapp";
  const blocked = byWhatsApp && !status?.available;
  const lastDigits = phoneHint?.slice(-2);
  const canConfirm = !busy && (byWhatsApp ? codeSent && /^\d{6}$/.test(value) : status?.method === "password" && value.length > 0);

  async function sendCode() {
    setBusy(true);
    setError(null);
    try {
      const result = await authApi.requestOwnerCode();
      if (result.phone_hint) setPhoneHint(result.phone_hint);
      if (result.status === "sent") {
        setCodeSent(true);
        setCooldown(45);
      } else if (result.status === "cooldown") {
        setCodeSent(true);
        setCooldown(result.retry_after_seconds);
        setError(`A code was sent recently. Use it, or request another in ${formatWait(result.retry_after_seconds)}.`);
      } else {
        setError("A code can't be sent right now — no WhatsApp number is connected.");
      }
    } catch (err) {
      setError(plainError(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!canConfirm) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await authApi.confirmOwnerVerification(byWhatsApp ? { code: value } : { password: value }));
    } catch (err) {
      setError(plainError(err));
      setBusy(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !busy && onDone(null)}>
      <div className="modal anim-rise" role="dialog" aria-modal="true" aria-label="Confirm it's the owner">
        <div className="modal__head">
          <span className="modal__title">Confirm it's the owner</span>
          <button type="button" className="toast__close" onClick={() => onDone(null)} disabled={busy} aria-label="Close">
            <IconX size={13} />
          </button>
        </div>

        <form
          className="modal__body stack stack-4"
          onSubmit={(event) => {
            event.preventDefault();
            void confirm();
          }}
        >
          <p>{reason}</p>

          {!status && !error && (
            <p className="faint small">
              <span className="spinner" /> Checking verification…
            </p>
          )}

          {blocked && (
            <Note tone="bad">
              {status?.reason === "invalid_owner_phone"
                ? "ADMIN_PHONE in the server's .env isn't a valid phone number, so no code can be sent."
                : "No WhatsApp number is connected, so the code can't be sent. Link a number on the Connection page, then try again."}
            </Note>
          )}

          {byWhatsApp && !blocked && !codeSent && (
            <p>
              We'll send a 6-digit code to the owner's WhatsApp number ending in <strong>{lastDigits}</strong>.
            </p>
          )}

          {byWhatsApp && codeSent && (
            <div className="field">
              <label className="field__hint" style={labelStyle} htmlFor="owner-code">
                Code sent to WhatsApp ending in {lastDigits}
              </label>
              <input
                id="owner-code"
                className="input code-input"
                value={value}
                onChange={(event) => setValue(event.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
              />
              <button type="button" className="text-link" onClick={sendCode} disabled={busy || cooldown > 0}>
                {cooldown > 0 ? `Resend in ${formatWait(cooldown)}` : "Resend code"}
              </button>
            </div>
          )}

          {status?.method === "password" && (
            <div className="field">
              <label className="field__hint" style={labelStyle} htmlFor="owner-password">
                Your password (owner phone verification isn't set up)
              </label>
              <input
                id="owner-password"
                className="input"
                type="password"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                autoComplete="current-password"
                autoFocus
              />
            </div>
          )}

          {error && <Note tone="bad">{error}</Note>}
          <button type="submit" hidden />
        </form>

        <div className="modal__foot">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onDone(null)} disabled={busy}>
            Cancel
          </button>
          {byWhatsApp && !codeSent ? (
            <Button size="sm" variant="primary" icon={<IconSend size={14} />} onClick={sendCode} busy={busy} disabled={blocked}>
              Send code
            </Button>
          ) : (
            <Button size="sm" variant="primary" icon={<IconLock size={14} />} onClick={confirm} busy={busy} disabled={!canConfirm}>
              Confirm
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
