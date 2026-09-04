import { useState } from "react";
import { instagramApi, type InstagramStatusResponse } from "../api/instagramApi";
import { usePolling } from "../hooks/usePolling";
import { friendlyError } from "../lib/apiError";
import { relativeTime } from "../lib/formatters";
import { useToast } from "./ui/Toast";
import ConfirmDialog from "./ui/ConfirmDialog";
import { Badge, Button, Note, Panel, SkeletonRows } from "./ui/Primitives";
import { IconAlert, IconCheck, IconClock, IconInstagram, IconPower, IconUsers } from "./ui/Icons";

const STATUS_POLL_INTERVAL_MS = 3000;

const CHOICE_LABEL: Record<string, string> = {
  email: "emailed",
  sms: "texted",
};

export default function InstagramConnectionTab() {
  const toast = useToast();
  const [status, setStatus] = useState<InstagramStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);

  usePolling(async () => {
    try {
      const data = await instagramApi.getStatus();
      setStatus((prev) => {
        // A connect/verify submission already knows the freshest stage from
        // its own response — a poll landing a moment later must not stomp
        // that back to something stale while the next tick catches up.
        if (submitting) return prev;
        return data;
      });
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }, STATUS_POLL_INTERVAL_MS);

  async function handleConnect() {
    if (!username.trim() || !password) return;
    setSubmitting(true);
    try {
      const data = await instagramApi.connect(username.trim(), password);
      setStatus(data);
      setPassword("");
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't start connecting", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitCode() {
    if (!code.trim()) return;
    setSubmitting(true);
    try {
      const data = await instagramApi.submitCode(code.trim());
      setStatus(data);
      setCode("");
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't submit the code", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRetryApproval() {
    setSubmitting(true);
    try {
      const data = await instagramApi.retryApproval();
      setStatus(data);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't retry", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleStartOver() {
    setSubmitting(true);
    try {
      const data = await instagramApi.startOver();
      setStatus(data);
      setPassword("");
      setCode("");
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't reset", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDisconnect() {
    setDisconnecting(true);
    try {
      const data = await instagramApi.disconnect();
      setStatus(data);
      setConfirmDisconnect(false);
      toast.push({ tone: "ok", title: "Instagram disconnected", message: "Reel comments and DMs will no longer be handled." });
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't disconnect", message: friendlyError(err) });
    } finally {
      setDisconnecting(false);
    }
  }

  const loading = status === null && error === null;
  const stage = status?.stage ?? "disconnected";

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Step 1 — Connection</div>
          <h1 className="page-title">Instagram connection</h1>
          <p className="section-head__sub">
            Connect your client's Instagram account once — reel comments and shared reels get matched to your
            properties and answered automatically from then on, for as long as this stays connected.
          </p>
        </div>
      </header>

      {error && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {error}
        </Note>
      )}

      {loading ? (
        <Panel>
          <SkeletonRows rows={4} />
        </Panel>
      ) : stage === "connected" && status ? (
        <Panel raised tilt className="stack stack-5">
          <div className="row-between">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                Live state
              </div>
              <h2>@{status.username ?? "connected account"}</h2>
            </div>
            <Badge tone="ok" live>
              connected
            </Badge>
          </div>

          <div className="stat-grid">
            {status.connected_at && (
              <Stat label="Connected" value={relativeTime(new Date(status.connected_at))} icon={<IconUsers size={13} />} />
            )}
            {status.last_verified_at && (
              <Stat label="Last checked" value={relativeTime(new Date(status.last_verified_at))} icon={<IconClock size={13} />} />
            )}
          </div>

          <Note tone="ok" icon={<IconCheck size={16} />}>
            This stays connected on its own — no need to log in again unless you disconnect it here or the client
            signs it out from Instagram's own app.
          </Note>

          <div className="row-flex">
            <Button className="btn--danger" icon={<IconPower size={15} />} onClick={() => setConfirmDisconnect(true)}>
              Disconnect
            </Button>
          </div>
        </Panel>
      ) : stage === "awaiting_code" && status ? (
        <Panel raised className="stack stack-4" style={{ alignItems: "flex-start", maxWidth: 420 }}>
          <div className="section-head__eyebrow">Verification needed</div>
          <p className="section-head__sub" style={{ margin: 0 }}>
            Instagram {status.code_kind === "2fa" ? "wants the 2FA code for" : "doesn't recognize this login and"}{" "}
            {status.code_kind === "2fa"
              ? "this account."
              : `${CHOICE_LABEL[status.code_choice ?? ""] ?? "sent"} a verification code to the client — ask them for it.`}
          </p>
          <div className="field" style={{ width: "100%" }}>
            <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
              Verification code
            </label>
            <input
              className="input"
              inputMode="numeric"
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmitCode()}
              placeholder="6-digit code"
            />
          </div>
          <Button variant="primary" onClick={handleSubmitCode} busy={submitting} disabled={!code.trim()}>
            Submit code
          </Button>
        </Panel>
      ) : stage === "manual_verification_required" && status ? (
        <Panel raised className="stack stack-4" style={{ alignItems: "flex-start", maxWidth: 460 }}>
          <div className="section-head__eyebrow">Approval needed in the Instagram app</div>
          <Note tone="warn" icon={<IconAlert size={16} />}>
            {status.error_message ??
              "Instagram wants this login approved from inside the real Instagram app before it will let it through."}
          </Note>
          <p className="section-head__sub" style={{ margin: 0 }}>
            Open Instagram (the app, or instagram.com) and log in normally as <strong>@{status.username}</strong> —
            on the client's own phone if possible. Approve or dismiss whatever "was this you?" prompt it shows, then
            come back here and click Retry.
          </p>
          <div className="row-flex">
            <Button variant="primary" onClick={handleRetryApproval} busy={submitting}>
              I've approved it — Retry
            </Button>
            <Button variant="ghost" onClick={handleStartOver} disabled={submitting}>
              Start over
            </Button>
          </div>
        </Panel>
      ) : stage === "connecting" ? (
        <Panel className="stack stack-3">
          <div className="row-flex faint small">
            <span className="spinner" /> Connecting to Instagram…
          </div>
        </Panel>
      ) : (
        <Panel raised className="stack stack-4" style={{ alignItems: "flex-start", maxWidth: 420 }}>
          <div className="section-head__eyebrow">Not connected</div>

          {stage === "error" && status?.error_message && (
            <Note tone="bad" icon={<IconAlert size={16} />}>
              {status.error_message}
            </Note>
          )}

          <Note tone="warn" icon={<IconAlert size={16} />}>
            This logs in the same way the official Instagram app would, using the client's own username and
            password — it isn't Instagram's official developer API. That's what makes "just enter ID and password"
            possible with no separate setup, but it also means Instagram may occasionally ask for a one-time
            verification code (handled right here) on the first login, and there's a small standing risk of the
            account being challenged again later. Use an account the client is comfortable connecting this way.
          </Note>

          <div className="field" style={{ width: "100%" }}>
            <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
              Instagram username
            </label>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="clientbusiness" autoComplete="username" />
          </div>
          <div className="field" style={{ width: "100%" }}>
            <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
              Instagram password
            </label>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleConnect()}
              placeholder="••••••••"
              autoComplete="current-password"
            />
          </div>

          <Button
            variant="primary"
            icon={<IconInstagram size={16} />}
            onClick={handleConnect}
            busy={submitting}
            disabled={!username.trim() || !password}
          >
            Connect Instagram
          </Button>
        </Panel>
      )}

      {confirmDisconnect && (
        <ConfirmDialog
          title="Disconnect Instagram?"
          body={
            <p>
              Reel comments and shared reels will stop being answered until this is connected again. This does not
              affect anything already stored in your Properties or Inquiries.
            </p>
          }
          confirmLabel="Disconnect"
          tone="danger"
          busy={disconnecting}
          onConfirm={handleDisconnect}
          onClose={() => !disconnecting && setConfirmDisconnect(false)}
        />
      )}
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <div className="stat anim-rise">
      <div className="stat__label">
        {icon}
        {label}
      </div>
      <div className="stat__value">{value}</div>
    </div>
  );
}
