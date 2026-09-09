import { useEffect, useState, type FocusEvent, type FormEvent } from "react";
import { landingApi } from "../api/landingApi";
import { usePhoneVerification } from "../hooks/usePhoneVerification";
import { isPlausiblePhone, normalizeWhatsApp } from "../lib/format";
import { IconAlert, IconArrowRight, IconCheck } from "./Icons";
import PhoneVerifyDialog from "./PhoneVerifyDialog";

/**
 * The site's short form: a name and a WhatsApp number.
 *
 * Two fields, and only two — every extra box on a public form is another
 * reason to close the tab, and everything else worth knowing gets asked in
 * the WhatsApp conversation this form exists to start.
 *
 * Used in two places (the Contact section, and each property page's enquiry
 * card), which is why the copy is passed in: the same component, saying the
 * right thing for where it stands.
 *
 * The number still has to be confirmed with a 4-digit code before the
 * enquiry is sent, exactly as on the long requirements form — a lead filed
 * against a number that isn't the sender's means our reply goes to a
 * stranger, which is worse than no lead at all. Confirming once anywhere on
 * the site satisfies every form on it (see hooks/usePhoneVerification.ts),
 * so someone who already registered from the home page never sees this
 * dialog again on a property page.
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
  const [confirmHint, setConfirmHint] = useState(false);
  // Separate from confirmHint (which stays up as the inline message until
  // they confirm): this only drives the button's pulse animation, and
  // clears the instant that one play finishes.
  const [confirmPulse, setConfirmPulse] = useState(false);

  const verification = usePhoneVerification();

  const phoneLocked = verification.isVerified(phone);
  // See usePhoneVerification: "bypassed" means we had no way to send a code
  // at all, and this form goes back to behaving exactly as it always did.
  const phoneSettled = phoneLocked || verification.bypassed;

  // A number confirmed anywhere else on the site is already ours — this
  // form should never ask for it a second time.
  const storedPhone = verification.verified?.phone;
  useEffect(() => {
    if (storedPhone) setPhone(storedPhone);
  }, [storedPhone]);

  const nameInvalid = attempted && name.trim().length === 0;
  const phoneInvalid = attempted && !isPlausiblePhone(phone);

  function beginConfirm() {
    if (!isPlausiblePhone(phone)) {
      setAttempted(true);
      return;
    }
    setConfirmHint(false);
    verification.startVerification(phone.trim());
  }

  /** Moving on to the name with a plausible-but-unconfirmed number bounces
   *  focus back off it and points at Confirm instead of opening the dialog
   *  itself — pressing Confirm is the only thing that ever opens it. Same
   *  gate as the requirements form's. */
  function guardConfirmFirst(event: FocusEvent<HTMLInputElement>) {
    if (phoneSettled || verification.pendingPhone !== null) return;
    if (!isPlausiblePhone(phone)) return;
    event.currentTarget.blur();
    setConfirmHint(true);
    setConfirmPulse(true);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setAttempted(true);
    if (name.trim().length === 0 || !isPlausiblePhone(phone)) return;
    if (!phoneSettled) {
      setConfirmHint(true);
      setConfirmPulse(true);
      return;
    }

    setBusy(true);
    setFailure(null);
    try {
      await landingApi.submitLead({
        name: name.trim(),
        whatsapp_number: normalizeWhatsApp(phone),
        property_record_id: propertyRecordId ?? null,
        // When this resolves server-side it REPLACES the number above, so
        // the lead can only ever be filed against a number this browser
        // proved it owns.
        verification_token: phoneLocked ? verification.verified?.token ?? null : null,
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
      {verification.pendingPhone !== null && (
        <PhoneVerifyDialog
          phone={verification.pendingPhone}
          onVerified={(verifiedPhone, token, expiresInSeconds) => {
            setPhone(verifiedPhone);
            setConfirmHint(false);
            verification.completeVerification(verifiedPhone, token, expiresInSeconds);
          }}
          onUnavailable={() => {
            verification.markUnavailable();
            setConfirmHint(false);
          }}
          onClose={verification.cancelVerification}
        />
      )}

      <div className="field">
        <label className="field__label" htmlFor="lead-phone">
          WhatsApp number
        </label>
        <div className={phoneLocked ? "field__row" : "field__row field__row--action"}>
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
            disabled={phoneLocked}
            aria-invalid={phoneInvalid}
            aria-describedby={phoneInvalid ? "lead-phone-error" : undefined}
            maxLength={24}
          />
          {!phoneLocked && (
            <button
              type="button"
              className={`btn field__action ${isPlausiblePhone(phone) ? "btn--primary" : "btn--ghost"}${
                confirmPulse ? " is-pulsing" : ""
              }`}
              onClick={beginConfirm}
              disabled={!isPlausiblePhone(phone)}
              onAnimationEnd={() => setConfirmPulse(false)}
            >
              Confirm
            </button>
          )}
        </div>
        {phoneLocked ? (
          <span className="field__hint">Confirmed on WhatsApp — we'll use this number for everything.</span>
        ) : phoneInvalid ? (
          <span className="field__error" id="lead-phone-error">
            That doesn't look complete — please include all 10 digits.
          </span>
        ) : (
          confirmHint && (
            <span className="field__error">
              Almost there — tap Confirm and enter the 4-digit code we'll send to this number on WhatsApp.
            </span>
          )
        )}
      </div>

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
          onFocus={guardConfirmFirst}
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
