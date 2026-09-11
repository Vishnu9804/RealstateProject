import { useEffect, useRef, useState } from "react";
import { propertyShareApi } from "../api/propertyShareApi";
import type { BrokerRequirementRecord, ShareTarget } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { buildRequirementShareMessage, type SharePropertyLike } from "../lib/propertyShareTemplate";
import SendPropertiesDialog from "./SendPropertiesDialog";
import { useToast } from "./ui/Toast";

/**
 * The broker-requirement half of "Send details on WhatsApp": fetches the
 * saved template and the resolved send target, renders the message, and
 * owns the send. SendPropertiesDialog itself stays presentational (see its
 * own docstring), so this wrapper is all that differs between the two
 * sides.
 *
 * The message is rendered ONCE, when the template and the selected
 * properties first arrive, and then belongs to the operator: re-deriving it
 * on every render would silently throw away their edits the moment anything
 * else in the dialog changed. `renderedFor` is what enforces that — a
 * re-render cannot re-seed the draft, only a genuinely new template can.
 */
export default function ShareRequirementPropertiesDialog({
  requirement,
  properties,
  onClose,
  onBack,
  onSent,
}: {
  requirement: BrokerRequirementRecord;
  /** The property cards the operator ticked — never the whole matched set.
   *  Only these are described in the message. */
  properties: SharePropertyLike[];
  onClose: () => void;
  onBack?: () => void;
  /** Fired only after a send the backend accepted, so the caller can clear
   *  its selection. Also fired when delivery failed but the attempt was
   *  made — see the toast branches below, which say which happened. */
  onSent?: () => void;
}) {
  const toast = useToast();
  const [target, setTarget] = useState<ShareTarget | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  // The template the draft below was rendered from. Non-null means "the
  // draft is now the operator's" — see the component docstring.
  const renderedFor = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      propertyShareApi.getTemplates(),
      // Allowed to fail on its own: not knowing which number we'll send
      // from is worth a "No number connected" badge, not a dead dialog.
      propertyShareApi.getRequirementTarget(requirement.record_id).catch(() => null),
    ])
      .then(([templates, resolved]) => {
        if (cancelled) return;
        setTarget(
          resolved ?? {
            to_phone: requirement.sender_phone,
            to_name: requirement.contact_name || requirement.sender_saved_name || requirement.sender_name,
            from_number: null,
          },
        );
        if (renderedFor.current === null) {
          renderedFor.current = templates.requirement_template;
          setMessage(buildRequirementShareMessage(templates.requirement_template, requirement, properties));
        }
      })
      .catch((err) => {
        if (cancelled) return;
        toast.push({ tone: "bad", title: "Could not prepare the message", message: friendlyError(err) });
        onClose();
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // Intentionally keyed on the requirement alone. `properties` is fixed
    // for the life of this dialog (the selection is made before it opens),
    // and including it would re-run this and fight the operator's edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requirement.record_id]);

  async function handleSend() {
    setSending(true);
    try {
      const result = await propertyShareApi.sendForRequirement(requirement.record_id, message);
      if (result.sent) {
        toast.push({
          tone: "ok",
          title: "Property details sent",
          message: `${properties.length} propert${properties.length === 1 ? "y" : "ies"} sent to ${
            target?.to_name || result.to_phone
          }${result.from_number ? ` from ${result.from_number}` : ""}.`,
        });
      } else {
        toast.push({
          tone: "warn",
          title: "Could not deliver the message",
          message: "None of your WhatsApp numbers is connected — check the Connection page and send again.",
        });
      }
      onSent?.();
      onClose();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not send the details", message: friendlyError(err) });
    } finally {
      setSending(false);
    }
  }

  return (
    <SendPropertiesDialog
      eyebrow="Send on WhatsApp"
      title={`Send ${properties.length} propert${properties.length === 1 ? "y" : "ies"} to the broker`}
      subtitle={`For their requirement: ${requirementLine(requirement)}`}
      toName={target?.to_name ?? null}
      toPhone={target?.to_phone ?? requirement.sender_phone}
      fromNumber={target?.from_number ?? null}
      propertyCount={properties.length}
      message={message}
      onMessageChange={setMessage}
      loading={loading}
      sending={sending}
      onSend={handleSend}
      onClose={onClose}
      onBack={onBack}
      backLabel="Back to matches"
    />
  );
}

function requirementLine(requirement: BrokerRequirementRecord): string {
  const areas =
    requirement.preferred_areas.length > 0 ? requirement.preferred_areas.join(", ") : requirement.area_name;
  return (
    [
      [requirement.bhk, requirement.requirement_type].filter(Boolean).join(" ") || null,
      requirement.listing_type === "Rent" ? "to rent" : "to buy",
      areas,
    ]
      .filter(Boolean)
      .join(" · ") || requirement.sender_phone
  );
}
