import { useEffect } from "react";
import { createPortal } from "react-dom";
import type { InquiryClientRecord } from "../api/types";
import { formatCompactInr, formatIst, relativeTime } from "../lib/formatters";
import { formatPhone } from "../lib/phone";
import ClientAvatar from "./ClientAvatar";
import { Badge, Button, Copyable } from "./ui/Primitives";
import {
  IconBuilding,
  IconClock,
  IconEdit,
  IconPin,
  IconRuler,
  IconTag,
  IconUserCheck,
  IconX,
} from "./ui/Icons";

/**
 * One client's details — the dialog a row on the Inquiries page opens.
 * Lives here rather than inside InquiryClientsPage.tsx so the Visits page
 * opens exactly this same dialog for a visit's client, instead of a second
 * copy that could drift from it.
 *
 * Laid out the same way a property's read-only dialog is (see
 * PropertyReadOnlyDialog): what the client is actually asking for — what
 * they want to do, what kind of place, how big, how much — sits in the
 * header as chips, so the answer to "who is this and what do they want" is
 * legible before anything is read. Everything else the clients table holds
 * follows underneath in blocks, and the two free-text fields that can run
 * to any length (Additional requirements and Notes) are last, full width
 * and scrolled inside their own box — a paragraph of imported notes used
 * to stretch one grid cell into a column the height of the dialog and push
 * every other field off screen.
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
          <div className="row-flex" style={{ gap: 14, minWidth: 0, flexWrap: "nowrap", flex: 1 }}>
            <ClientAvatar client={client} size={64} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="detail-modal__eyebrow">
                Client{client.purpose ? ` · ${client.purpose}` : ""}
              </div>
              <h2 className="detail-modal__title cell-truncate">{client.name ?? formatPhone(client.phone)}</h2>
              <div className="detail-modal__sub cell-truncate">{formatPhone(client.phone)}</div>
              {/* The requirement in one glance — the same chip row the
                  property dialog carries, so a client and a listing read
                  the same way side by side. */}
              <div className="detail-modal__badges">
                <Badge tone={STATUS_TONE[client.status] ?? "info"}>{client.status || "new"}</Badge>
                {sourceLabel(client.source) && <Badge tone="info">{sourceLabel(client.source)}</Badge>}
                {(client.bhk || client.property_type) && (
                  <span className="fact">
                    <IconBuilding size={12} />
                    {[client.bhk, client.property_type].filter(Boolean).join(" ")}
                  </span>
                )}
                {sizeChips(client).map((size) => (
                  <span className="fact" key={size}>
                    <IconRuler size={12} />
                    {size}
                  </span>
                ))}
                {(client.budget_min_inr !== null || client.budget_max_inr !== null) && (
                  <span className="fact">
                    <IconTag size={12} />
                    {formatBudgetRange(client.budget_min_inr, client.budget_max_inr)}
                  </span>
                )}
                {client.preferred_areas && (
                  <span className="fact" title={client.preferred_areas}>
                    <IconPin size={12} />
                    <span className="cell-truncate" style={{ maxWidth: 220 }}>
                      {client.preferred_areas}
                    </span>
                  </span>
                )}
                {client.furnishing && <span className="fact">{client.furnishing}</span>}
              </div>
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

/** Friendly wording for ClientRecord.source — how this client first reached
 *  us. Falls back to the stored value so a source added later still shows
 *  something rather than disappearing. */
function sourceLabel(source: string | null): string {
  if (!source || source === "unknown") return "";
  return (
    {
      manual: "Added by hand",
      whatsapp: "via WhatsApp",
      instagram: "via Instagram",
      website_form: "Website form",
      website_enquiry: "Website enquiry",
      excel: "Excel import",
    }[source] ?? source
  );
}

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "accent"> = {
  new: "accent",
  complete: "ok",
  completed: "ok",
  pending: "warn",
  incomplete: "warn",
};

/** The sizes the client gave, ready to print as chips: just the size when
 *  they asked for one kind of property ("110 var"), qualified by type when
 *  they asked for several ("Row House 110 var"), since the number alone
 *  would then say nothing about which requirement it belongs to.
 *
 *  Printed exactly as stored — the unit is part of the stored value now
 *  (see lib/propertyTypeOptions.ts), so nothing here has to guess it. */
function sizeChips(client: InquiryClientRecord): string[] {
  const sizes = client.property_sizes;
  if (!sizes) return [];
  const entries = Object.entries(sizes).filter(([, size]) => size?.trim());
  if (entries.length === 0) return [];
  if (entries.length === 1) return [entries[0][1]];
  return entries.map(([type, size]) => `${type} ${size}`);
}

/** An instant as both the exact IST moment and how long ago that was —
 *  the first answers "when exactly", the second answers "is this recent",
 *  and reading one off the other is work nobody should have to do. */
function When({ iso }: { iso: string | null }) {
  if (!iso) return <>—</>;
  return (
    <>
      {formatIst(iso)} <span className="faint">({relativeTime(new Date(iso))})</span>
    </>
  );
}

function ClientDetail({ client }: { client: InquiryClientRecord }) {
  const hasBudget = client.budget_min_inr !== null || client.budget_max_inr !== null;
  return (
    <div className="detail">
      <div className="detail__grid">
        <div className="detail__block">
          <div className="detail__k">Contact</div>
          <div className="detail__v">{client.name ?? "—"}</div>
          <div className="detail__v" style={{ marginTop: 4 }}>
            {/* Shown spaced, copied as stored — what lands in the
                clipboard is pasted into WhatsApp or a dialler, where the
                space is at best noise. The same split ui/ContactPhones
                makes for a listing's numbers. */}
            <Copyable text={client.phone}>{formatPhone(client.phone)}</Copyable>
          </div>
          {client.email && (
            <div className="faint small" style={{ marginTop: 4 }}>
              {client.email}
            </div>
          )}
          {/* Extra, unverified numbers — kept apart from the WhatsApp number
              above, which is this client's actual identity here. */}
          {client.additional_phones && client.additional_phones.length > 0 && (
            <div className="faint small" style={{ marginTop: 4 }}>
              {client.additional_phones.map((phone, index) => (
                <div key={index}>
                  <Copyable text={phone}>{formatPhone(phone)}</Copyable>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="detail__block">
          <div className="detail__k">Requirement</div>
          <div className="detail__v">
            {[client.purpose, client.bhk, client.property_type].filter(Boolean).join(" · ") || "—"}
          </div>
          {sizeChips(client).length > 0 && (
            <div className="faint small" style={{ marginTop: 4 }}>
              {sizeChips(client).join(" · ")}
            </div>
          )}
          {client.furnishing && (
            <div className="faint small" style={{ marginTop: 4 }}>
              {client.furnishing}
            </div>
          )}
        </div>

        <div className="detail__block">
          <div className="detail__k">
            <IconTag size={11} /> Budget
          </div>
          <div className="detail__v">
            {hasBudget ? formatBudgetRange(client.budget_min_inr, client.budget_max_inr) : "—"}
          </div>
        </div>

        <div className="detail__block">
          <div className="detail__k">
            <IconPin size={11} /> Preferred areas
          </div>
          <div className="detail__v">{client.preferred_areas || "—"}</div>
        </div>

        <div className="detail__block">
          <div className="detail__k">
            <IconPin size={11} /> Current address
          </div>
          <div className="detail__v">{client.current_address || "—"}</div>
        </div>

        <div className="detail__block">
          <div className="detail__k">Loan</div>
          <div className="detail__v">{client.about_loan || "—"}</div>
        </div>

        {/* Exact instants, not "3 days ago" alone: the follow-up stamp is
            written to the second the automatic WhatsApp actually goes out
            (Backend/Service/AgentManagementService/visit_reminder_service.py),
            and rounding that away on screen would throw away the one thing
            it is for. Always IST — see lib/formatters.ts. */}
        <div className="detail__block">
          <div className="detail__k">
            <IconClock size={11} /> Timeline
          </div>
          <div className="detail__v">
            First contacted <When iso={client.created_at} />
          </div>
          <div className="faint small" style={{ marginTop: 4 }}>
            Last updated <When iso={client.updated_at} />
          </div>
        </div>

        {/* Read-only here; the table's own cell is where it is changed. */}
        <div className="detail__block">
          <div className="detail__k">
            <IconClock size={11} /> Last follow-up
          </div>
          <div className="detail__v">
            {client.last_follow_up_dates ? <When iso={client.last_follow_up_dates} /> : "— not yet"}
          </div>
        </div>

        <div className="detail__block">
          <div className="detail__k">
            <IconUserCheck size={11} /> Site visit hand-off
          </div>
          <div className="detail__v">
            {client.handoff_sent_at ? (
              <>
                Sent <When iso={client.handoff_sent_at} />
              </>
            ) : (
              "Not sent yet"
            )}
          </div>
          {client.assigned_agent_id && (
            <div className="faint small" style={{ marginTop: 4 }}>
              Agent ref {client.assigned_agent_id}
            </div>
          )}
        </div>

        {/* Always shown, unlike an optional block: "nothing was written" is
            itself worth seeing when someone is deciding whether this client
            has been spoken to, so it reads "-" rather than disappearing.
            Written in the same popover as the date above. */}
        <div className="detail__block">
          <div className="detail__k">Follow-up report</div>
          <div className="detail__note">{client.follow_up_report?.trim() || "-"}</div>
        </div>

        {/* The two fields that can be any length at all, last and across
            the full width, each scrolling inside its own box so neither can
            set the height of the dialog. */}
        {client.additional_requirements && (
          <div className="detail__block detail__block--full">
            <div className="detail__k">Additional requirements</div>
            <div className="detail__note">{client.additional_requirements}</div>
          </div>
        )}

        {client.notes && (
          <div className="detail__block detail__block--full">
            <div className="detail__k">Notes</div>
            <div className="detail__note detail__note--tall">{client.notes}</div>
          </div>
        )}
      </div>
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
