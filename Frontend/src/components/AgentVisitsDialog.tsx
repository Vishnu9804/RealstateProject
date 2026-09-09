import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { agentApi } from "../api/agentApi";
import { propertyApi } from "../api/propertyApi";
import type { AgentSummary, AssignedClientSummary, VisitRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { formatCompactInr, relativeTime } from "../lib/formatters";
import { setCachedPropertyList } from "../lib/propertyListCache";
import PropertyReadOnlyDialog from "./PropertyReadOnlyDialog";
import { useToast } from "./ui/Toast";
import { Avatar, Badge, Button, EmptyState, Segmented } from "./ui/Primitives";
import { IconCheck, IconClock, IconInbox, IconMessage, IconRefresh, IconX } from "./ui/Icons";

type Tab = "active" | "completed";

function budgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "";
  if (min !== null && max !== null) return `${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `${formatCompactInr(min)}+`;
  return `Up to ${formatCompactInr(max as number)}`;
}

/** Oldest first — a null timestamp only ever comes from a row written
 *  before this field was tracked, so it sorts as the oldest thing there
 *  is rather than jumping to either end unpredictably. */
function byOldestFirst(iso: string | null): number {
  return iso ? new Date(iso).getTime() : 0;
}

/**
 * The Agents page's per-agent dialog — clicking an agent card opens this
 * instead of expanding a property list inline on the card (which is what
 * made the card itself feel cluttered/awkward). Two tabs: Active (this
 * agent's current site visits — Mark as complete) and Completed (their
 * history — Mark as still active undoes one), both oldest-first so the
 * visit that's been waiting longest is always the one on top.
 */
export default function AgentVisitsDialog({
  agent,
  onClose,
  onCompleteVisit,
  onReopened,
}: {
  agent: AgentSummary;
  onClose: () => void;
  /** Bubbles up to the Agents page's existing CompleteVisitDialog flow —
   *  that dialog takes optional notes, so it stays a separate step rather
   *  than a bare button action the way reopening is. */
  onCompleteVisit: (client: AssignedClientSummary) => void;
  /** Fired once agentApi.reopenVisit succeeds, so the Agents page can move
   *  this visit out of completed_visits and into active_clients locally. */
  onReopened: (agentId: string, visit: VisitRecord, restored: AssignedClientSummary) => void;
}) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("active");
  const [reopeningId, setReopeningId] = useState<string | null>(null);
  const [viewingPropertyId, setViewingPropertyId] = useState<string | null>(null);

  const nestedOpen = viewingPropertyId !== null;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !nestedOpen) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, nestedOpen]);

  // Warms the shared property-list cache on open, the same way
  // ClientMatchesDialog.tsx does — clicking a row here opens
  // PropertyReadOnlyDialog, which checks that same cache before falling
  // back to a ~1-2s single-property fetch. Without this, a session that
  // never visited the Properties/Landing Page page first paid that fetch
  // on every single row click; a background refresh here means it almost
  // never has to. Fire-and-forget: a click that lands before this
  // resolves just falls back to PropertyReadOnlyDialog's own fetch,
  // exactly as before this existed.
  useEffect(() => {
    let cancelled = false;
    propertyApi
      .getProperties(500)
      .then((data) => {
        if (!cancelled) setCachedPropertyList(data, null);
      })
      .catch(() => {
        /* PropertyReadOnlyDialog's own per-property fetch is the fallback */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const activeSorted = useMemo(
    () => [...agent.active_clients].sort((a, b) => byOldestFirst(a.assigned_at) - byOldestFirst(b.assigned_at)),
    [agent.active_clients],
  );
  const completedSorted = useMemo(
    () => [...agent.completed_visits].sort((a, b) => byOldestFirst(a.completed_at) - byOldestFirst(b.completed_at)),
    [agent.completed_visits],
  );

  async function handleReopen(visit: VisitRecord) {
    setReopeningId(visit.visit_id);
    try {
      const restored = await agentApi.reopenVisit(agent.agent_id, visit.visit_id);
      onReopened(agent.agent_id, visit, restored);
      toast.push({
        tone: "ok",
        title: "Marked as still active",
        message: visit.property_label ?? undefined,
      });
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not reopen this visit", message: friendlyError(err) });
    } finally {
      setReopeningId(null);
    }
  }

  return createPortal(
    <>
      <div
        className="modal-scrim"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && !nestedOpen) onClose();
        }}
      >
        <div
          className="detail-modal anim-rise"
          role="dialog"
          aria-modal="true"
          aria-label={`${agent.name}'s site visits`}
          style={{ maxWidth: 720 }}
        >
          <div className="detail-modal__head">
            <div className="row-flex" style={{ gap: 12, minWidth: 0 }}>
              <Avatar name={agent.name} size={44} />
              <div style={{ minWidth: 0 }}>
                <div className="detail-modal__eyebrow">Site visits</div>
                <h2 className="detail-modal__title cell-truncate">{agent.name}</h2>
                <div className="detail-modal__sub cell-truncate">{agent.phone}</div>
                <div className="detail-modal__badges">
                  <Badge tone={agent.active_clients.length > 0 ? "ok" : "info"}>
                    {agent.active_clients.length} active
                  </Badge>
                  <Badge tone="accent">{agent.visits_this_month} this month</Badge>
                </div>
              </div>
            </div>
            <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
              <IconX size={15} />
            </button>
          </div>

          <div className="matches-dialog__tabs">
            <Segmented<Tab>
              ariaLabel="Active or completed visits"
              value={tab}
              onChange={setTab}
              options={[
                { value: "active", label: `Active (${activeSorted.length})` },
                { value: "completed", label: `Completed (${completedSorted.length})` },
              ]}
            />
          </div>

          <div className="detail-modal__body">
            {tab === "active" && activeSorted.length === 0 && (
              <EmptyState icon={<IconInbox size={36} />} title="No active visits" body={`${agent.name} is free right now.`} />
            )}
            {tab === "active" &&
              activeSorted.map((client) => (
                <ActiveVisitRow
                  key={`${client.phone}-${client.property_record_id}`}
                  client={client}
                  onOpenProperty={() => setViewingPropertyId(client.property_record_id)}
                  onCompleteVisit={() => onCompleteVisit(client)}
                />
              ))}

            {tab === "completed" && completedSorted.length === 0 && (
              <EmptyState icon={<IconInbox size={36} />} title="No completed visits yet" body="Visits marked complete for this agent will show up here." />
            )}
            {tab === "completed" &&
              completedSorted.map((visit) => (
                <CompletedVisitRow
                  key={visit.visit_id}
                  visit={visit}
                  reopening={reopeningId === visit.visit_id}
                  onOpenProperty={() => visit.property_record_id && setViewingPropertyId(visit.property_record_id)}
                  onReopen={() => handleReopen(visit)}
                />
              ))}
          </div>

          <div className="detail-modal__foot">
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      </div>

      {viewingPropertyId && (
        <PropertyReadOnlyDialog recordId={viewingPropertyId} onClose={() => setViewingPropertyId(null)} />
      )}
    </>,
    document.body,
  );
}

/* ------------------------------------------------------------- rows */

function ActiveVisitRow({
  client,
  onOpenProperty,
  onCompleteVisit,
}: {
  client: AssignedClientSummary;
  onOpenProperty: () => void;
  onCompleteVisit: () => void;
}) {
  const budget = budgetRange(client.budget_min_inr, client.budget_max_inr);
  return (
    <div
      className="detail visit-row"
      style={{ padding: "12px 14px", cursor: "pointer" }}
      role="button"
      tabIndex={0}
      onClick={onOpenProperty}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenProperty();
        }
      }}
    >
      <div className="row-flex" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div className="stack stack-1" style={{ minWidth: 0 }}>
          <strong className="cell-truncate">{client.property_label}</strong>
          <div className="faint small row-flex" style={{ gap: 10, flexWrap: "wrap" }}>
            <span>{client.name || client.phone}</span>
            {budget && <span>{budget}</span>}
            {client.assigned_at && (
              <span className="row-flex" style={{ gap: 4 }}>
                <IconClock size={12} /> Assigned {relativeTime(new Date(client.assigned_at))}
              </span>
            )}
          </div>
        </div>
        <Button
          size="sm"
          icon={<IconCheck size={13} />}
          onClick={(event) => {
            event.stopPropagation();
            onCompleteVisit();
          }}
        >
          Mark as complete
        </Button>
      </div>
    </div>
  );
}

function CompletedVisitRow({
  visit,
  reopening,
  onOpenProperty,
  onReopen,
}: {
  visit: VisitRecord;
  reopening: boolean;
  onOpenProperty: () => void;
  onReopen: () => void;
}) {
  const budget = budgetRange(visit.budget_min_inr, visit.budget_max_inr);
  const clickable = visit.property_record_id !== null;
  const completedDate = visit.completed_at
    ? new Date(visit.completed_at).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" })
    : null;
  return (
    <div
      className="detail"
      style={{ padding: "12px 14px", cursor: clickable ? "pointer" : "default" }}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? onOpenProperty : undefined}
      onKeyDown={
        clickable
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpenProperty();
              }
            }
          : undefined
      }
    >
      <div className="row-flex" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div className="stack stack-1" style={{ minWidth: 0 }}>
          <strong className="cell-truncate">{visit.property_label ?? "Property"}</strong>
          <div className="faint small row-flex" style={{ gap: 10, flexWrap: "wrap" }}>
            <span>{visit.client_name || visit.client_phone}</span>
            {budget && <span>{budget}</span>}
          </div>
          {visit.notes && (
            <div className="visit-row__notes">
              <IconMessage size={12} />
              <span>{visit.notes}</span>
            </div>
          )}
          {completedDate && (
            <Badge tone="ok" title={completedDate}>
              <IconCheck size={11} /> Completed {completedDate}
            </Badge>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          icon={<IconRefresh size={13} />}
          busy={reopening}
          disabled={visit.property_record_id === null}
          onClick={(event) => {
            event.stopPropagation();
            onReopen();
          }}
        >
          Mark as still active
        </Button>
      </div>
    </div>
  );
}
