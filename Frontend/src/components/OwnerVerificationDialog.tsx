import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { authApi } from "../api/authApi";
import type { OwnerVerificationGrant } from "../api/types";
import { plainError } from "../lib/apiError";
import { Button, Note } from "./ui/Primitives";
import { IconLock, IconX } from "./ui/Icons";

const labelStyle = { fontWeight: 560, color: "var(--ink-2)" };

/** Confirms it's really the admin before a staff login is added or changed — always by password, no OTP. */
export default function OwnerVerificationDialog({
  reason,
  onDone,
}: {
  reason: string;
  onDone: (grant: OwnerVerificationGrant | null) => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const canConfirm = !busy && value.length > 0;

  async function confirm() {
    if (!canConfirm) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await authApi.confirmOwnerVerification({ password: value }));
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

          <div className="field">
            <label className="field__hint" style={labelStyle} htmlFor="owner-password">
              Your password
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

          {error && <Note tone="bad">{error}</Note>}
          <button type="submit" hidden />
        </form>

        <div className="modal__foot">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onDone(null)} disabled={busy}>
            Cancel
          </button>
          <Button size="sm" variant="primary" icon={<IconLock size={14} />} onClick={confirm} busy={busy} disabled={!canConfirm}>
            Confirm
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
