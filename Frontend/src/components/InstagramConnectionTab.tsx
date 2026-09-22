import { useState } from "react";
import { instagramApi, type InstagramSetupResponse, type InstagramStatusResponse } from "../api/instagramApi";
import { usePolling } from "../hooks/usePolling";
import { friendlyError } from "../lib/apiError";
import { relativeTime } from "../lib/formatters";
import { useToast } from "./ui/Toast";
import ConfirmDialog from "./ui/ConfirmDialog";
import { Badge, Button, Copyable, Note, Panel, SkeletonRows, Stat } from "./ui/Primitives";
import { IconAlert, IconCheck, IconClock, IconInstagram, IconLink, IconPower, IconRefresh, IconUsers } from "./ui/Icons";
import { useAuth } from "../state/AuthProvider";

/**
 * The Instagram tab of the Connection page.
 *
 * This connects through Meta's OFFICIAL Instagram Platform API. The previous
 * version asked for the account's username and password and drove the
 * private mobile API, which is what made Instagram start warning about
 * automated activity on the very account the business depends on. Nothing
 * here touches a password any more, and nothing polls Instagram: Meta pushes
 * each comment and DM to this backend as it happens.
 *
 * Polled slowly on purpose. The old version refreshed every 3 seconds
 * because a login could move through 2FA and challenge stages while the
 * operator watched. There are no stages left — connecting either succeeds or
 * fails, in one request — so anything faster than this would be the frontend
 * spending hosting CPU on a value that changes about twice a year.
 */
const STATUS_POLL_INTERVAL_MS = 20000;

export default function InstagramConnectionTab() {
  const { isAdmin } = useAuth();
  const toast = useToast();
  const [status, setStatus] = useState<InstagramStatusResponse | null>(null);
  const [setup, setSetup] = useState<InstagramSetupResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);

  usePolling(async () => {
    try {
      const [statusData, setupData] = await Promise.all([instagramApi.getStatus(), instagramApi.getSetup()]);
      setSetup(setupData);
      setStatus((prev) => {
        // A connect submission already knows the freshest stage from its own
        // response — a poll landing a moment later must not stomp that back
        // to something stale while the next tick catches up.
        if (submitting) return prev;
        return statusData;
      });
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }, STATUS_POLL_INTERVAL_MS);

  async function handleConnectToken() {
    if (!token.trim()) return;
    setSubmitting(true);
    try {
      const data = await instagramApi.connectToken(token.trim());
      setStatus(data);
      if (data.stage === "connected") {
        setToken("");
        toast.push({
          tone: "ok",
          title: `Connected as @${data.username}`,
          message: "Reel comments and shared reels will now be answered automatically.",
        });
      } else {
        toast.push({ tone: "bad", title: "Instagram didn't accept that token", message: data.error_message ?? "" });
      }
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't connect", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOAuth() {
    setSubmitting(true);
    try {
      const { url } = await instagramApi.getOAuthUrl();
      // A full navigation, not a popup: Instagram's consent screen refuses to
      // render inside a frame, and the flow ends by redirecting the browser
      // back to this page with ?instagram=connected (handled in
      // ConnectionPage.tsx).
      window.location.href = url;
    } catch (err) {
      toast.push({ tone: "bad", title: "Instagram login isn't configured yet", message: friendlyError(err) });
      setSubmitting(false);
    }
  }

  async function handleResubscribe() {
    setSubmitting(true);
    try {
      const data = await instagramApi.resubscribe();
      setStatus(data);
      toast.push(
        data.webhook_subscribed
          ? { tone: "ok", title: "Webhooks re-checked", message: "Meta is pushing this account's comments and DMs here." }
          : { tone: "bad", title: "Webhooks are not active", message: data.error_message ?? "See the backend terminal for the reason." },
      );
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't re-check webhooks", message: friendlyError(err) });
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
            Connect your client's Instagram professional account once — from then on Meta sends every reel
            comment and every shared reel straight to this server, and each one is matched to a property and
            answered automatically.
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
            {status.token_expires_at && (
              <Stat
                label="Access valid for"
                value={daysUntil(status.token_expires_at)}
                icon={<IconRefresh size={13} />}
                hint="Renewed automatically well before it runs out — nobody ever has to reconnect because of this."
              />
            )}
            <Stat
              label="Live events"
              value={status.webhook_subscribed ? "on" : "not active"}
              tone={status.webhook_subscribed ? "ok" : "warn"}
              icon={<IconLink size={13} />}
              hint={`Meta pushes: ${status.webhook_fields.join(", ")}`}
            />
          </div>

          {status.webhook_subscribed ? (
            <>
              <Note tone="ok" icon={<IconCheck size={16} />}>
                Comments and shared reels arrive here the moment they happen — nothing is polled, and this stays
                connected on its own. You only need to come back here if the client removes this app from their
                Instagram settings.
              </Note>
              {/* This one setting lives inside the Instagram phone app, not
                  in anything this backend or the Meta dashboard can reach or
                  read back — and with it off, Meta delivers no DM webhook at
                  all and reports no error anywhere. Connected, subscribed and
                  completely silent looks identical to a broken integration,
                  so it is called out here rather than left to be rediscovered
                  against a client's live account. */}
              <Note tone="warn" icon={<IconAlert size={16} />}>
                <strong>DMs need one switch on the phone.</strong> On <strong>@{status.username}</strong>, open the
                Instagram app → <em>Settings and activity</em> → <em>Messages and story replies</em> →{" "}
                <em>Message controls</em> → <em>Connected tools</em> → turn <strong>Allow access to messages</strong>{" "}
                ON. Until that is on, shared reels never reach this server — Instagram simply does not send them,
                and shows no error. Comment replies work either way.
              </Note>
            </>
          ) : (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              The account is connected, but Meta is not pushing its events here yet. Check that the callback URL
              below is saved in the app's <strong>Configure webhooks</strong> panel with{" "}
              <code>comments</code> and <code>messages</code> ticked, then press Re-check.
            </Note>
          )}

          <div className="row-flex">
            <Button icon={<IconRefresh size={15} />} onClick={handleResubscribe} busy={submitting}>
              Re-check live events
            </Button>
            {isAdmin && (
              <Button className="btn--danger" icon={<IconPower size={15} />} onClick={() => setConfirmDisconnect(true)}>
                Disconnect
              </Button>
            )}
          </div>

          <SetupDetails setup={setup} />
        </Panel>
      ) : stage === "connecting" ? (
        <Panel className="stack stack-3">
          <div className="row-flex faint small">
            <span className="spinner" /> Connecting to Instagram…
          </div>
        </Panel>
      ) : (
        <Panel raised className="stack stack-4" style={{ alignItems: "flex-start", maxWidth: 560 }}>
          <div className="section-head__eyebrow">Not connected</div>

          {stage === "error" && status?.error_message && (
            <Note tone="bad" icon={<IconAlert size={16} />}>
              {status.error_message}
            </Note>
          )}

          <Note tone="info" icon={<IconInstagram size={16} />}>
            This uses Meta's official Instagram API, so there is no password to hand over and no risk of the
            account being flagged for automated activity. The account must be a <strong>professional</strong>{" "}
            (Business or Creator) Instagram account.
          </Note>

          {status?.oauth_available && (
            <>
              <Button variant="primary" icon={<IconInstagram size={16} />} onClick={handleOAuth} busy={submitting}>
                Connect with Instagram
              </Button>
              <div className="faint small">or paste an access token generated in the Meta App Dashboard:</div>
            </>
          )}

          <div className="field" style={{ width: "100%" }}>
            <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
              Instagram access token
            </label>
            <input
              className="input"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleConnectToken()}
              placeholder="IGAA…"
              autoComplete="off"
              spellCheck={false}
            />
            <div className="field__hint">
              Meta App Dashboard → your app → Instagram → API setup with Instagram business login → step 2,
              “Generate token” next to the connected account.
            </div>
          </div>

          <Button
            variant="primary"
            icon={<IconInstagram size={16} />}
            onClick={handleConnectToken}
            busy={submitting}
            disabled={!token.trim()}
          >
            Connect with this token
          </Button>

          <SetupDetails setup={setup} />
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

/** How much of the access token's life is left, in whole days.
 *
 *  Deliberately not relativeTime(): that formatter clamps a future instant to
 *  "just now", which for a token that is good for another two months would
 *  read as the exact opposite of the truth. */
function daysUntil(iso: string): string {
  const days = Math.round((new Date(iso).getTime() - Date.now()) / 86_400_000);
  if (days <= 0) return "renewing";
  return days === 1 ? "1 day" : `${days} days`;
}

/**
 * The two values that have to be typed into the Meta App Dashboard, printed
 * from the address the browser actually reached this API on.
 *
 * Worth its own block rather than documentation somewhere else: pasting the
 * bare tunnel domain without the /api/instagram/webhook path is by far the
 * most common way this setup fails, and it fails silently — Meta accepts the
 * URL and simply never delivers anything.
 */
function SetupDetails({ setup }: { setup: InstagramSetupResponse | null }) {
  if (!setup) return null;
  return (
    <details className="stack stack-3" style={{ width: "100%" }}>
      <summary className="faint small" style={{ cursor: "pointer" }}>
        Meta App Dashboard settings
      </summary>
      <div className="stack stack-3" style={{ marginTop: 10 }}>
        <div className="field">
          <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
            Callback URL (Configure webhooks)
          </label>
          <Copyable text={setup.callback_url} />
        </div>
        <div className="field">
          <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
            OAuth redirect URI (Business login settings)
          </label>
          <Copyable text={setup.oauth_redirect_uri} />
        </div>
        <div className="faint small">
          Verify token: {setup.verify_token_configured ? "set in Backend/.env" : "NOT SET — add INSTAGRAM_WEBHOOK_VERIFY_TOKEN"}
          {" · "}
          App secret: {setup.app_secret_configured ? "set" : "NOT SET — webhook deliveries cannot be verified"}
          {" · "}
          Subscribed fields: {setup.subscribed_fields.join(", ")}
        </div>
      </div>
    </details>
  );
}
