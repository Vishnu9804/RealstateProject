import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Scene from "../components/Scene";
import { Button, Note } from "../components/ui/Primitives";
import { IconLock, IconZap } from "../components/ui/Icons";
import { plainError } from "../lib/apiError";
import { useAuth } from "../state/AuthProvider";

const labelStyle = { fontWeight: 560, color: "var(--ink-2)" };

/** Sign-in only. No self-registration and no password recovery — the admin
 *  starts with the .env password and changes it from inside the app once
 *  signed in; a forgotten employee password is reset by the admin. */
export default function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!auth.isRestoring && auth.user) return <Navigate to="/properties" replace />;

  async function signIn() {
    if (!username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await auth.login(username.trim(), password);
      const from = (location.state as { from?: string } | null)?.from ?? "/properties";
      navigate(from, { replace: true });
    } catch (err) {
      setError(plainError(err));
      setBusy(false);
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    void signIn();
  }

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
              <span className="brand__name">Manibhadra Real Estate</span>
              <span className="brand__sub">WhatsApp intake</span>
            </span>
          </div>

          <h1 className="login-card__title">Sign in</h1>
          <p className="login-card__sub">Use the username and password the owner gave you.</p>

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
        </form>
      </div>
    </>
  );
}
