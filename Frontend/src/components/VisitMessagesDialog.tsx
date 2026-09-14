import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { friendlyError } from "../lib/apiError";
import { MessagePreview } from "./HandoffDialog";
import { useToast } from "./ui/Toast";
import { Button } from "./ui/Primitives";
import { IconCheck, IconSend, IconX } from "./ui/Icons";

/**
 * Offered right after a visit time is set or changed on the matches
 * dialog's Assigned tab: the agent's and the client's message side by side,
 * editable, exactly like the hand-off's own messages (it reuses that
 * dialog's MessagePreview). Sending is optional — the time was already
 * saved before this opened, so "Skip" (or just closing) keeps the time and
 * sends nothing, and Send costs no database work at all (see
 * inquiryClientApi.sendVisitMessages).
 */
export default function VisitMessagesDialog({
  clientPhone,
  clientName,
  agentName,
  agentPhone,
  rescheduled,
  whenLabel,
  agentMessage,
  clientMessage,
  onClose,
}: {
  clientPhone: string;
  clientName: string | null;
  agentName: string;
  agentPhone: string;
  /** True when this replaced an earlier time (wording only). */
  rescheduled: boolean;
  whenLabel: string;
  agentMessage: string;
  clientMessage: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [agentText, setAgentText] = useState(agentMessage);
  const [clientText, setClientText] = useState(clientMessage);
  const [sending, setSending] = useState(false);
  const clientDisplay = clientName || clientPhone;

  // Same capture-phase Escape as VisitPlannerDialog — see its comment.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const sendingRef = useRef(sending);
  sendingRef.current = sending;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (!sendingRef.current) onCloseRef.current();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);

  const nothingToSend = !agentText.trim() && !clientText.trim();

  async function handleSend() {
    setSending(true);
    try {
      const result = await inquiryClientApi.sendVisitMessages(clientPhone, {
        agent_phone: agentPhone,
        agent_message: agentText,
        client_message: clientText,
      });
      const failed = [
        result.agent_sent === false ? agentName : null,
        result.client_sent === false ? clientDisplay : null,
      ].filter((name): name is string => name !== null);
      if (failed.length === 0) {
        toast.push({ tone: "ok", title: "Messages sent", message: `The ${rescheduled ? "new " : ""}visit time went out on WhatsApp.` });
      } else {
        toast.push({
          tone: "warn",
          title: "Sent partially",
          message: `Could not reach ${failed.join(", ")} — check the inquiry WhatsApp connection on the Connection page. The visit time itself is saved.`,
        });
      }
      onClose();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not send the messages", message: `${friendlyError(err)} The visit time itself is saved.` });
    } finally {
      setSending(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !sending && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Send the visit time on WhatsApp" style={{ maxWidth: 900 }}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              <IconCheck size={11} /> Visit time {rescheduled ? "updated" : "saved"}
            </div>
            <h2 className="detail-modal__title">Tell {agentName} and {clientDisplay}?</h2>
            <div className="detail-modal__sub">📅 {whenLabel}</div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={sending} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 16 }}>
            <MessagePreview
              label={`To ${agentName}`}
              name={agentName}
              message={agentText}
              edited={agentText !== agentMessage}
              disabled={sending}
              onChange={setAgentText}
              onReset={() => setAgentText(agentMessage)}
            />
            <MessagePreview
              label="To your client"
              name={clientDisplay}
              message={clientText}
              edited={clientText !== clientMessage}
              disabled={sending}
              onChange={setClientText}
              onReset={() => setClientText(clientMessage)}
            />
          </div>
          <p className="faint small" style={{ margin: 0 }}>
            The time is already saved. Skip if you'd rather tell them yourself — nothing is sent unless you press Send.
          </p>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={sending}>
            Skip — don't send
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" icon={<IconSend size={14} />} onClick={handleSend} busy={sending} disabled={nothingToSend}>
              Send both on WhatsApp
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
