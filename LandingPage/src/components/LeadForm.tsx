import { useState, type FormEvent } from "react";
import { landingApi } from "../api/landingApi";
import { isPlausiblePhone, normalizeWhatsApp } from "../lib/format";
import { IconAlert, IconArrowRight, IconCheck } from "./Icons";

/**
 * The site's only form: a name and a WhatsApp number.
 *
 * Two fields, and only two — every extra box on a public form is another
 * reason to close the tab, and everything else worth knowing gets asked in
 * the WhatsApp conversation this form exists to start.
 *
 * Used in two places (the Contact section, and each property page's enquiry
 * card), which is why the copy is passed in: the same component, saying the
 * right thing for where it stands.
 */
export default function LeadForm({
  propertyRecordId,
  submitLabel = "Send my details",
  successTitle = "Thank you — we've got it.",
  successBody = "One of us will message you on WhatsApp shortly. No forms, no call centre — just a person.",
}: {
  propertyRecordId?: string;
  submitLabel?: string;
  successTitle?: string;
  successBody?: string;
}) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  // Validation errors only appear after a submit attempt. Marking a field
  // red while someone is still typing their first character is scolding
  // them for not having finished yet.
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const nameInvalid = attempted && name.trim().length === 0;
  const phoneInvalid = attempted && !isPlausiblePhone(phone);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setAttempted(true);
    if (name.trim().length === 0 || !isPlausiblePhone(phone)) return;

    setBusy(true);
    setFailure(null);
    try {
      await landingApi.submitLead({
        name: name.trim(),
        whatsapp_number: normalizeWhatsApp(phone),
        property_record_id: propertyRecordId ?? null,
      });
      setDone(true);
    } catch {
      // Deliberately not the raw error: a visitor can do nothing with a
      // status code, and the one thing they need to know is that their
      // details did not go through and are still there to resend.
      setFailure("We couldn't send that just now. Please check your connection and try once more.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="lead-done" role="status">
        <span className="lead-done__icon">
          <IconCheck size={22} />
        </span>
        <h3>{successTitle}</h3>
        <p>{successBody}</p>
      </div>
    );
  }

  return (
    <form className="lead-form" onSubmit={onSubmit} noValidate>
      <div className="field">
        <label className="field__label" htmlFor="lead-name">
          Your name
        </label>
        <input
          id="lead-name"
          name="name"
          type="text"
          autoComplete="name"
          placeholder="e.g. Rohan Mehta"
          value={name}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={nameInvalid}
          aria-describedby={nameInvalid ? "lead-name-error" : undefined}
          maxLength={120}
        />
        {nameInvalid && (
          <span className="field__error" id="lead-name-error">
            Please tell us what to call you.
          </span>
        )}
      </div>

      <div className="field">
        <label className="field__label" htmlFor="lead-phone">
          WhatsApp number
        </label>
        <input
          id="lead-phone"
          name="whatsapp"
          // "tel" rather than "number": it brings up the phone keypad on
          // mobile, and unlike a number input it never strips a leading "+"
          // or lets a stray scroll change someone's phone number.
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="e.g. 98765 43210"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          aria-invalid={phoneInvalid}
          aria-describedby={phoneInvalid ? "lead-phone-error" : undefined}
          maxLength={24}
        />
        {phoneInvalid && (
          <span className="field__error" id="lead-phone-error">
            That doesn't look complete — please include all 10 digits.
          </span>
        )}
      </div>

      {failure && (
        <p className="form-error" role="alert">
          <IconAlert />
          {failure}
        </p>
      )}

      <button type="submit" className="btn btn--primary" disabled={busy} style={{ marginTop: 4 }}>
        {busy ? <span className="spinner" /> : null}
        {busy ? "Sending…" : submitLabel}
        {!busy && <IconArrowRight />}
      </button>

      <p className="lead-form__note">
        We only use this to reply about properties. Your number is never shared, sold or added to a broadcast list.
      </p>
    </form>
  );
}
