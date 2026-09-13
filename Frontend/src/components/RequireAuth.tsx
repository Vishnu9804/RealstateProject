import { Navigate, Outlet, useLocation } from "react-router-dom";
import Scene from "./Scene";
import { Button } from "./ui/Primitives";
import { IconRefresh } from "./ui/Icons";
import { useAuth } from "../state/AuthProvider";

export default function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();

  if (auth.isRestoring) return null;

  if (auth.isOffline) {
    return (
      <>
        <Scene />
        <div className="login-page">
          <div className="panel panel--pad panel--raised anim-rise login-card" role="status">
            <h1 className="login-card__title">Can't reach the server</h1>
            <p className="login-card__sub">You're still signed in. Retrying every few seconds…</p>
            <Button variant="primary" icon={<IconRefresh size={15} />} onClick={auth.retry}>
              Retry now
            </Button>
          </div>
        </div>
      </>
    );
  }

  if (!auth.user) {
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  }

  return <Outlet />;
}
