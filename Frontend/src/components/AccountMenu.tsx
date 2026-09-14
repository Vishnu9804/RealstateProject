import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { authApi } from "../api/authApi";
import { plainError } from "../lib/apiError";
import { useAuth } from "../state/AuthProvider";
import ChangePasswordDialog from "./ChangePasswordDialog";
import { useToast } from "./ui/Toast";
import { Avatar } from "./ui/Primitives";
import { IconLock, IconLogOut, IconPower, IconUsers } from "./ui/Icons";

/** Topbar account button: who is signed in, the owner's account tools, and sign out. */
export default function AccountMenu() {
  const auth = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);
  const [position, setPosition] = useState<{ top: number; right: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = buttonRef.current?.getBoundingClientRect();
      if (rect) setPosition({ top: rect.bottom + 10, right: Math.max(12, window.innerWidth - rect.right) });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const user = auth.user;
  if (!user) return null;

  const pick = (action: () => void) => () => {
    setOpen(false);
    action();
  };

  async function endOtherSessions() {
    try {
      auth.replaceSession(await authApi.endOtherSessions());
      toast.push({ tone: "ok", title: "Other devices signed out", message: "Only this browser is still signed in." });
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't sign out other devices", message: plainError(err) });
    }
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="account-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account menu for ${user.username}`}
        title={`Signed in as ${user.username}`}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        <Avatar name={user.username} size={30} />
        {user.using_initial_password && <span className="account-trigger__dot" aria-hidden="true" />}
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="popover account-menu"
            role="menu"
            aria-label="Account"
            style={{ top: position?.top ?? -9999, right: position?.right ?? 0, visibility: position ? "visible" : "hidden" }}
          >
            <div className="account-menu__head">
              <Avatar name={user.username} size={36} />
              <div className="account-menu__who">
                <span className="account-menu__name">{user.username}</span>
                <span className="account-menu__role">{auth.isAdmin ? "Owner · full access" : "Employee"}</span>
              </div>
            </div>

            {user.using_initial_password && (
              <div className="account-menu__callout account-menu__callout--warn">
                You're still using the initial password from the server setup. Change it now.
              </div>
            )}

            {auth.isAdmin ? (
              <>
                <button type="button" role="menuitem" className="popover__opt" onClick={pick(() => navigate("/team"))}>
                  <IconUsers size={15} /> Team &amp; access
                </button>
                <button type="button" role="menuitem" className="popover__opt" onClick={pick(() => setChangingPassword(true))}>
                  <IconLock size={15} /> Change password
                </button>
                <button type="button" role="menuitem" className="popover__opt" onClick={pick(() => void endOtherSessions())}>
                  <IconPower size={15} /> Sign out other devices
                </button>
              </>
            ) : (
              <div className="account-menu__callout">Only the owner can change your username or password.</div>
            )}

            <div className="account-menu__sep" />
            <button
              type="button"
              role="menuitem"
              className="popover__opt account-menu__danger"
              onClick={pick(() => auth.logout())}
            >
              <IconLogOut size={15} /> Sign out
            </button>
          </div>,
          document.body,
        )}

      {changingPassword && <ChangePasswordDialog onClose={() => setChangingPassword(false)} />}
    </>
  );
}
