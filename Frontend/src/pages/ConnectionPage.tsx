import { cloneElement, isValidElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { whatsappApi } from "../api/whatsappApi";
import type { ConnectionRole, WhatsAppConnection, WhatsAppGroup } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced, usePersistentState, useUnsavedGuard } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { useToast } from "../components/ui/Toast";
import { useAppStatus } from "../state/StatusProvider";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import InstagramConnectionTab from "../components/InstagramConnectionTab";
import {
  Badge,
  Button,
  Check,
  EmptyState,
  Highlight,
  Note,
  Panel,
  SearchInput,
  Segmented,
  SkeletonRows,
} from "../components/ui/Primitives";
import {
  IconAlert,
  IconBuilding,
  IconCheck,
  IconDatabase,
  IconInbox,
  IconInfo,
  IconInstagram,
  IconMessage,
  IconPhone,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconTrash,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

type ConnectionTab = "whatsapp" | "instagram";

/**
 * Both connection channels live under this one page, as tabs — the
 * Connection page is "how do messages/inquiries get in", and WhatsApp and
 * Instagram are just two sources feeding the same property/inquiry
 * pipeline, not two separate concerns.
 *
 * Also picks up the ?instagram=connected|error&message=... redirect
 * Backend/Controller/InstagramInquiryHandlingController/instagram_controller.py's
 * /callback route lands the browser back on after the Instagram OAuth
 * flow, surfaces it as a toast, switches to the Instagram tab, and strips
 * the query string so a page refresh doesn't replay the same toast.
 */
export default function ConnectionPage() {
  const [tab, setTab] = usePersistentState<ConnectionTab>("connection.tab", "whatsapp");
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const result = params.get("instagram");
    if (!result) return;

    if (result === "connected") {
      toast.push({ tone: "ok", title: "Instagram connected", message: "Reel comments and DMs will now be handled automatically." });
    } else if (result === "error") {
      toast.push({ tone: "bad", title: "Instagram connection failed", message: params.get("message") ?? "Please try again." });
    }
    setTab("instagram");
    navigate(location.pathname, { replace: true });
    // Only ever meant to run once, for the redirect this page was just
    // loaded from — re-running on every render would re-fire the toast.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="stack stack-5">
      <Segmented<ConnectionTab>
        ariaLabel="Connection channel"
        value={tab}
        onChange={setTab}
        options={[
          { value: "whatsapp", label: "WhatsApp", icon: <IconMessage size={14} /> },
          { value: "instagram", label: "Instagram", icon: <IconInstagram size={14} /> },
        ]}
      />
      {tab === "whatsapp" ? <WhatsAppTab /> : <InstagramConnectionTab />}
    </div>
  );
}

const QR_POLL_INTERVAL_MS = 3000;
const CONNECTIONS_POLL_INTERVAL_MS = 5000;

/** WhatsApp phone numbers are country code + number, digits only. Catching a
 *  bad entry here — while the user can still see what they typed — beats a
 *  silent no-match hours later when the messages never arrive. */
const VALID_NUMBER = /^\d{8,15}$/;

function displayNumber(connection: WhatsAppConnection): string {
  return connection.phone_number ? `+${connection.phone_number}` : `Connecting… (${connection.connection_id.slice(0, 6)})`;
}

function statusBadgeTone(status: string): "ok" | "warn" | "bad" | "info" {
  if (status === "listening") return "ok";
  if (status === "logged_out" || status === "crashed" || status === "disconnected") return "bad";
  if (status === "waiting_for_qr_scan") return "warn";
  return "info";
}

function statusLabel(status: string): string {
  switch (status) {
    case "listening":
      return "Listening";
    case "fetching_groups":
      return "Loading groups…";
    case "pairing":
      return "Pairing…";
    case "disconnected":
      return "Reconnecting…";
    case "logged_out":
      return "Logged out";
    case "crashed":
      return "Crashed";
    case "starting":
      return "Starting…";
    default:
      return status;
  }
}

function WhatsAppTab() {
  const toast = useToast();
  const { status: appStatus } = useAppStatus();

  const [connections, setConnections] = useState<WhatsAppConnection[] | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [failures, setFailures] = useState(0);
  const [initialLoading, setInitialLoading] = useState(true);

  const [qrTick, setQrTick] = useState(0);
  const [qrLoadFailed, setQrLoadFailed] = useState(false);

  const [pendingRoleAction, setPendingRoleAction] = useState<string | null>(null);
  const [unlinkTarget, setUnlinkTarget] = useState<WhatsAppConnection | null>(null);
  const [unlinkBusy, setUnlinkBusy] = useState(false);
  const [onboardingBusy, setOnboardingBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await whatsappApi.getConnections();
      setConnections(data);
      setStatusError(null);
      setFailures(0);
    } catch (err) {
      setStatusError(friendlyError(err));
      setFailures((count) => count + 1);
    } finally {
      setInitialLoading(false);
    }
  }, []);

  usePolling(load, CONNECTIONS_POLL_INTERVAL_MS);

  const backendDown = failures >= 2 && statusError !== null;

  const pending = useMemo(() => connections?.find((c) => c.is_pending) ?? null, [connections]);
  const confirmed = useMemo(() => connections?.filter((c) => !c.is_pending) ?? [], [connections]);
  const propertyConns = useMemo(() => confirmed.filter((c) => c.roles.includes("property")), [confirmed]);
  const inquiryConns = useMemo(() => confirmed.filter((c) => c.roles.includes("inquiry")), [confirmed]);

  // Only polled while a pairing QR is actually up — no point hammering the
  // backend for an image that isn't shown, and no background pairing
  // session runs unless the operator explicitly asked for one.
  usePolling(
    () => {
      setQrTick((t) => t + 1);
      setQrLoadFailed(false);
    },
    QR_POLL_INTERVAL_MS,
    pending !== null,
  );

  // Surfaces the one outcome the connections poll alone can't explain: a
  // pending pairing that silently vanished because nobody scanned it in
  // time (see whatsapp_connection_manager.py's _handle_pairing_timeout) —
  // without this the QR panel would just quietly revert to "Add a number"
  // with no explanation. A pending connection that instead graduated into
  // a confirmed one (the success path) keeps the same connection_id, so it
  // still shows up in `connections` and gets no warning here.
  const prevPendingIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prevId = prevPendingIdRef.current;
    if (prevId && !pending && connections && !connections.some((c) => c.connection_id === prevId)) {
      toast.push({
        tone: "warn",
        title: "QR expired",
        message: "Nobody scanned it in time. Tap “Add a number” to try again.",
      });
    }
    prevPendingIdRef.current = pending?.connection_id ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, connections]);

  async function handleStartOnboarding() {
    setOnboardingBusy(true);
    try {
      const conn = await whatsappApi.startOnboarding();
      setConnections((prev) => [...(prev ?? []).filter((c) => c.connection_id !== conn.connection_id), conn]);
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not start pairing", message: friendlyError(err) });
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function handleCancelOnboarding() {
    try {
      await whatsappApi.cancelOnboarding();
      await load();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not cancel", message: friendlyError(err) });
    }
  }

  async function toggleRole(connection: WhatsAppConnection, role: ConnectionRole, on: boolean) {
    setPendingRoleAction(connection.connection_id);
    try {
      const nextRoles = on
        ? Array.from(new Set([...connection.roles, role]))
        : connection.roles.filter((r) => r !== role);
      await whatsappApi.updateRoles(connection.connection_id, nextRoles);
      await load();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not update this number", message: friendlyError(err) });
    } finally {
      setPendingRoleAction(null);
    }
  }

  async function confirmUnlink() {
    if (!unlinkTarget) return;
    setUnlinkBusy(true);
    try {
      await whatsappApi.unlinkConnection(unlinkTarget.connection_id);
      toast.push({ tone: "ok", title: "Number unlinked", message: `${displayNumber(unlinkTarget)} was logged out and forgotten.` });
      setUnlinkTarget(null);
      await load();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not unlink this number", message: friendlyError(err) });
    } finally {
      setUnlinkBusy(false);
    }
  }

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Connection</div>
          <h1 className="page-title">WhatsApp connection</h1>
          <p className="section-head__sub">
            Link as many WhatsApp numbers as you need, then decide what each one is for. Everything here can be
            changed later without restarting the backend.
          </p>
        </div>
        <Button variant="ghost" icon={<IconRefresh size={15} />} onClick={load}>
          Refresh
        </Button>
      </header>

      {backendDown && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {statusError} — check that{" "}
          <code>uvicorn main:app --reload --host 0.0.0.0 --port 8000</code> is running, then this page will recover on its own.
        </Note>
      )}

      {appStatus && !appStatus.database_configured && (
        <Note tone="warn" icon={<IconDatabase size={17} />}>
          <strong>No database configured.</strong> Messages are being captured, but nothing is being stored — set{" "}
          <code>DATABASE_URL</code> in the backend environment to keep them.
        </Note>
      )}

      <PendingQrPanel
        pending={pending}
        qrTick={qrTick}
        qrLoadFailed={qrLoadFailed}
        setQrLoadFailed={setQrLoadFailed}
        onStart={handleStartOnboarding}
        onCancel={handleCancelOnboarding}
        starting={onboardingBusy}
      />

      <ConnectedNumbersPanel
        connections={confirmed}
        initialLoading={initialLoading}
        onUnlink={setUnlinkTarget}
        unlinkBusyId={unlinkBusy ? unlinkTarget?.connection_id ?? null : null}
      />

      <div className="two-col">
        {/* One role, two pipelines. A number added here has its groups
            fetched once and then feeds whichever of Property / Requirement
            the two pickers below point it at — which is why the block is
            named for both rather than for Property alone. */}
        <RoleBlock
          title="Numbers for Property/Requirement"
          icon={<IconBuilding size={15} />}
          members={propertyConns}
          allConnections={confirmed}
          busyId={pendingRoleAction}
          onAdd={(c) => toggleRole(c, "property", true)}
          onRemove={(c) => toggleRole(c, "property", false)}
          emptyHint="No number is feeding the property or requirement pipeline yet. Tap + and pick a connected number."
        />
        <RoleBlock
          title="Number for Client Inquiry"
          icon={<IconInbox size={15} />}
          members={inquiryConns}
          allConnections={confirmed}
          busyId={pendingRoleAction}
          onAdd={(c) => toggleRole(c, "inquiry", true)}
          onRemove={(c) => toggleRole(c, "inquiry", false)}
          emptyHint="No number is watching for client inquiries yet. Tap + and pick a connected number."
        />
      </div>

      {/* Two independent selections over the SAME numbers and the SAME
          group list. They are rendered as two separate panels, each seeded
          only from its own server-side selection, so a group ticked for
          Property never shows up ticked for Requirement (and vice versa) —
          picking the same group on both sides is allowed and is what routes
          a chat's listings and its requirements to their own pipelines. */}
      <MonitoringPanel kind="property" propertyConns={propertyConns} onSaved={load} />
      <MonitoringPanel kind="requirement" propertyConns={propertyConns} onSaved={load} />

      {inquiryConns.length > 0 && (
        <Note tone="info" icon={<IconInfo size={16} />}>
          Only personal (1:1) chats on {inquiryConns.length === 1 ? "this number" : "these numbers"} are watched for
          client inquiries — group messages are never used for inquiries, on any number, whether or not Property or
          Requirement is also watching them. If a number is used for Property/Requirement too, every personal number
          claimed by either of those selections is left out of Inquiry, so a broker feeding the pipelines never gets
          an automated client reply and nothing is handled twice.
        </Note>
      )}

      {!initialLoading && confirmed.length === 0 && (
        <Note tone="info" icon={<IconInfo size={16} />}>
          Nothing is monitored yet — tap "Add a number" above to link a WhatsApp account, then assign it to Property
          and/or Client Inquiry below.
        </Note>
      )}

      {unlinkTarget && (
        <ConfirmDialog
          title="Unlink this number?"
          tone="danger"
          confirmLabel="Unlink"
          busy={unlinkBusy}
          onClose={() => setUnlinkTarget(null)}
          onConfirm={confirmUnlink}
          body={
            <p>
              <strong>{displayNumber(unlinkTarget)}</strong> will be logged out and removed from Property and Client
              Inquiry. To use it again later, it has to be linked from scratch with a fresh QR scan.
            </p>
          }
        />
      )}
    </div>
  );
}

/* =========================================================================
   Pending QR panel — always running, so a code to link one more number is
   always available.
   ========================================================================= */

function PendingQrPanel({
  pending,
  qrTick,
  qrLoadFailed,
  setQrLoadFailed,
  onStart,
  onCancel,
  starting,
}: {
  pending: WhatsAppConnection | null;
  qrTick: number;
  qrLoadFailed: boolean;
  setQrLoadFailed: (v: boolean) => void;
  onStart: () => void;
  onCancel: () => void;
  starting: boolean;
}) {
  const waitingForQr = pending?.status === "waiting_for_qr_scan";
  const inProgress = pending !== null;

  return (
    <Panel tilt raised className="stack stack-4">
      <div className="row-between">
        <div>
          <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
            Add a number
          </div>
          <h2>Link a WhatsApp number</h2>
          <p className="section-head__sub" style={{ marginTop: 6 }}>
            {inProgress
              ? "Scan with the WhatsApp account you want to link."
              : "Generate a pairing code whenever you're ready to link one more number."}
          </p>
        </div>
        {inProgress ? (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : (
          <Button variant="primary" icon={<IconPlus size={15} />} onClick={onStart} busy={starting}>
            Add a number
          </Button>
        )}
      </div>

      {inProgress && (
        <>
          <div className="two-col">
            <div className="stack stack-4" style={{ alignItems: "center" }}>
              {waitingForQr ? (
                !qrLoadFailed ? (
                  <div className="qr">
                    <img src={whatsappApi.getPendingQrUrl(qrTick)} alt="WhatsApp pairing QR code" onError={() => setQrLoadFailed(true)} />
                    <span className="qr__corner qr__corner--tl" />
                    <span className="qr__corner qr__corner--tr" />
                    <span className="qr__corner qr__corner--bl" />
                    <span className="qr__corner qr__corner--br" />
                  </div>
                ) : (
                  <div className="qr-skeleton">
                    <span className="spinner" style={{ width: 22, height: 22 }} />
                    <span>Waiting for WhatsApp to generate a code…</span>
                  </div>
                )
              ) : (
                <div className="qr-skeleton">
                  <span className="spinner" style={{ width: 22, height: 22 }} />
                  <span>Preparing a pairing code…</span>
                </div>
              )}
            </div>
            <ol className="stack stack-2 small muted" style={{ margin: 0, paddingLeft: 18, alignSelf: "center" }}>
              <li>Open WhatsApp on the phone whose number you want to link.</li>
              <li>
                Go to <strong>Settings → Linked devices</strong>.
              </li>
              <li>
                Tap <strong>Link a device</strong> and scan the code.
              </li>
              <li>The number appears in "Connected numbers" below within a few seconds.</li>
            </ol>
          </div>
          <p className="faint small" style={{ textAlign: "center" }}>
            This code refreshes on its own while it's up. It expires after a few minutes if nobody scans it — just tap
            "Add a number" again if that happens.
          </p>
        </>
      )}
    </Panel>
  );
}

/* =========================================================================
   Connected numbers roster — every linked number, whatever it's used for.
   ========================================================================= */

function ConnectedNumbersPanel({
  connections,
  initialLoading,
  onUnlink,
  unlinkBusyId,
}: {
  connections: WhatsAppConnection[];
  initialLoading: boolean;
  onUnlink: (connection: WhatsAppConnection) => void;
  unlinkBusyId: string | null;
}) {
  return (
    <Panel className="stack stack-3" delay={70}>
      <div className="row-between">
        <h3>
          Connected numbers <span className="faint small">({connections.length})</span>
        </h3>
      </div>
      {initialLoading ? (
        <SkeletonRows rows={3} />
      ) : connections.length === 0 ? (
        <EmptyState icon={<IconPhone size={32} />} title="No numbers linked yet" body="Tap “Add a number” above to link your first WhatsApp number." />
      ) : (
        <div className="stack stack-2">
          {connections.map((connection) => (
            <div key={connection.connection_id} className="row-between" style={{ padding: "8px 2px" }}>
              <div className="row-flex" style={{ gap: 10 }}>
                <Badge tone={statusBadgeTone(connection.status)} live={connection.status === "listening"}>
                  {statusLabel(connection.status)}
                </Badge>
                <span style={{ fontWeight: 600 }}>{displayNumber(connection)}</span>
                <span className="faint small">{connection.joined_groups.length} group(s)</span>
                {connection.roles.length === 0 ? (
                  <span className="faint small">Not assigned yet</span>
                ) : (
                  <div className="row-flex" style={{ gap: 6 }}>
                    {connection.roles.includes("property") && <span className="badge badge--info">Property</span>}
                    {connection.roles.includes("inquiry") && <span className="badge badge--info">Inquiry</span>}
                  </div>
                )}
              </div>
              <Button
                size="sm"
                variant="ghost"
                icon={<IconTrash size={14} />}
                busy={unlinkBusyId === connection.connection_id}
                onClick={() => onUnlink(connection)}
              >
                Unlink
              </Button>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* =========================================================================
   Property / Inquiry blocks — side by side, each with a "+" picker over
   every connected number. Overlap is intentional: the same number can show
   up in both.
   ========================================================================= */

function RoleBlock({
  title,
  icon,
  members,
  allConnections,
  busyId,
  onAdd,
  onRemove,
  emptyHint,
}: {
  title: string;
  icon: React.ReactNode;
  members: WhatsAppConnection[];
  allConnections: WhatsAppConnection[];
  busyId: string | null;
  onAdd: (connection: WhatsAppConnection) => void;
  onRemove: (connection: WhatsAppConnection) => void;
  emptyHint: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const memberIds = useMemo(() => new Set(members.map((c) => c.connection_id)), [members]);

  return (
    <Panel className="stack stack-3" delay={110}>
      <div className="row-between">
        <h3 className="row-flex" style={{ gap: 8 }}>
          {icon} {title} <span className="faint small">({members.length})</span>
        </h3>
        <div ref={anchorRef} style={{ display: "inline-block" }}>
          <Button size="sm" variant="ghost" icon={<IconPlus size={14} />} onClick={() => setMenuOpen((o) => !o)}>
            Add
          </Button>
        </div>
        {menuOpen && anchorRef.current && (
          <NumberPickerMenu
            anchorEl={anchorRef.current}
            options={allConnections}
            alreadySelected={memberIds}
            onPick={(connection) => onAdd(connection)}
            onClose={() => setMenuOpen(false)}
          />
        )}
      </div>

      {members.length === 0 ? (
        <EmptyState icon={isValidElement(icon) ? cloneElement(icon, { size: 32 } as { size: number }) : icon} title="Nothing here yet" body={emptyHint} />
      ) : (
        <div className="stack stack-2">
          {members.map((connection) => (
            <div key={connection.connection_id} className="row-between" style={{ padding: "6px 2px" }}>
              <div className="row-flex" style={{ gap: 8 }}>
                <Badge tone={statusBadgeTone(connection.status)} live={connection.status === "listening"}>
                  {statusLabel(connection.status)}
                </Badge>
                <span style={{ fontWeight: 600 }}>{displayNumber(connection)}</span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                iconOnly
                icon={<IconX size={13} />}
                aria-label={`Remove ${displayNumber(connection)} from ${title}`}
                busy={busyId === connection.connection_id}
                onClick={() => onRemove(connection)}
              />
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function NumberPickerMenu({
  anchorEl,
  options,
  alreadySelected,
  onPick,
  onClose,
}: {
  anchorEl: HTMLElement;
  options: WhatsAppConnection[];
  alreadySelected: Set<string>;
  onPick: (connection: WhatsAppConnection) => void;
  onClose: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [query, setQuery] = useState("");

  useLayoutEffect(() => {
    const place = () => {
      const rect = anchorEl.getBoundingClientRect();
      const width = 296;
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
      const top = rect.bottom + 8;
      setPosition({ left, top });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchorEl]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (popoverRef.current?.contains(target) || anchorEl.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [anchorEl, onClose]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((o) => displayNumber(o).toLowerCase().includes(needle));
  }, [options, query]);

  return createPortal(
    <div
      ref={popoverRef}
      className="popover"
      role="dialog"
      aria-label="Pick a connected number"
      style={{ left: position?.left ?? -9999, top: position?.top ?? -9999, visibility: position ? "visible" : "hidden" }}
    >
      <div className="popover__head">
        <span className="popover__title">Pick a connected number</span>
        <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
          <IconX size={13} />
        </button>
      </div>

      <div className="popover__search">
        <IconSearch size={14} />
        <input
          autoFocus
          className="popover__search-input"
          value={query}
          placeholder="Search connected numbers…"
          aria-label="Search connected numbers"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <div className="popover__list">
        {options.length === 0 && <p className="popover__empty">No numbers connected yet — tap "Add a number" above first.</p>}
        {options.length > 0 && filtered.length === 0 && <p className="popover__empty">No match for “{query}”.</p>}
        {filtered.map((option) => {
          const already = alreadySelected.has(option.connection_id);
          return (
            <button
              key={option.connection_id}
              type="button"
              className={`popover__opt${already ? " popover__opt--on" : ""}`}
              disabled={already}
              onClick={() => {
                onPick(option);
                onClose();
              }}
            >
              <span className="popover__tick" aria-hidden="true">
                {already && <IconCheck size={11} strokeWidth={3} />}
              </span>
              <span className="popover__opt-text">
                <span>{displayNumber(option)}</span>
                <span className="popover__opt-detail">
                  {option.joined_groups.length} group(s){already ? " · already added" : ""}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>,
    document.body,
  );
}

/* =========================================================================
   Monitoring selection — groups aggregated across every Property-role
   connection, plus a shared personal-numbers list.

   Rendered TWICE, once per kind: "property" (which chats feed the property
   pipeline) and "requirement" (which chats feed the broker-requirement
   pipeline). Both read the same numbers and the same joined-group list, and
   both write through the same shape of endpoint — but each instance is
   seeded ONLY from its own server-side selection and saves ONLY its own.

   That independence is the whole point, and it is why this is one
   parameterised component rather than two hand-copied ones: the two panels
   must behave identically in every respect except which set they read and
   write, so a group ticked under Property renders completely untouched
   under Requirement, and selecting it there too is a normal, expected
   thing to do rather than something the UI quietly prevents or pre-fills.
   ========================================================================= */

interface TaggedGroup extends WhatsAppGroup {
  connectionId: string;
  connectionLabel: string;
}

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const value of a) if (!b.has(value)) return false;
  return true;
}

type MonitoringKind = "property" | "requirement";

/** Everything that differs between the two panels, in one place — so the
 *  behaviour below can be read once and trusted for both. */
const MONITORING_CONFIG: Record<
  MonitoringKind,
  {
    eyebrow: string;
    heading: string;
    blurb: string;
    groupsHint: string;
    personalHint: string;
    emptyTitle: string;
    emptyBody: string;
    savedTitle: string;
    stopWarning: string;
    groupJidsOf: (connection: WhatsAppConnection) => string[];
    personalNumbersOf: (connection: WhatsAppConnection) => string[];
    save: (connectionId: string, groupJids: string[], personalNumbers: string[]) => Promise<WhatsAppConnection>;
  }
> = {
  property: {
    eyebrow: "Property monitoring",
    heading: "Which chats feed the property pipeline?",
    blurb:
      "Groups from every number assigned above, in one list. Anything selected here is watched for property listings — messages that read as a requirement are never stored as properties, wherever they arrive.",
    groupsHint: "Groups watched for property listings",
    personalHint: "Applies across every number assigned to Property/Requirement.",
    emptyTitle: "No number assigned yet",
    emptyBody:
      "Add a connected number to the Numbers for Property/Requirement block above to pick which groups feed the property pipeline.",
    savedTitle: "Property monitoring updated",
    stopWarning: "Nothing selected — saving this will stop property capture on these numbers.",
    groupJidsOf: (connection) => connection.property_group_jids,
    personalNumbersOf: (connection) => connection.property_personal_numbers,
    save: (connectionId, groupJids, personalNumbers) =>
      whatsappApi.updatePropertySelection(connectionId, groupJids, personalNumbers),
  },
  requirement: {
    eyebrow: "Requirement monitoring",
    heading: "Which chats feed the requirement pipeline?",
    blurb:
      "The same groups and numbers as above, selected independently. A chat can be watched for both — its listings go to Properties and its requirements go to Broker Requirements. Picking a group here does not select it above, and picking it above does not select it here.",
    groupsHint: "Groups watched for broker requirements",
    personalHint: "Applies across every number assigned to Property/Requirement.",
    emptyTitle: "No number assigned yet",
    emptyBody:
      "Add a connected number to the Numbers for Property/Requirement block above to pick which groups feed the requirement pipeline.",
    savedTitle: "Requirement monitoring updated",
    stopWarning: "Nothing selected — saving this will stop requirement capture on these numbers.",
    groupJidsOf: (connection) => connection.requirement_group_jids,
    personalNumbersOf: (connection) => connection.requirement_personal_numbers,
    save: (connectionId, groupJids, personalNumbers) =>
      whatsappApi.updateRequirementSelection(connectionId, groupJids, personalNumbers),
  },
};

function MonitoringPanel({
  kind,
  propertyConns,
  onSaved,
}: {
  kind: MonitoringKind;
  propertyConns: WhatsAppConnection[];
  onSaved: () => void | Promise<void>;
}) {
  const config = MONITORING_CONFIG[kind];
  const toast = useToast();
  const [groupFilter, setGroupFilter] = useState("");
  const debouncedFilter = useDebounced(groupFilter, 140);
  const [draftSelection, setDraftSelection] = useState<Record<string, Set<string>>>({});
  const [personalNumbersInput, setPersonalNumbersInput] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const initializedConnIds = useRef<Set<string>>(new Set());
  const personalNumbersInitialized = useRef(false);

  // Seed each connection's draft selection from the server the first time
  // it's seen — afterwards it's the user's in-progress edit and must not be
  // silently overwritten by a background poll. Seeded strictly from THIS
  // panel's own selection (config.groupJidsOf), never from the other one:
  // that is what keeps the two pickers visually independent.
  useEffect(() => {
    let changed = false;
    const next = { ...draftSelection };
    for (const conn of propertyConns) {
      if (!initializedConnIds.current.has(conn.connection_id)) {
        next[conn.connection_id] = new Set(config.groupJidsOf(conn));
        initializedConnIds.current.add(conn.connection_id);
        changed = true;
      }
    }
    if (changed) setDraftSelection(next);

    if (!personalNumbersInitialized.current && propertyConns.length > 0) {
      const union = new Set<string>();
      propertyConns.forEach((c) => config.personalNumbersOf(c).forEach((n) => union.add(n)));
      if (union.size > 0) {
        setPersonalNumbersInput(Array.from(union).join(", "));
      }
      personalNumbersInitialized.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propertyConns]);

  const allGroups: TaggedGroup[] = useMemo(
    () =>
      propertyConns.flatMap((conn) =>
        conn.joined_groups.map((group) => ({ ...group, connectionId: conn.connection_id, connectionLabel: displayNumber(conn) })),
      ),
    [propertyConns],
  );

  const filteredGroups = useMemo(() => {
    const query = debouncedFilter.trim().toLowerCase();
    const matching = query ? allGroups.filter((g) => g.name.toLowerCase().includes(query)) : allGroups;
    return [...matching].sort((a, b) => {
      const aOn = draftSelection[a.connectionId]?.has(a.jid) ? 0 : 1;
      const bOn = draftSelection[b.connectionId]?.has(b.jid) ? 0 : 1;
      return aOn - bOn;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allGroups, debouncedFilter]);

  const parsedNumbers = useMemo(
    () =>
      personalNumbersInput
        .split(/[,\n]/)
        .map((n) => n.trim())
        .filter(Boolean),
    [personalNumbersInput],
  );
  const invalidNumbers = useMemo(() => parsedNumbers.filter((n) => !VALID_NUMBER.test(n)), [parsedNumbers]);

  const selectedCount = useMemo(
    () => Object.values(draftSelection).reduce((sum, set) => sum + set.size, 0),
    [draftSelection],
  );

  const dirty = useMemo(() => {
    for (const conn of propertyConns) {
      const draft = draftSelection[conn.connection_id] ?? new Set<string>();
      if (!setsEqual(draft, new Set(config.groupJidsOf(conn)))) return true;
    }
    const serverNumbers = new Set<string>();
    propertyConns.forEach((c) => config.personalNumbersOf(c).forEach((n) => serverNumbers.add(n)));
    return !setsEqual(new Set(parsedNumbers), serverNumbers);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propertyConns, draftSelection, parsedNumbers]);

  useUnsavedGuard(dirty);

  function toggleGroup(group: TaggedGroup) {
    setDraftSelection((prev) => {
      const current = new Set(prev[group.connectionId] ?? []);
      if (current.has(group.jid)) current.delete(group.jid);
      else current.add(group.jid);
      return { ...prev, [group.connectionId]: current };
    });
  }

  function selectAllFiltered() {
    setDraftSelection((prev) => {
      const next = { ...prev };
      for (const group of filteredGroups) {
        const current = new Set(next[group.connectionId] ?? []);
        current.add(group.jid);
        next[group.connectionId] = current;
      }
      return next;
    });
  }

  function clearAllFiltered() {
    setDraftSelection((prev) => {
      const next = { ...prev };
      for (const group of filteredGroups) {
        const current = new Set(next[group.connectionId] ?? []);
        current.delete(group.jid);
        next[group.connectionId] = current;
      }
      return next;
    });
  }

  function revert() {
    const seeded: Record<string, Set<string>> = {};
    propertyConns.forEach((c) => (seeded[c.connection_id] = new Set(config.groupJidsOf(c))));
    setDraftSelection(seeded);
    const union = new Set<string>();
    propertyConns.forEach((c) => config.personalNumbersOf(c).forEach((n) => union.add(n)));
    setPersonalNumbersInput(Array.from(union).join(", "));
    toast.push({ tone: "info", title: "Changes discarded", message: "Back to what the backend currently monitors." });
  }

  async function handleSave() {
    setSubmitting(true);
    try {
      await Promise.all(
        propertyConns.map((conn) =>
          config.save(conn.connection_id, Array.from(draftSelection[conn.connection_id] ?? []), parsedNumbers),
        ),
      );
      toast.push({
        tone: "ok",
        title: config.savedTitle,
        message: `Watching ${selectedCount} group(s) across ${propertyConns.length} number(s).`,
      });
      await onSaved();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not save selection", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  const allFilteredSelected =
    filteredGroups.length > 0 && filteredGroups.every((g) => draftSelection[g.connectionId]?.has(g.jid));
  const showConnectionTag = propertyConns.length > 1;

  if (propertyConns.length === 0) {
    return (
      <Panel className="stack stack-3" delay={140}>
        <div className="section-head__eyebrow" style={{ marginBottom: 0 }}>
          {config.eyebrow}
        </div>
        <EmptyState icon={<IconBuilding size={32} />} title={config.emptyTitle} body={config.emptyBody} />
      </Panel>
    );
  }

  return (
    <Panel className="stack stack-5" delay={140}>
      <div className="row-between">
        <div>
          <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
            {config.eyebrow}
          </div>
          <h2>{config.heading}</h2>
          <p className="section-head__sub" style={{ marginTop: 6 }}>
            {config.blurb}
          </p>
        </div>
        {dirty && (
          <Badge tone="warn" live title="You have changes that have not been sent to the backend yet">
            Unsaved changes
          </Badge>
        )}
      </div>

      <div className="two-col">
        {/* groups */}
        <div className="stack stack-3">
          <div className="row-between">
            <h3>
              Groups <span className="faint small">({filteredGroups.length} of {allGroups.length})</span>
            </h3>
            <span className="badge badge--info">{selectedCount} selected</span>
          </div>

          <p className="faint small" style={{ marginTop: -2 }}>
            {config.groupsHint}
          </p>

          <SearchInput
            value={groupFilter}
            onChange={setGroupFilter}
            placeholder="Filter groups by name…"
            ariaLabel={`Filter ${kind} groups by name`}
          />

          <div className="row-flex">
            <Button size="sm" onClick={selectAllFiltered} disabled={filteredGroups.length === 0 || allFilteredSelected}>
              Select {groupFilter ? "filtered" : "all"}
            </Button>
            <Button size="sm" variant="ghost" onClick={clearAllFiltered} disabled={selectedCount === 0}>
              Clear {groupFilter ? "filtered" : "all"}
            </Button>
          </div>

          {allGroups.length === 0 ? (
            <EmptyState icon={<IconUsers size={34} />} title="No groups yet" body="This number's groups are still loading, or it isn't in any." />
          ) : filteredGroups.length === 0 ? (
            <EmptyState icon={<IconUsers size={34} />} title="Nothing matches that filter" body="Try a shorter search term — filtering only looks at the group name." />
          ) : (
            <div className="scroll-list">
              {filteredGroups.map((group) => (
                <Check
                  key={`${group.connectionId}:${group.jid}`}
                  checked={draftSelection[group.connectionId]?.has(group.jid) ?? false}
                  onChange={() => toggleGroup(group)}
                >
                  <span className="cell-truncate" style={{ maxWidth: "100%" }} title={group.name}>
                    <Highlight text={group.name} query={debouncedFilter} />
                  </span>
                  <span className="faint small">
                    {group.member_count} members{showConnectionTag ? ` · ${group.connectionLabel}` : ""}
                  </span>
                </Check>
              ))}
            </div>
          )}
        </div>

        {/* personal numbers */}
        <div className="stack stack-3">
          <h3>Personal chats</h3>
          <p className="faint small">
            Phone numbers with country code, digits only, separated by commas or new lines — e.g. <code>919876543210</code>.{" "}
            {config.personalHint}
          </p>
          <textarea
            className="textarea"
            value={personalNumbersInput}
            onChange={(e) => setPersonalNumbersInput(e.target.value)}
            rows={4}
            aria-label={`Personal phone numbers to monitor for ${kind}`}
            placeholder="919876543210, 919812345678"
          />

          {parsedNumbers.length > 0 && (
            <div className="row-flex" style={{ gap: 7 }}>
              {parsedNumbers.map((number, index) => (
                <span
                  key={`${number}-${index}`}
                  className={`badge ${VALID_NUMBER.test(number) ? "badge--info" : "badge--bad"}`}
                  title={VALID_NUMBER.test(number) ? undefined : "Digits only, 8–15 characters, including country code"}
                >
                  <IconPhone size={11} />
                  {number}
                </span>
              ))}
            </div>
          )}

          {invalidNumbers.length > 0 && (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              {invalidNumbers.length} entr{invalidNumbers.length === 1 ? "y does" : "ies do"} not look like a phone number. Use
              digits only, including the country code and no <code>+</code> or spaces.
            </Note>
          )}

          {/* Stated once, on the Requirement side, because this is the one
              consequence of these two panels that is not obvious from the
              checkboxes themselves. */}
          {kind === "requirement" && (
            <Note tone="info" icon={<IconInfo size={16} />}>
              A personal number listed here (or under Property monitoring) never receives the automatic client-inquiry
              reply, even if the same WhatsApp number also has the Client Inquiry role — it is treated as a broker
              feeding the pipelines, not as a client to answer.
            </Note>
          )}
        </div>
      </div>

      <div className="row-flex">
        <Button variant="primary" onClick={handleSave} busy={submitting} disabled={!dirty}>
          {submitting ? "Saving…" : dirty ? "Save monitoring selection" : "Everything is saved"}
        </Button>
        {dirty && (
          <Button variant="ghost" onClick={revert} disabled={submitting}>
            Discard changes
          </Button>
        )}
        {selectedCount === 0 && parsedNumbers.length === 0 && (
          <span className="small" style={{ color: "var(--warn)" }}>
            {config.stopWarning}
          </span>
        )}
      </div>
    </Panel>
  );
}
