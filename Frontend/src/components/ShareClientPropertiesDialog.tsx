import { useEffect, useRef, useState } from "react";
import { propertyShareApi } from "../api/propertyShareApi";
import type { InquiryClientRecord, ShareTarget } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { formatPhone } from "../lib/phone";
import {
  buildClientShareParts,
  MAX_PHOTOS_PER_PROPERTY,
  type SharePropertyLike,
} from "../lib/propertyShareTemplate";
import SendPropertiesDialog from "./SendPropertiesDialog";
import { useToast } from "./ui/Toast";

/**
 * The client-inquiry half of "Send details on WhatsApp" — the counterpart
 * of ShareRequirementPropertiesDialog.tsx, reading the client template
 * instead of the requirement one and sending to the client's own number.
 *
 * Unlike the broker side, a client receives SEPARATE messages: the opening
 * text (the template before {properties}), then each property as its own
 * message — its photos first, with the details as the caption so the photos
 * and the details arrive as one message — then the closing text (the
 * template after {properties}). Every one of those is editable here.
 *
 * Which of the operator's numbers it goes out FROM is resolved entirely by
 * the backend (the number this client's inquiry arrived on, or — for a
 * website or Instagram enquiry that never came in over WhatsApp — the first
 * number selected for client inquiries on the Connection page). This
 * component only displays that answer; see
 * Backend/Service/PropertySharingService/property_share_service.py.
 *
 * Same one-shot render rule as the requirement side: the drafts are seeded
 * once from the saved template and then belong to the operator.
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
  const [intro, setIntro] = useState("");
  const [closing, setClosing] = useState("");
  const [details, setDetails] = useState<string[]>([]);
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
          const parts = buildClientShareParts(templates.client_template, client, properties);
          setIntro(parts.intro);
          setClosing(parts.closing);
          setDetails(parts.details);
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

  const detailsComplete = properties.every((_, index) => (details[index] ?? "").trim().length > 0);
  const count = properties.length;
  const noun = `propert${count === 1 ? "y" : "ies"}`;

  async function handleSend() {
    setSending(true);
    try {
      const result = await propertyShareApi.sendPropertiesToClient(client.phone, {
        intro,
        closing,
        properties: properties.map((property, index) => ({ record_id: property.record_id, details: details[index] ?? "" })),
      });
      const recipient = client.name || formatPhone(result.to_phone);
      const photos = result.photos_sent > 0 ? ` with ${result.photos_sent} photo${result.photos_sent === 1 ? "" : "s"}` : "";
      if (result.sent) {
        toast.push({
          tone: "ok",
          title: "Property details sent",
          message: `${result.properties_sent} ${noun} sent to ${recipient}${photos}${
            result.from_number ? ` from ${formatPhone(result.from_number)}` : ""
          }.`,
        });
      } else if (result.properties_sent === 0 && !result.from_number) {
        toast.push({
          tone: "warn",
          title: "Could not deliver the message",
          message: "None of your WhatsApp numbers is connected — check the Connection page and send again.",
        });
      } else {
        toast.push({
          tone: "warn",
          title: "Some messages did not go out",
          message: `${result.properties_sent} of ${count} ${noun} reached ${recipient}${photos}. Check the Connection page and resend the rest.`,
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
      title={`Send ${count} ${noun} to ${client.name || "this client"}`}
      subtitle={clientLine(client)}
      toName={target?.to_name ?? client.name}
      toPhone={target?.to_phone ?? client.phone}
      fromNumber={target?.from_number ?? null}
      propertyCount={count}
      message={intro}
      onMessageChange={setIntro}
      messageLabel="Opening message (sent first — leave empty to skip)"
      messageRows={6}
      messageOptional
      canSendExtra={detailsComplete}
      loading={loading}
      sending={sending}
      onSend={handleSend}
      onClose={onClose}
      onBack={onBack}
      backLabel="Back to matches"
    >
      {properties.map((property, index) => (
        <div className="field" key={property.record_id}>
          <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
            Property {index + 1} of {count} — its own message{" "}
            <span className="faint" style={{ fontWeight: 400 }}>
              ({photoHint(property)})
            </span>
          </label>
          <textarea
            className="textarea"
            rows={Math.min(8, (details[index] ?? "").split("\n").length + 1)}
            value={details[index] ?? ""}
            disabled={sending}
            onChange={(event) => {
              const next = event.target.value;
              setDetails((previous) => {
                const copy = [...previous];
                copy[index] = next;
                return copy;
              });
            }}
            style={{ fontSize: 13, lineHeight: 1.55 }}
          />
        </div>
      ))}
      <div className="field">
        <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
          Closing message (sent last — leave empty to skip)
        </label>
        <textarea
          className="textarea"
          rows={3}
          value={closing}
          disabled={sending}
          onChange={(event) => setClosing(event.target.value)}
          style={{ fontSize: 13, lineHeight: 1.55 }}
        />
      </div>
    </SendPropertiesDialog>
  );
}

function photoHint(property: SharePropertyLike): string {
  if (property.image_count === undefined) return "its photos, if any, go first with these details as their caption";
  if (property.image_count === 0) return "no photos — sent as a text message";
  const sent = Math.min(property.image_count, MAX_PHOTOS_PER_PROPERTY);
  const which = property.image_count > MAX_PHOTOS_PER_PROPERTY ? `first ${sent} of ${property.image_count} photos` : `${sent} photo${sent === 1 ? "" : "s"}`;
  return `${which} first, these details as the caption`;
}

function clientLine(client: InquiryClientRecord): string {
  return (
    [
      [client.bhk, client.property_type].filter(Boolean).join(" ") || null,
      client.purpose ? `to ${client.purpose}` : null,
      client.preferred_areas,
    ]
      .filter(Boolean)
      .join(" · ") || formatPhone(client.phone)
  );
}
