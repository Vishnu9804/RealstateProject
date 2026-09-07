import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { agentApi } from "../api/agentApi";
import type { AssignedClientSummary, VisitRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { useToast } from "./ui/Toast";
import { IconX } from "./ui/Icons";

/**
 * The Agents page's "Mark visit complete" action — moves one of an agent's
 * active clients into their completed-visit history (see
 * Backend/Service/AgentManagementService/agent_store.py's complete_visit).
 * Notes are optional free text — a price the client mentioned, how the
 * visit went, anything worth remembering later.
 */
export default function CompleteVisitDialog({
  agentId,
  agentName,
  client,
  onClose,
  onCompleted,
}: {
  agentId: string;
  agentName: string;
  client: AssignedClientSummary;
  onClose: () => void;
  onCompleted: (visit: VisitRecord) => void;
}) {
  const toast = useToast();
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, saving]);

  async function handleSave() {
    setSaving(true);
    try {
      const visit = await agentApi.completeVisit(agentId, {
        client_phone: client.phone,
        property_record_id: client.property_record_id,
        notes: notes.trim() || null,
      });
      toast.push({ tone: "ok", title: "Visit marked complete", message: `${client.name ?? client.phone} — ${client.property_label}` });
      onCompleted(visit);
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not mark this visit complete", message: friendlyError(err) });
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="modal anim-rise" role="dialog" aria-modal="true" aria-label="Mark visit complete">
        <div className="modal__head">
          <span className="modal__title">Mark visit complete</span>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={13} />
          </button>
        </div>

        <div className="modal__body stack stack-3">
          <p className="faint small">
            {client.property_label} for {client.name ?? client.phone} moves out of {agentName}'s active visits and into their completed visits.
          </p>
          <div className="field">
            <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
              Notes (optional)
            </label>
            <textarea
              className="textarea"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Client offered 2.6cr, wants a second visit next week"
              autoFocus
            />
          </div>
        </div>

        <div className="modal__foot">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn btn--sm btn--primary" onClick={handleSave} disabled={saving} aria-busy={saving || undefined}>
            {saving && <span className="spinner" />}
            Mark complete
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
