import { useEffect, useRef, useState } from "react";
import { propertyShareApi } from "../api/propertyShareApi";
import type { InquiryClientRecord, ShareTarget } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { buildClientShareMessage, type SharePropertyLike } from "../lib/propertyShareTemplate";
import SendPropertiesDialog from "./SendPropertiesDialog";
import { useToast } from "./ui/Toast";

/**
 * The client-inquiry half of "Send details on WhatsApp" — the exact
 * counterpart of ShareRequirementPropertiesDialog.tsx, reading the client
 * template instead of the requirement one and sending to the client's own
 * number.
 *
 * Which of the operator's numbers it goes out FROM is resolved entirely by
 * the backend (the number this client's inquiry arrived on, or — for a
 * website or Instagram enquiry that never came in over WhatsApp — the first
 * number selected for client inquiries on the Connection page). This
 * component only displays that answer; see
 * Backend/Service/PropertySharingService/property_share_service.py.
 *
 * Same one-shot render rule as the requirement side: the draft is seeded
 * once from the saved template and then belongs to the operator.
 */
export default function ShareClientPropertiesDialog({
  client,
  properties,
  onClose,
  onBack,
  onSent,
}: {
  client: InquiryClientRecord;
  /** Only the property cards the operator ticked. */
  properties: SharePropertyLike[];
  onClose: () => void;
  onBack?: () => void;
  onSent?: () => void;
}) {
  const toast = useToast();
  const [target, setTarget] = useState<ShareTarget | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const renderedFor = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      propertyShareApi.getTemplates(),
      propertyShareApi.getClientTarget(client.phone).catch(() => null),
    ])
      .then(([templates, resolved]) => {
        if (cancelled) return;
        setTarget(resolved ?? { to_phone: client.phone, to_name: client.name, from_number: null });
        if (renderedFor.current === null) {
          renderedFor.current = templates.client_template;
          setMessage(buildClientShareMessage(templates.client_template, client, properties));
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
    // See ShareRequirementPropertiesDialog for why `properties` is not a
    // dependency here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client.phone]);

  async function handleSend() {
    setSending(true);
    try {
      const result = await propertyShareApi.sendForClient(client.phone, message);
      if (result.sent) {
        toast.push({
          tone: "ok",
          title: "Property details sent",
          message: `${properties.length} propert${properties.length === 1 ? "y" : "ies"} sent to ${
            client.name || result.to_phone
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
      title={`Send ${properties.length} propert${properties.length === 1 ? "y" : "ies"} to ${client.name || "this client"}`}
      subtitle={clientLine(client)}
      toName={target?.to_name ?? client.name}
      toPhone={target?.to_phone ?? client.phone}
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

function clientLine(client: InquiryClientRecord): string {
  return (
    [
      [client.bhk, client.property_type].filter(Boolean).join(" ") || null,
      client.purpose ? `to ${client.purpose}` : null,
      client.preferred_areas,
    ]
      .filter(Boolean)
      .join(" · ") || client.phone
  );
}
