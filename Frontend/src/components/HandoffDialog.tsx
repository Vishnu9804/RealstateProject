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
 *
 * EVERY MESSAGE HERE IS EDITABLE BEFORE IT GOES OUT, and an edit applies to
 * this hand-off only — the Settings template is never rewritten by it. The
 * template is the default wording, and a default cannot cover "tell Ramesh
 * the client can only do evenings". Before this, changing one word meant
 * changing it for everyone, forever; now the operator adjusts the message
 * in front of them and the next hand-off still starts from the saved
 * template. `edits` below is what keeps those two facts apart: the rendered
 * template stays the baseline, and an entry exists only for a message a
 * human actually touched.
 */

/** The one message that isn't keyed by an agent id. A literal that can
 *  never collide with one, since agent ids are uuids. */
const CLIENT_KEY = "__client__";
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
  // Per-message overrides, keyed by agent_id (and CLIENT_KEY for the
  // client's own message). Absent = "not touched", which is why this is a
  // sparse map rather than a copy of every rendered message: the template
  // render stays the source of truth for anything the operator has not
  // deliberately changed, and Reset is simply deleting the key.
  const [edits, setEdits] = useState<Record<string, string>>({});

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
        ? assignments.map((assignment) => {
            const rendered = renderTemplate(templates.agent_template, buildAgentTokens(client, assignment.properties));
            // `message` is what will actually be sent: the operator's edit
            // when there is one, the freshly rendered template otherwise.
            return { assignment, rendered, message: edits[assignment.agent.agent_id] ?? rendered };
          })
        : [],
    [templates, assignments, client, edits],
  );
  const renderedClientMessage = useMemo(
    () => (templates ? buildClientMessage(client, assignments, templates.client_template) : ""),
    [templates, client, assignments],
  );
  const clientMessage = edits[CLIENT_KEY] ?? renderedClientMessage;

  function setEdit(key: string, value: string) {
    setEdits((previous) => ({ ...previous, [key]: value }));
  }

  function resetEdit(key: string) {
    setEdits((previous) => {
      if (!(key in previous)) return previous;
      const next = { ...previous };
      delete next[key];
      return next;
    });
  }

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
        // `message` is already the edited text wherever the operator
        // changed it — see agentMessages above. Nothing is written back to
        // the stored template.
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
      //
      // `result.client` is null when this hand-off was for a landing-page
      // lead, who has no ClientRecord to stamp at all — the messages still
      // went out and the visits were still recorded, so falling back to the
      // record we were handed keeps onSent's contract intact.
      let updated = result.client ?? client;
      try {
        updated = await inquiryClientApi.assignAgent(client.phone, assignments[0].agent.agent_id);
      } catch {
        // Non-fatal — the hand-off itself already went out; the Agent
        // column just won't reflect it until the next successful save (and
        // for a lead there is no record to save it on in the first place).
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
              {agentMessages.map(({ assignment, rendered, message }) => (
                <MessagePreview
                  key={assignment.agent.agent_id}
                  label={`To ${assignment.agent.name}`}
                  name={assignment.agent.name}
                  message={message}
                  edited={message !== rendered}
                  disabled={sending}
                  onChange={(value) => setEdit(assignment.agent.agent_id, value)}
                  onReset={() => resetEdit(assignment.agent.agent_id)}
                />
              ))}
              <MessagePreview
                label="To your client"
                name={client.name || client.phone}
                message={clientMessage}
                edited={clientMessage !== renderedClientMessage}
                disabled={sending}
                onChange={(value) => setEdit(CLIENT_KEY, value)}
                onReset={() => resetEdit(CLIENT_KEY)}
              />
            </div>
          )}

          <div className="note note--info" style={{ marginTop: 16 }}>
            <span className="note__icon">
              <IconZap size={16} />
            </span>
            <div>
              Every agent gets only their own property(ies) — nobody has to ring you to ask "which flats do I show". It
              is all in their chat. Edit any message above for this hand-off only; your saved templates on the Settings
              page stay exactly as they are.
            </div>
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

/** One message, shown the way it will arrive and editable in place. The
 *  "edited" line plus Reset exist so a changed message is obvious at a
 *  glance and always recoverable — without them, an operator who typed into
 *  the wrong box would have to abandon the whole flow and redo the
 *  assignment to get the original wording back. */
function MessagePreview({
  label,
  name,
  message,
  edited,
  disabled,
  onChange,
  onReset,
}: {
  label: string;
  name: string;
  message: string;
  edited: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
  onReset: () => void;
}) {
  return (
    <div className="panel panel--pad stack stack-3" style={{ display: "flex", flexDirection: "column" }}>
      <div className="row-flex" style={{ justifyContent: "space-between", gap: 8 }}>
        <div className="section-head__eyebrow" style={{ marginBottom: 0 }}>
          {label}
        </div>
        {edited && (
          <Button size="sm" variant="ghost" onClick={onReset} disabled={disabled}>
            Reset
          </Button>
        )}
      </div>
      <div className="row-flex" style={{ gap: 10 }}>
        <Avatar name={name} size={32} />
        <div style={{ minWidth: 0 }}>
          <div className="cell-strong cell-truncate">{name}</div>
          <div className="faint small">{edited ? "edited for this hand-off" : "online"}</div>
        </div>
      </div>
      <textarea
        className="textarea"
        rows={12}
        value={message}
        disabled={disabled}
        aria-label={`${label} — message text`}
        onChange={(event) => onChange(event.target.value)}
        style={{ fontSize: 12.5, lineHeight: 1.55 }}
      />
    </div>
  );
}
