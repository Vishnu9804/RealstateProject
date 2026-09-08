import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { HandoffTemplates, InquiryClientRecord } from "../api/types";
import { agentApi } from "../api/agentApi";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { friendlyError } from "../lib/apiError";
import { buildAgentTokens, buildClientMessage, propertyLabel, renderTemplate, type AgentAssignment } from "../lib/handoffTemplate";
import { useToast } from "./ui/Toast";
import { Avatar, Button, SkeletonRows } from "./ui/Primitives";
import { IconSend, IconX, IconZap } from "./ui/Icons";

/**
 * "Step 5 — Hand over": generates the pre-filled WhatsApp messages — one
 * per agent involved (each naming only the property/properties assigned
 * to them, see lib/handoffTemplate.ts's buildAgentTokens), plus one
 * message to the client covering everyone — rendered from the templates a
 * real-estate client can customize on the Settings page. "Send" actually
 * delivers all of them over the already-connected inquiry WhatsApp account
 * (no wa.me link to click through) and records the hand-off.
 */
export default function HandoffDialog({
  client,
  assignments,
  onClose,
  onPickDifferentAgent,
  onSent,
}: {
  client: InquiryClientRecord;
  /** One entry per distinct agent involved in this hand-off round —
   *  almost always length 1, but can be more when different properties
   *  (matched and/or manually-added) were assigned to different agents. */
  assignments: AgentAssignment[];
  onClose: () => void;
  onPickDifferentAgent: () => void;
  onSent: (client: InquiryClientRecord) => void;
}) {
  const toast = useToast();
  const [sending, setSending] = useState(false);
  const [templates, setTemplates] = useState<HandoffTemplates | null>(null);

  useEffect(() => {
    let cancelled = false;
    agentApi
      .getHandoffTemplates()
      .then((data) => !cancelled && setTemplates(data))
      .catch(() => !cancelled && setTemplates({ agent_template: "", client_template: "" }));
    return () => {
      cancelled = true;
    };
  }, []);

  const agentMessages = useMemo(
    () =>
      templates
        ? assignments.map((assignment) => ({
            assignment,
            message: renderTemplate(templates.agent_template, buildAgentTokens(client, assignment.properties)),
          }))
        : [],
    [templates, assignments, client],
  );
  const clientMessage = useMemo(
    () => (templates ? buildClientMessage(client, assignments, templates.client_template) : ""),
    [templates, client, assignments],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !sending) {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, sending]);

  async function handleSend() {
    setSending(true);
    try {
      const result = await inquiryClientApi.sendHandoff(client.phone, {
        agent_messages: agentMessages.map(({ assignment, message }) => ({
          agent_id: assignment.agent.agent_id,
          agent_phone: assignment.agent.phone,
          message,
          properties: assignment.properties.map((p) => ({ record_id: p.record_id, label: propertyLabel(p) })),
        })),
        client_message: clientMessage,
      });
      // The client's assigned_agent_id is just a lightweight "has this
      // client been handed off to anyone" signal now (see
      // InquiryClientsPage.tsx's Status pill) — the real per-property
      // assignment record (Backend/Model/AgentManagementModel/
      // assignment_record.py) is what the Agents page and each property's
      // "Assigned to" label actually read. When this round involved more
      // than one agent, the first is recorded here; that's fine, since
      // nothing displays this field as "the" agent anymore.
      let updated = result.client;
      try {
        updated = await inquiryClientApi.assignAgent(client.phone, assignments[0].agent.agent_id);
      } catch {
        // Non-fatal — the hand-off itself already went out; the Agent
        // column just won't reflect it until the next successful save.
      }

      const failedAgents = result.agent_results.filter((r) => !r.sent);
      if (failedAgents.length === 0 && result.client_sent) {
        toast.push({ tone: "ok", title: "Hand-off sent", message: `${assignments.length} agent${assignments.length === 1 ? "" : "s"} and ${client.name || "the client"} all received their WhatsApp message.` });
      } else {
        const names = assignments.filter((a) => failedAgents.some((f) => f.agent_phone === a.agent.phone)).map((a) => a.agent.name);
        if (!result.client_sent) names.push(client.name || "the client");
        toast.push({
          tone: "warn",
          title: "Sent partially",
          message: `Could not reach ${names.join(", ")} — check the inquiry WhatsApp connection on the Connection page.`,
        });
      }
      onSent(updated);
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not send the hand-off", message: friendlyError(err) });
    } finally {
      setSending(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !sending && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="WhatsApp hand-off" style={{ maxWidth: 980 }}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Step 5 — Hand over</div>
            <h2 className="detail-modal__title">{assignments.length === 1 ? "Two WhatsApp messages, one click" : `${assignments.length + 1} WhatsApp messages, one click`}</h2>
            <div className="detail-modal__sub">
              {assignments.map((a) => a.agent.name).join(" and ")} get{assignments.length === 1 ? "s" : ""} the brief and the shortlist. {client.name || "The client"} gets a name and a number.
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={sending} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          {!templates ? (
            <SkeletonRows rows={5} />
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 16 }}>
              {agentMessages.map(({ assignment, message }) => (
                <MessagePreview key={assignment.agent.agent_id} label={`To ${assignment.agent.name}`} name={assignment.agent.name} message={message} />
              ))}
              <MessagePreview label="To your client" name={client.name || client.phone} message={clientMessage} />
            </div>
          )}

          <div className="note note--info" style={{ marginTop: 16 }}>
            <span className="note__icon">
              <IconZap size={16} />
            </span>
            <div>Every agent gets only their own property(ies) — nobody has to ring you to ask "which flats do I show". It is all in their chat.</div>
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onPickDifferentAgent} disabled={sending}>
            Change assignment
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" icon={<IconSend size={14} />} onClick={handleSend} busy={sending} disabled={!templates}>
              Send all on WhatsApp
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MessagePreview({ label, name, message }: { label: string; name: string; message: string }) {
  return (
    <div className="panel panel--pad stack stack-3" style={{ display: "flex", flexDirection: "column" }}>
      <div className="section-head__eyebrow" style={{ marginBottom: 0 }}>
        {label}
      </div>
      <div className="row-flex" style={{ gap: 10 }}>
        <Avatar name={name} size={32} />
        <div>
          <div className="cell-strong">{name}</div>
          <div className="faint small">online</div>
        </div>
      </div>
      <div
        className="detail__msg"
        style={{ whiteSpace: "pre-wrap", maxHeight: 320, overflowY: "auto", background: "color-mix(in oklab, var(--ok) 10%, var(--plane-1))" }}
      >
        {message}
      </div>
    </div>
  );
}
