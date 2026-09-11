import { useEffect } from "react";
import { createPortal } from "react-dom";
import { Avatar, Badge, Button, Note } from "./ui/Primitives";
import { IconAlert, IconInfo, IconSend, IconX } from "./ui/Icons";

/**
 * The last step before a property shortlist actually leaves: who it is
 * going to, which of the operator's own numbers it will appear to come
 * from, and the exact message — editable right here.
 *
 * WHY THE MESSAGE IS EDITABLE HERE AND NOT ONLY IN SETTINGS
 *
 * The Settings template is the DEFAULT wording, and a default is never
 * right for every send: one broker needs a line about parking, one client
 * needs to be reminded which visit this follows. Before this dialog, the
 * only way to change a word was to go and change everyone's template. So
 * the text is editable in place, and — the important half — editing it here
 * does NOT write back to Settings. The next send starts from the saved
 * template again, unchanged. That is stated on screen rather than left to
 * be discovered, because a "helpful" silent save would quietly rewrite the
 * operator's default from a one-off tweak.
 *
 * WHY IT STATES THE SENDING NUMBER
 *
 * Which linked number a message goes out from is backend state the operator
 * cannot see from here (it is the number this conversation arrived on, or
 * the first one selected for client inquiries — see
 * Backend/Service/PropertySharingService/property_share_service.py). A
 * shortlist arriving from an unexpected number reads as a stranger's, so it
 * is shown before sending, not after. `fromNumber` null means nothing is
 * connected right now: sending is still allowed and simply reports itself
 * as not delivered, which is the same contract every other outbound action
 * in this app has.
 *
 * Deliberately presentational: it neither fetches nor sends. Each caller
 * owns its own template/target fetch and its own send call, so this one
 * component serves both the broker-requirement and the client-inquiry
 * flows without knowing which it is in.
 */
export default function SendPropertiesDialog({
  eyebrow,
  title,
  subtitle,
  toName,
  toPhone,
  fromNumber,
  propertyCount,
  message,
  onMessageChange,
  sending,
  loading,
  onSend,
  onClose,
  onBack,
  backLabel = "Back",
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  /** Display name of the recipient — falls back to their number when we
   *  have no name for them, which is common for a broker in a group. */
  toName: string | null;
  toPhone: string;
  /** Which of the operator's own numbers this will be sent from. null =
   *  nothing connected right now. */
  fromNumber: string | null;
  propertyCount: number;
  message: string;
  onMessageChange: (next: string) => void;
  sending: boolean;
  /** True while the template / target are still being fetched — the
   *  textarea is not rendered empty and editable before the real default
   *  arrives, or an operator could start typing into a draft that is about
   *  to be replaced. */
  loading: boolean;
  onSend: () => void;
  onClose: () => void;
  /** Only supplied when there is a previous step to go back to. */
  onBack?: () => void;
  backLabel?: string;
}) {
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

  const recipient = toName || toPhone;
  const canSend = !loading && !sending && message.trim().length > 0;

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !sending && onClose()}>
      <div
        className="detail-modal anim-rise"
        role="dialog"
        aria-modal="true"
        aria-label="Send property details on WhatsApp"
        style={{ maxWidth: 720 }}
      >
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">{eyebrow}</div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
            <div className="detail-modal__badges">
              <Badge tone="accent">
                {propertyCount} propert{propertyCount === 1 ? "y" : "ies"}
              </Badge>
              {fromNumber ? (
                <Badge tone="ok">Sending from {fromNumber}</Badge>
              ) : (
                <Badge tone="warn">No number connected</Badge>
              )}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={sending} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body stack stack-4">
          <div className="row-flex" style={{ gap: 10 }}>
            <Avatar name={recipient} size={34} />
            <div style={{ minWidth: 0 }}>
              <div className="cell-strong cell-truncate">To {recipient}</div>
              <div className="faint small">{toPhone}</div>
            </div>
          </div>

          {/* No number connected: say so before the operator presses Send,
              rather than reporting a failure after they already believe the
              message went out. */}
          {!loading && !fromNumber && (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              None of your WhatsApp numbers is connected right now, so this message cannot be delivered. Reconnect on
              the <strong>Connection</strong> page and send again.
            </Note>
          )}

          <div className="field">
            <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
              Message being sent
            </label>
            <textarea
              className="textarea"
              rows={14}
              value={loading ? "" : message}
              disabled={loading || sending}
              placeholder={loading ? "Preparing the message…" : undefined}
              onChange={(event) => onMessageChange(event.target.value)}
              style={{ fontSize: 13, lineHeight: 1.55 }}
            />
          </div>

          <Note tone="info" icon={<IconInfo size={16} />}>
            Edit anything above and only <strong>this</strong> message changes — your saved template on the Settings
            page stays exactly as it is.
          </Note>
        </div>

        <div className="detail-modal__foot">
          {onBack && (
            <Button variant="ghost" onClick={onBack} disabled={sending}>
              {backLabel}
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} disabled={sending}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" icon={<IconSend size={14} />} onClick={onSend} busy={sending} disabled={!canSend}>
              Send on WhatsApp
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
