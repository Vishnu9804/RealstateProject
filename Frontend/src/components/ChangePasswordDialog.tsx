import { useState } from "react";
import { createPortal } from "react-dom";
import { authApi } from "../api/authApi";
import { plainError } from "../lib/apiError";
import { useAuth } from "../state/AuthProvider";
import { useToast } from "./ui/Toast";
import { Button, Note } from "./ui/Primitives";
import { IconLock, IconX } from "./ui/Icons";

const labelStyle = { fontWeight: 560, color: "var(--ink-2)" };

/** Owner-only: needs the current password; ends every other session. */
export default function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const auth = useAuth();
  const toast = useToast();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mismatch = confirmPassword.length > 0 && confirmPassword !== newPassword;
  const canSave = currentPassword.length > 0 && newPassword.length >= 8 && newPassword === confirmPassword && !saving;

  async function handleSave() {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      const result = await authApi.changeOwnPassword({ current_password: currentPassword, new_password: newPassword });
      auth.replaceSession(result);
      toast.push({ tone: "ok", title: "Password changed", message: "Every other device was signed out." });
      onClose();
    } catch (err) {
      setError(plainError(err));
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="modal anim-rise" role="dialog" aria-modal="true" aria-label="Change your password">
        <div className="modal__head">
          <span className="modal__title">Change your password</span>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={13} />
          </button>
        </div>

        <form
          className="modal__body stack stack-4"
          onSubmit={(event) => {
            event.preventDefault();
            void handleSave();
          }}
        >
          <div className="field">
            <label className="field__hint" style={labelStyle} htmlFor="current-password">
              Current password
            </label>
            <input
              id="current-password"
              className="input"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoFocus
              autoComplete="current-password"
            />
          </div>
          <div className="field">
            <label className="field__hint" style={labelStyle} htmlFor="new-password">
              New password
            </label>
            <input
              id="new-password"
              className="input"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="At least 8 characters"
              autoComplete="new-password"
            />
          </div>
          <div className="field">
            <label className="field__hint" style={labelStyle} htmlFor="confirm-password">
              Confirm new password
            </label>
            <input
              id="confirm-password"
              className="input"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />
            {mismatch && <span className="field__hint">The two passwords don't match.</span>}
          </div>
          {error && <Note tone="bad">{error}</Note>}
          <button type="submit" hidden />
        </form>

        <div className="modal__foot">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <Button size="sm" variant="primary" icon={<IconLock size={14} />} onClick={handleSave} busy={saving} disabled={!canSave}>
            Save
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
