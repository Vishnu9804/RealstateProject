import { useState } from "react";
import { createPortal } from "react-dom";
import { authApi, type UpdateEmployeeRequest } from "../api/authApi";
import type { UserSummary } from "../api/types";
import { plainError } from "../lib/apiError";
import { useOwnerVerification, VerificationCancelledError } from "../state/OwnerVerificationProvider";
import { useToast } from "./ui/Toast";
import { Button, Note } from "./ui/Primitives";
import { IconLock, IconPlus, IconX } from "./ui/Icons";

const USERNAME_PATTERN = /^[a-z0-9._-]{3,32}$/;
const labelStyle = { fontWeight: 560, color: "var(--ink-2)" };

/** Add or edit an employee login. Saving requires owner verification (see OwnerVerificationProvider). */
export default function EmployeeFormDialog({
  employee,
  onClose,
  onSaved,
}: {
  employee?: UserSummary;
  onClose: () => void;
  onSaved: (employee: UserSummary) => void;
}) {
  const mode: "add" | "edit" = employee ? "edit" : "add";
  const toast = useToast();
  const verify = useOwnerVerification();
  const [username, setUsername] = useState(employee?.username ?? "");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const usernameValid = USERNAME_PATTERN.test(username);
  const passwordValid = mode === "edit" && password.length === 0 ? true : password.length >= 8;
  const changed = mode === "add" || username !== employee?.username || password.length > 0;
  const canSave = usernameValid && passwordValid && changed && !saving;

  async function handleSave() {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      if (mode === "edit" && employee) {
        const body: UpdateEmployeeRequest = {};
        if (username !== employee.username) body.username = username;
        if (password) body.password = password;
        const updated = await verify(`Update the login for "${employee.username}".`, (grant) =>
          authApi.updateEmployee(employee.user_id, body, grant),
        );
        toast.push({
          tone: "ok",
          title: "Login updated",
          message: password ? `${updated.username} was signed out everywhere and must use the new password.` : updated.username,
        });
        onSaved(updated);
      } else {
        const created = await verify(`Create a login for "${username}".`, (grant) =>
          authApi.createEmployee({ username, password }, grant),
        );
        toast.push({ tone: "ok", title: "Employee added", message: `Give ${created.username} their password directly.` });
        onSaved(created);
      }
    } catch (err) {
      if (!(err instanceof VerificationCancelledError)) setError(plainError(err));
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label={mode === "edit" ? "Edit employee" : "Add an employee"}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Team &amp; access</div>
            <h2 className="detail-modal__title">{mode === "edit" ? "Edit employee login" : "Add an employee"}</h2>
            <div className="detail-modal__sub">
              {mode === "edit"
                ? "Change their username or set a new password. A new password signs them out everywhere."
                : "They sign in with exactly this username and password and can't change either themselves."}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <form
          className="detail-modal__body stack stack-4"
          onSubmit={(event) => {
            event.preventDefault();
            void handleSave();
          }}
        >
          <div className="field">
            <label className="field__hint" style={labelStyle} htmlFor="employee-username">
              Username
            </label>
            <input
              id="employee-username"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase().replace(/\s+/g, ""))}
              autoFocus
              autoComplete="off"
              spellCheck={false}
            />
            <span className="field__hint">3–32 characters: lowercase letters, numbers, dot, dash or underscore.</span>
          </div>

          <div className="field">
            <label className="field__hint" style={labelStyle} htmlFor="employee-password">
              {mode === "edit" ? "New password (optional)" : "Password"}
            </label>
            <div className="row-flex" style={{ gap: 8, flexWrap: "nowrap" }}>
              <input
                id="employee-password"
                className="input"
                style={{ flex: 1 }}
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "edit" ? "Leave blank to keep the current password" : "At least 8 characters"}
                autoComplete="new-password"
              />
              <Button variant="ghost" size="sm" onClick={() => setShowPassword((shown) => !shown)}>
                {showPassword ? "Hide" : "Show"}
              </Button>
            </div>
            {password.length > 0 && password.length < 8 && <span className="field__hint">At least 8 characters.</span>}
          </div>

          {error && <Note tone="bad">{error}</Note>}
          <button type="submit" hidden />
        </form>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button
              variant="primary"
              icon={mode === "edit" ? <IconLock size={14} /> : <IconPlus size={14} />}
              onClick={handleSave}
              busy={saving}
              disabled={!canSave}
            >
              {mode === "edit" ? "Save changes" : "Add employee"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
