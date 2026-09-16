import { useEffect } from "react";
import { createPortal } from "react-dom";
import type { InquiryClientRecord } from "../api/types";
import { formatCompactInr, formatIst, relativeTime } from "../lib/formatters";
import ClientAvatar from "./ClientAvatar";
import { Button, Copyable } from "./ui/Primitives";
import { IconEdit, IconPin, IconTag, IconX } from "./ui/Icons";

/**
 * One client's details — the dialog a row on the Inquiries page opens.
 * Lives here rather than inside InquiryClientsPage.tsx so the Visits page
 * opens exactly this same dialog for a visit's client, instead of a second
 * copy that could drift from it.
 */
export default function ClientDetailDialog({
  client,
  onClose,
  onEdit,
}: {
  client: InquiryClientRecord;
  onClose: () => void;
  /** Shows the footer's Edit button. Left out only where the client can't
   *  be edited (a visit whose client is no longer in the Inquiries list). */
  onEdit?: (client: InquiryClientRecord) => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Client details">
        <div className="detail-modal__head">
          <div className="row-flex" style={{ gap: 14, minWidth: 0, flexWrap: "nowrap" }}>
            <ClientAvatar client={client} size={64} />
            <div style={{ minWidth: 0 }}>
              <div className="detail-modal__eyebrow">Client</div>
              <h2 className="detail-modal__title cell-truncate">{client.name ?? client.phone}</h2>
              <div className="detail-modal__sub cell-truncate">{client.phone}</div>
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>
        <div className="detail-modal__body">
          <ClientDetail client={client} />
        </div>
        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {onEdit && (
            <span style={{ marginLeft: "auto" }}>
              <Button variant="ghost" icon={<IconEdit size={14} />} onClick={() => onEdit(client)}>
                Edit
              </Button>
            </span>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ClientDetail({ client }: { client: InquiryClientRecord }) {
  return (
    <div className="detail">
      <div className="detail__grid">
        <div className="detail__block">
          <div className="detail__k">Contact</div>
          <div className="detail__v">{client.name ?? "—"}</div>
          <div className="detail__v" style={{ marginTop: 4 }}>
            <Copyable text={client.phone} />
          </div>
          {client.email && (
            <div className="faint small" style={{ marginTop: 4 }}>
              {client.email}
            </div>
          )}
        </div>

        <div className="detail__block">
          <div className="detail__k">Timeline</div>
          <div className="detail__v">
            First contacted{" "}
            {client.created_at
              ? relativeTime(new Date(client.created_at))
              : "—"}
          </div>
          <div className="faint small" style={{ marginTop: 4 }}>
            Last updated{" "}
            {client.updated_at
              ? relativeTime(new Date(client.updated_at))
              : "—"}
          </div>
          {/* Read-only here; the table's own cell is where it is changed. */}
          <div className="faint small" style={{ marginTop: 4 }}>
            Last follow-up{" "}
            {client.last_follow_up_dates ? formatIst(client.last_follow_up_dates) : "— not yet"}
          </div>
        </div>

        {client.current_address && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Current address
            </div>
            <div className="detail__v">{client.current_address}</div>
          </div>
        )}

        {client.about_loan && (
          <div className="detail__block">
            <div className="detail__k">Loan</div>
            <div className="detail__v">{client.about_loan}</div>
          </div>
        )}

        {client.property_sizes && Object.keys(client.property_sizes).length > 0 && (
          <div className="detail__block">
            <div className="detail__k">
              <IconTag size={11} /> Preferred size
            </div>
            {Object.entries(client.property_sizes).map(([type, size]) => (
              <div className="detail__v" key={type}>
                {type}: {size}
              </div>
            ))}
          </div>
        )}

        {(client.budget_min_inr !== null || client.budget_max_inr !== null) && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Budget
            </div>
            <div className="detail__v">
              {formatBudgetRange(client.budget_min_inr, client.budget_max_inr)}
            </div>
          </div>
        )}

        {client.preferred_areas && (
          <div className="detail__block">
            <div className="detail__k">
              <IconTag size={11} /> Preferred areas
            </div>
            <div className="detail__v">{client.preferred_areas}</div>
          </div>
        )}
      </div>

      {client.additional_requirements && (
        <div className="detail__block">
          <div className="detail__k">Additional requirements</div>
          <div className="detail__v">{client.additional_requirements}</div>
        </div>
      )}
    </div>
  );
}

export function formatBudgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "—";
  if (min !== null && max !== null)
    return `${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `${formatCompactInr(min)}+`;
  return `Up to ${formatCompactInr(max as number)}`;
}
