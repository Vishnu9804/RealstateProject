import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Scene from "../components/Scene";
import { Button, Note } from "../components/ui/Primitives";
import { IconLock, IconSend, IconZap } from "../components/ui/Icons";
import { authApi } from "../api/authApi";
import { plainError } from "../lib/apiError";
import { useAuth } from "../state/AuthProvider";

const labelStyle = { fontWeight: 560, color: "var(--ink-2)" };

/** Sign-in, plus owner password reset by a WhatsApp code sent to the owner's number. No self-registration. */
export default function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<"signin" | "recover">("signin");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  if (!auth.isRestoring && auth.user) return <Navigate to="/dashboard" replace />;

  function switchMode(next: "signin" | "recover") {
    setMode(next);
    setError(null);
    setInfo(null);
  }

  async function signIn() {
    if (!username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await auth.login(username.trim(), password);
      const from = (location.state as { from?: string } | null)?.from ?? "/dashboard";
      navigate(from, { replace: true });
    } catch (err) {
      setError(plainError(err));
      setBusy(false);
    }
  }

  async function sendResetCode() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const result = await authApi.requestRecoveryCode();
      if (result.status === "sent" || result.status === "cooldown") {
        setCodeSent(true);
        setInfo(
          result.status === "sent"
            ? `Code sent to the owner's WhatsApp ending in ${result.phone_hint?.slice(-2)}.`
            : `A code was sent recently — use it, or request another in ${Math.ceil(result.retry_after_seconds / 60)} min.`,
        );
      } else if (result.status === "unavailable") {
        setError("No WhatsApp number is connected to the server, so a reset code can't be sent.");
      } else {
        setError("Password reset by WhatsApp isn't set up on this server (ADMIN_PHONE).");
      }
    } catch (err) {
      setError(plainError(err));
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword() {
    if (newPassword !== confirmPassword) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await authApi.resetWithRecoveryCode({ code, new_password: newPassword });
      setUsername(result.username);
      setPassword("");
      setCode("");
      setNewPassword("");
      setConfirmPassword("");
      setCodeSent(false);
      setMode("signin");
      setInfo(`Password reset for "${result.username}". Sign in with your new password.`);
    } catch (err) {
      setError(plainError(err));
    } finally {
      setBusy(false);
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (mode === "signin") void signIn();
    else if (codeSent) void resetPassword();
    else void sendResetCode();
  }

  const canReset = /^\d{6}$/.test(code) && newPassword.length >= 8 && confirmPassword.length > 0;

  return (
    <>
      <Scene />
      <div className="login-page">
        <form className="panel panel--pad panel--raised anim-rise login-card" onSubmit={handleSubmit}>
          <div className="login-card__brand">
            <span className="brand__mark">
              <IconZap size={19} />
            </span>
            <span className="brand__text">
              <span className="brand__name">Estate Signal</span>
              <span className="brand__sub">WhatsApp intake</span>
            </span>
          </div>

          {mode === "signin" ? (
            <>
              <h1 className="login-card__title">Sign in</h1>
              <p className="login-card__sub">Use the username and password the owner gave you.</p>

              {info && <Note tone="ok">{info}</Note>}

              <div className="field">
                <label className="field__hint" style={labelStyle} htmlFor="login-username">
                  Username
                </label>
                <input
                  id="login-username"
                  className="input"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoFocus
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </div>

              <div className="field">
                <label className="field__hint" style={labelStyle} htmlFor="login-password">
                  Password
                </label>
                <input
                  id="login-password"
                  className="input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>

              {error && <Note tone="bad">{error}</Note>}

              <Button
                type="submit"
                variant="primary"
                icon={<IconLock size={15} />}
                busy={busy}
                disabled={!username.trim() || !password}
                style={{ width: "100%", justifyContent: "center" }}
              >
                Sign in
              </Button>
              <button type="button" className="text-link" style={{ alignSelf: "center" }} onClick={() => switchMode("recover")}>
                Forgot password?
              </button>
            </>
          ) : (
            <>
              <h1 className="login-card__title">Reset owner password</h1>
              <p className="login-card__sub">
                A reset code goes to the owner's WhatsApp. Employees: ask the owner to reset your password.
              </p>

              {info && <Note tone="ok">{info}</Note>}

              {codeSent && (
                <>
                  <div className="field">
                    <label className="field__hint" style={labelStyle} htmlFor="reset-code">
                      6-digit code
                    </label>
                    <input
                      id="reset-code"
                      className="input code-input"
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      autoFocus
                    />
                  </div>
                  <div className="field">
                    <label className="field__hint" style={labelStyle} htmlFor="reset-new">
                      New password
                    </label>
                    <input
                      id="reset-new"
                      className="input"
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="At least 8 characters"
                      autoComplete="new-password"
                    />
                  </div>
                  <div className="field">
                    <label className="field__hint" style={labelStyle} htmlFor="reset-confirm">
                      Confirm new password
                    </label>
                    <input
                      id="reset-confirm"
                      className="input"
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      autoComplete="new-password"
                    />
                  </div>
                </>
              )}

              {error && <Note tone="bad">{error}</Note>}

              {codeSent ? (
                <Button
                  type="submit"
                  variant="primary"
                  icon={<IconLock size={15} />}
                  busy={busy}
                  disabled={!canReset}
                  style={{ width: "100%", justifyContent: "center" }}
                >
                  Reset password
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="primary"
                  icon={<IconSend size={15} />}
                  busy={busy}
                  style={{ width: "100%", justifyContent: "center" }}
                >
                  Send reset code
                </Button>
              )}
              <button type="button" className="text-link" style={{ alignSelf: "center" }} onClick={() => switchMode("signin")}>
                Back to sign in
              </button>
            </>
          )}
        </form>
      </div>
    </>
  );
}
