import { useEffect, useRef, useState, type FocusEvent, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { inquiryFormApi } from "../api/inquiryFormApi";
import { landingApi } from "../api/landingApi";
import { phoneVerificationApi } from "../api/phoneVerificationApi";
import type { InquiryChannel, InquiryFormPrefill, InquiryFormSubmission } from "../api/types";
import { usePhoneVerification } from "../hooks/usePhoneVerification";
import { formatBudgetDisplay, isPlausiblePhone, parseCompactInr } from "../lib/format";
import { joinAreas, mergeAreas, splitAreas, SURAT_AREAS } from "../lib/suratAreas";
import AreaPicker from "./AreaPicker";
import { IconAlert, IconArrowRight, IconCheck } from "./Icons";
import PhoneVerifyDialog from "./PhoneVerifyDialog";

const PROPERTY_TYPES = [
  "Flat",
  "Penthouse",
  "Row House",
  "Bungalow",
  "Shop",
  "Office",
  "Land/Plot",
  "Warehouse",
  "Other",
];

// No "Sell": this site exists to put buyers and tenants in front of what the
// client has listed, and a seller's enquiry has no requirements to match a
// property against — the matcher ignores the value anyway (see scoring.py's
// _purpose_gate, which refuses to guess for anything outside buy/rent).
const PURPOSES = [
  { value: "buy", label: "Buy" },
  { value: "rent", label: "Rent" },
] as const;

const PHONE_REQUIRED_MESSAGE =
  "Please enter your WhatsApp number first, so we can send you the properties matched to your requirements.";

const CONFIRM_REQUIRED_MESSAGE =
  "Almost there — tap Confirm and enter the 4-digit code we'll send to this number on WhatsApp.";

/**
 * The requirements form — the ONE form this business has, and now the only
 * one on this site.
 *
 * It used to be a standalone page in the internal tool, opened by the link
 * our WhatsApp and Instagram messages send. It isn't any more: that link
 * now lands on this site's home page (see App.tsx's /enquire/:token), which
 * scrolls down to this component. Same fields, same endpoints, same
 * behaviour — the visitor just arrives somewhere that looks like the
 * business they were already talking to, instead of a bare form on a
 * different app.
 *
 * Two ways in, and the only difference between them is where the number
 * comes from — but in BOTH cases it is a number we established, never one
 * the browser merely asserted:
 *
 *  - WITH a whatsapp token (arrived from our message): the number is the
 *    one the token was minted for. Prefilled and locked — there is nothing
 *    to type, and nothing they could type that would change who this is
 *    saved against.
 *  - WITHOUT one, or from Instagram (found the site, tapped a bio link):
 *    they type a number and then confirm it with a 4-digit code we send to
 *    it (components/PhoneVerifyDialog.tsx). Until that is done, the rest of
 *    the form is gated — because an unconfirmed number could be anyone's,
 *    and filing one person's requirements against another person's phone is
 *    the exact failure this whole feature exists to prevent.
 *
 * Once confirmed, the number is remembered in this browser
 * (lib/verifiedPhone.ts) and prefilled, locked, on every later visit —
 * along with whatever we already have on file for it, so a returning
 * visitor edits their real requirements instead of retyping them. A
 * whatsapp link ALWAYS wins over that memory: someone opening our message
 * on a shared or borrowed device must see the number the link is for, not
 * whoever last used this browser.
 *
 * One case deliberately refuses to save: a client whose site visit is
 * already assigned to an agent. They can still type — being told "you
 * can't touch this" while your own requirements are greyed out is a
 * terrible way to be treated — but the submit is refused server-side and
 * answered with a WhatsApp message explaining what we hold and asking them
 * to call. See Backend/Service/WhatsAppInquiryHandlingService/
 * assignment_lock_service.py for why that rule exists at all.
 */
export default function RequirementsForm({
  token,
  idPrefix = "req",
  contextNote,
}: {
  /** Present only when the visitor arrived from a link we sent them. */
  token?: string;
  /** Namespaces the input ids, so a second instance elsewhere in the same
   *  document never collides with this one. */
  idPrefix?: string;
  /** Seeds "Anything else?" on the property page, so an enquiry sent from a
   *  listing still says which listing it was about — the fields themselves
   *  have nowhere else to carry that. Never overwrites saved text. */
  contextNote?: string;
}) {
  // "loading" only ever happens with a token — a visitor who just scrolled
  // down here has nothing to fetch and should see the form immediately.
  const [loading, setLoading] = useState(Boolean(token));
  const [linkNote, setLinkNote] = useState<string | null>(null);
  const [isNewClient, setIsNewClient] = useState(true);
  // "whatsapp" is the only channel whose number is fixed by the link. A
  // failed or expired token falls back to null, which behaves exactly like
  // a visitor who walked in off the home page.
  const [channel, setChannel] = useState<InquiryChannel | null>(null);
  // Set only by a real link. Kept apart from `channel` because the verified
  // prefill below also describes itself as the "whatsapp" channel, and only
  // a LINK may override the browser's remembered number.
  const [fromLink, setFromLink] = useState(false);
  const [hasActiveAssignment, setHasActiveAssignment] = useState(false);

  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [purpose, setPurpose] = useState("");
  const [propertyType, setPropertyType] = useState("");
  const [bhk, setBhk] = useState("");
  // Both budget fields hold whatever is currently ON SCREEN — raw digits
  // while focused, the short "2cr"/"85L" form once blurred. The number the
  // backend gets is parsed back out of it on submit (parseCompactInr), so
  // there is only ever one string per field and no way for a display value
  // and a "real" value to drift apart.
  const [budgetMin, setBudgetMin] = useState("");
  const [budgetMax, setBudgetMax] = useState("");
  const [preferredAreas, setPreferredAreas] = useState<string[]>([]);
  const [areaOptions, setAreaOptions] = useState<string[]>(SURAT_AREAS);
  const [additionalRequirements, setAdditionalRequirements] = useState(contextNote ?? "");

  const [phoneError, setPhoneError] = useState<string | null>(null);
  // Pulses the Confirm button rather than opening the code dialog on its
  // own — clicking Confirm is the only thing that ever opens it.
  const [confirmPulse, setConfirmPulse] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // Set when the backend refused to save because a site visit is already
  // assigned — a different ending from `done`, and not a failure.
  const [lockedMessage, setLockedMessage] = useState<string | null>(null);

  const phoneRef = useRef<HTMLInputElement>(null);
  const verification = usePhoneVerification();

  // A link-supplied number is fixed by the token; a typed one is fixed once
  // it has been confirmed with a code. Nothing else locks the field.
  const phoneFromToken = fromLink && channel === "whatsapp" && phone.trim().length > 0;
  const phoneLocked = phoneFromToken || verification.isVerified(phone);
  // The one escape hatch: we couldn't send a code at all (nothing linked to
  // send from), so the form behaves exactly as it did before verification
  // existed rather than trapping a real visitor. See usePhoneVerification.
  const phoneSettled = phoneLocked || verification.bypassed;

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    inquiryFormApi
      .getPrefill(token)
      .then((data) => {
        if (cancelled) return;
        applyPrefill(data);
        setFromLink(true);
        setLinkNote(null);
      })
      .catch((error) => {
        if (cancelled) return;
        // An expired link is not a dead end: the form still works, it just
        // has to ask for the number it can no longer infer — and confirm it,
        // like any other visitor who arrived without a link.
        setChannel(null);
        setFromLink(false);
        setLinkNote(
          error instanceof ApiError && error.status === 404
            ? "That link has expired — no problem, just add your WhatsApp number below and we'll pick up from there."
            : "We couldn't load your saved details just now. Fill this in below and it will still reach us.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // The token is genuinely the only input to this fetch; applyPrefill is
    // defined inline and contextNote is a constant per mounting page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  /**
   * The returning visitor. No link, but this browser confirmed a number
   * before — so the number comes back, and so does everything we already
   * hold for it. A link always takes precedence (see the component
   * docstring), which is why this is skipped entirely when one is present.
   */
  const storedToken = verification.verified?.token;
  const storedPhone = verification.verified?.phone;
  useEffect(() => {
    if (token || !storedToken || !storedPhone) return;
    let cancelled = false;
    setPhone(storedPhone);
    phoneVerificationApi
      .verifiedPrefill(storedToken)
      .then((data) => {
        if (cancelled) return;
        applyPrefill(data);
      })
      .catch((error) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          // The proof expired (or the server restarted). The number is
          // almost certainly still theirs, so it stays on screen — it just
          // needs confirming again before anything is saved against it.
          verification.forgetVerification();
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, storedToken, storedPhone]);

  function applyPrefill(data: InquiryFormPrefill) {
    setIsNewClient(data.is_new_client);
    setChannel(data.channel);
    setHasActiveAssignment(data.has_active_assignment);
    setPhone(data.phone ?? "");
    setName(data.name ?? "");
    setEmail(data.email ?? "");
    setPurpose(data.purpose ?? "");
    setPropertyType(data.property_type ?? "");
    setBhk(data.bhk ?? "");
    // Straight into the short form — a returning visitor reads their own
    // saved budget back as "85L", never as a wall of zeroes to count.
    setBudgetMin(data.budget_min_inr != null ? formatBudgetDisplay(data.budget_min_inr) : "");
    setBudgetMax(data.budget_max_inr != null ? formatBudgetDisplay(data.budget_max_inr) : "");
    setPreferredAreas(splitAreas(data.preferred_areas));
    // Their own saved note wins over the "interested in X" seed — that seed
    // is a convenience, not something worth overwriting real text with.
    setAdditionalRequirements(data.additional_requirements ?? contextNote ?? "");
  }

  /**
   * The area capsules the picker offers: this site's own Surat list, plus
   * whichever areas the client has configured on the internal Settings page
   * that it doesn't already cover. Merged, never replaced — the built-in
   * list is what keeps the picker looking populated when only a handful are
   * configured, and a Settings area already on it is not added twice (see
   * lib/suratAreas.ts's mergeAreas).
   *
   * Failure is silent on purpose: the picker still works perfectly off the
   * built-in list, so a dead settings call is not worth a visible error on
   * a form someone is trying to fill in.
   */
  useEffect(() => {
    let cancelled = false;
    landingApi
      .getAreas()
      .then((areas) => {
        if (!cancelled && areas.length > 0) setAreaOptions(mergeAreas(SURAT_AREAS, areas));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  function onPhoneChange(value: string) {
    setPhone(value);
    if (phoneError && isPlausiblePhone(value)) setPhoneError(null);
  }

  /** Blur: show the short form ("2cr"). Focus: put the full number back, so
   *  editing means editing digits rather than picking apart "2.5cr". Text
   *  that isn't a number at all is left exactly as typed — this is a
   *  convenience, not a validator. */
  function onBudgetBlur(value: string, set: (next: string) => void) {
    const amount = parseCompactInr(value);
    if (amount !== null) set(formatBudgetDisplay(amount));
  }

  function onBudgetFocus(value: string, set: (next: string) => void) {
    const amount = parseCompactInr(value);
    if (amount !== null) set(String(amount));
  }

  /** Opens the code dialog, which sends the code the moment it mounts.
   *  The ONLY thing that ever opens it — every other gate below just points
   *  at this button instead of triggering it itself. */
  function beginConfirm() {
    if (!isPlausiblePhone(phone)) {
      setPhoneError(PHONE_REQUIRED_MESSAGE);
      phoneRef.current?.focus();
      return;
    }
    setPhoneError(null);
    setConfirmPulse(false);
    verification.startVerification(phone.trim());
  }

  /**
   * The "number first" gate, now a "number CONFIRMED first" gate. Anyone
   * whose number we haven't established is dealt with before they start
   * filling in the rest — this sits on the wrapper around every OTHER
   * field, in the capture phase, so it fires whichever of them is tabbed or
   * clicked into.
   *
   * Two different situations, two different responses — neither of which
   * opens the dialog on its own; only pressing Confirm does that:
   *
   *  - Nothing usable typed yet: the original message, and focus goes back
   *    to the number, because at that point they have typed nothing at all
   *    and a message alone is easy to scroll straight past.
   *  - A plausible number typed but not confirmed: focus is bounced off the
   *    field they just tried to use, an inline message says why, and the
   *    Confirm button pulses so it's obvious what to do next.
   *
   * The submit button sits OUTSIDE this wrapper and is never intercepted —
   * it runs its own validation, and a button that silently refuses a click
   * is exactly the kind of "broken" this is meant to avoid.
   */
  function guardPhoneFirst(event: FocusEvent<HTMLDivElement>) {
    if (phoneSettled) return;
    if (event.target === phoneRef.current) return;
    if (verification.pendingPhone !== null) return;

    if (isPlausiblePhone(phone)) {
      (event.target as HTMLElement).blur();
      setPhoneError(CONFIRM_REQUIRED_MESSAGE);
      setConfirmPulse(true);
      return;
    }
    setPhoneError(PHONE_REQUIRED_MESSAGE);
    if (phone.trim().length === 0) phoneRef.current?.focus();
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;

    if (!phoneSettled) {
      setFormError(null);
      if (!isPlausiblePhone(phone)) {
        setPhoneError(PHONE_REQUIRED_MESSAGE);
        phoneRef.current?.focus();
        return;
      }
      // They came straight to Send without confirming. Same answer as the
      // focus gate: point at Confirm rather than opening the dialog for them.
      setPhoneError(CONFIRM_REQUIRED_MESSAGE);
      setConfirmPulse(true);
      phoneRef.current?.focus();
      return;
    }
    if (!name.trim()) {
      setFormError("Please tell us what to call you.");
      return;
    }
    if (!purpose) {
      setFormError("Please choose whether you want to buy or rent.");
      return;
    }

    setBusy(true);
    setFormError(null);
    setPhoneError(null);

    // Blank text is sent as null, not "": that is what tells the backend a
    // field was deliberately cleared rather than left untouched (see
    // Backend/Service/WhatsAppInquiryHandlingService/inquiry_form_service.py).
    const body: InquiryFormSubmission = {
      name: name.trim(),
      email: email.trim() || null,
      purpose: purpose || null,
      property_type: propertyType || null,
      bhk: bhk.trim() || null,
      // The full rupee figure, always — "2cr" is only ever what the FIELD
      // shows. Nothing downstream (the matcher's budget curve, the agent
      // hand-off, the stored client record) sees anything but the number.
      budget_min_inr: parseCompactInr(budgetMin),
      budget_max_inr: parseCompactInr(budgetMax),
      preferred_areas: joinAreas(preferredAreas) || null,
      additional_requirements: additionalRequirements.trim() || null,
    };
    const verificationToken = verification.isVerified(phone) ? verification.verified?.token : undefined;

    try {
      let result;
      if (token && fromLink && channel === "whatsapp") {
        // Identity is the token's. A phone sent here would be ignored
        // anyway; omitting it keeps that invariant visible in the request.
        result = await inquiryFormApi.submit(token, body);
      } else if (token && fromLink && channel === "instagram") {
        result = await inquiryFormApi.submit(token, {
          ...body,
          phone: phone.trim(),
          verification_token: verificationToken ?? null,
        });
      } else {
        result = await inquiryFormApi.submitPublic({
          ...body,
          phone: phone.trim(),
          verification_token: verificationToken ?? null,
        });
      }
      // "locked" is a deliberate refusal, not a failure — the client has a
      // site visit assigned and has just been messaged about it on WhatsApp.
      if (result?.status === "locked") setLockedMessage(result.message ?? null);
      setDone(true);
    } catch (error) {
      setFormError(
        error instanceof ApiError && error.status === 400
          ? "That WhatsApp number doesn't look right — please check it and try again."
          : "We couldn't send that just now. Please check your connection and try once more.",
      );
    } finally {
      setBusy(false);
    }
  }

  const dialog =
    verification.pendingPhone !== null ? (
      <PhoneVerifyDialog
        phone={verification.pendingPhone}
        onVerified={(verifiedPhone, verifiedToken, expiresInSeconds) => {
          // The canonical E.164 form replaces whatever was typed, so what
          // is shown, stored and submitted are all the same string.
          setPhone(verifiedPhone);
          setPhoneError(null);
          verification.completeVerification(verifiedPhone, verifiedToken, expiresInSeconds);
        }}
        onUnavailable={() => {
          verification.markUnavailable();
          setPhoneError(null);
        }}
        onClose={() => {
          verification.cancelVerification();
          phoneRef.current?.focus();
        }}
      />
    ) : null;

  if (loading) {
    return (
      <div className="req-form req-form--loading" aria-busy="true" aria-label="Loading your details">
        {[200, 320, 320, 260, 320].map((width, index) => (
          <div key={index} className="skeleton req-form__skeleton" style={{ maxWidth: width }} />
        ))}
      </div>
    );
  }

  if (done) {
    return (
      <div className="lead-done" role="status">
        <span className="lead-done__icon">
          {lockedMessage ? <IconAlert size={22} /> : <IconCheck size={22} />}
        </span>
        <h3>{lockedMessage ? "We've messaged you on WhatsApp." : "Thank you — we've got it."}</h3>
        <p>
          {lockedMessage ??
            "Your requirements are saved, and one of us will message you on WhatsApp shortly with the properties that actually fit. No call centre — just a person."}
        </p>
      </div>
    );
  }

  return (
    <form className="req-form" onSubmit={onSubmit} noValidate>
      {dialog}

      {linkNote && (
        <p className="form-note">
          <IconAlert size={16} />
          {linkNote}
        </p>
      )}

      {hasActiveAssignment && (
        <p className="form-note">
          <IconAlert size={16} />
          You have a site visit assigned with one of our agents, so your requirements are locked while that's in
          progress. Do send your changes — we'll message you on WhatsApp and a quick call is all it takes to update
          them.
        </p>
      )}

      {!isNewClient && <span className="req-form__badge">Updating the details we already have for you</span>}

      {/* Always first, always alone — it is the one field the rest of this
          form is useless without, and the one that has to be PROVEN before
          anything is saved against it. */}
      <div className="field">
        <label className="field__label" htmlFor={`${idPrefix}-phone`}>
          WhatsApp number
        </label>
        <div className={phoneLocked ? "field__row" : "field__row field__row--action"}>
          <input
            id={`${idPrefix}-phone`}
            ref={phoneRef}
            name="whatsapp"
            // "tel", not "number": it brings up the phone keypad, never
            // strips a leading "+", and can't be changed by a stray scroll.
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            placeholder="e.g. 98765 43210"
            value={phone}
            onChange={(event) => onPhoneChange(event.target.value)}
            disabled={phoneLocked}
            aria-invalid={Boolean(phoneError)}
            aria-describedby={phoneError ? `${idPrefix}-phone-error` : undefined}
            maxLength={24}
          />
          {!phoneLocked && (
            // Turns gold — the site's one "go" colour — the instant the
            // number looks complete, so it's obviously the next thing to
            // press without needing to be told. Pulses on top of that when
            // the visitor tried to move on without pressing it.
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
          <span className="field__hint">
            {phoneFromToken
              ? "Taken from your WhatsApp chat with us — nothing to type."
              : "Confirmed on WhatsApp — we'll use this number for everything."}
          </span>
        ) : (
          phoneError && (
            <span className="field__error" id={`${idPrefix}-phone-error`}>
              {phoneError}
            </span>
          )
        )}
      </div>

      <div className="req-form__rest" onFocusCapture={guardPhoneFirst}>
        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-name`}>
            Your name
          </label>
          <input
            id={`${idPrefix}-name`}
            name="name"
            type="text"
            autoComplete="name"
            placeholder="e.g. Rohan Mehta"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-email`}>
            Email (optional)
          </label>
          <input
            id={`${idPrefix}-email`}
            name="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            maxLength={160}
          />
        </div>

        <div className="field">
          <span className="field__label">I want to</span>
          <div className="seg" role="group" aria-label="Purpose">
            {PURPOSES.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`seg__btn${purpose === option.value ? " is-active" : ""}`}
                onClick={() => setPurpose(option.value)}
                aria-pressed={purpose === option.value}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-type`}>
            Property type
          </label>
          <select id={`${idPrefix}-type`} value={propertyType} onChange={(event) => setPropertyType(event.target.value)}>
            <option value="">Select…</option>
            {PROPERTY_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-bhk`}>
            BHK / configuration
          </label>
          <input
            id={`${idPrefix}-bhk`}
            type="text"
            placeholder="e.g. 3 BHK, 2/3 BHK, 3+ BHK"
            value={bhk}
            onChange={(event) => setBhk(event.target.value)}
            maxLength={40}
          />
          {/* Each of these maps to a shape the matcher actually parses (see
              normalization.py's parse_bhk_intent) — they are examples of
              real behaviour, not decoration: "3+" opens up everything
              larger, "exactly 3" closes it down again. */}
          <span className="field__hint">
            Write it however you think of it — “3 BHK”, “2 or 3 BHK”, “3+ BHK”, “exactly 3 BHK”, “1 RK”, “studio”.
          </span>
        </div>

        <div className="field">
          <span className="field__label">Budget (₹)</span>
          <div className="req-form__pair">
            {/* "text", not "number": the field shows "2cr" the moment it
                loses focus, and a number input refuses to display that at
                all (it blanks itself instead). inputMode still brings up a
                numeric keypad, which is what is actually typed into it. */}
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              placeholder="Min"
              aria-label="Minimum budget"
              value={budgetMin}
              onChange={(event) => setBudgetMin(event.target.value)}
              onFocus={() => onBudgetFocus(budgetMin, setBudgetMin)}
              onBlur={() => onBudgetBlur(budgetMin, setBudgetMin)}
              maxLength={20}
            />
            <input
              type="text"
              inputMode="decimal"
              autoComplete="off"
              placeholder="Max"
              aria-label="Maximum budget"
              value={budgetMax}
              onChange={(event) => setBudgetMax(event.target.value)}
              onFocus={() => onBudgetFocus(budgetMax, setBudgetMax)}
              onBlur={() => onBudgetBlur(budgetMax, setBudgetMax)}
              maxLength={20}
            />
          </div>
          <span className="field__hint">
            Type the full amount — we'll shorten it for you (20000000 becomes 2cr, 8500000 becomes 85L).
          </span>
        </div>

        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-areas`}>
            Preferred areas
          </label>
          <AreaPicker
            id={`${idPrefix}-areas`}
            selected={preferredAreas}
            onChange={setPreferredAreas}
            options={areaOptions}
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-notes`}>
            Anything else? (optional)
          </label>
          <textarea
            id={`${idPrefix}-notes`}
            rows={3}
            placeholder="Parking, floor, possession date — anything that matters to you…"
            value={additionalRequirements}
            onChange={(event) => setAdditionalRequirements(event.target.value)}
            maxLength={1000}
          />
        </div>
      </div>

      {formError && (
        <p className="form-error" role="alert">
          <IconAlert />
          {formError}
        </p>
      )}

      <button type="submit" className="btn btn--primary" disabled={busy} style={{ marginTop: 4 }}>
        {busy ? <span className="spinner" /> : null}
        {busy ? "Sending…" : isNewClient ? "Send my requirements" : "Save my changes"}
        {!busy && <IconArrowRight />}
      </button>

      <p className="req-form__note">
        We only use this to reply about properties. Your number is never shared, sold or added to a broadcast list.
      </p>
    </form>
  );
}
