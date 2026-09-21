import { useEffect, useRef, useState, type FocusEvent, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { inquiryFormApi } from "../api/inquiryFormApi";
import { landingApi } from "../api/landingApi";
import { phoneVerificationApi } from "../api/phoneVerificationApi";
import type { InquiryChannel, InquiryFormPrefill, InquiryFormSubmission } from "../api/types";
import { usePhoneVerification } from "../hooks/usePhoneVerification";
import { formatBudgetDisplay, isPlausiblePhone, readBudget } from "../lib/format";
import { joinAreas, mergeAreas, splitAreas, SURAT_AREAS } from "../lib/suratAreas";
import {
  AREA_UNITS,
  SIZE_HINT,
  joinSizeValue,
  sizeTextError,
  splitSizeValue,
  splitTypes,
  withStored,
  type AreaUnit,
} from "../lib/propertyTypeOptions";
import AreaPicker from "./AreaPicker";
import { IconAlert, IconArrowRight, IconCheck, IconEdit } from "./Icons";
import PhoneVerifyDialog from "./PhoneVerifyDialog";

/** The size boxes' two halves, held apart while they are being typed and
 *  joined into the one string the backend stores only on submit. Keyed by
 *  the type exactly as it appears in the picked list. */
function toSizeState(
  types: string[],
  stored: Record<string, string> | null | undefined,
): { texts: Record<string, string>; units: Record<string, AreaUnit | ""> } {
  const texts: Record<string, string> = {};
  const units: Record<string, AreaUnit | ""> = {};
  for (const [key, value] of Object.entries(stored ?? {})) {
    const type = types.find((candidate) => candidate.toLowerCase() === key.trim().toLowerCase());
    if (!type || !value) continue;
    const { text, unit } = splitSizeValue(value);
    texts[type] = text;
    units[type] = unit;
  }
  return { texts, units };
}

function sizeInputId(prefix: string, type: string): string {
  return `${prefix}-size-${type.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

// No "Sell": this site exists to put buyers and tenants in front of what the
// client has listed, and a seller's enquiry has no requirements to match a
// property against — the matcher ignores the value anyway (see scoring.py's
// _purpose_gate, which refuses to guess for anything outside buy/rent).
const PURPOSES = [
  { value: "buy", label: "Buy" },
  { value: "rent", label: "Rent" },
] as const;

/** The three values a PROPERTY's furnishing is normalized onto by the
 *  extractor (Backend/Agent/WhatsAppDataFetchingAgent/glm_extraction_schema.py),
 *  offered here word-for-word so the two sides compare directly in matching.
 *  Optional: left unanswered it is not a preference and is never scored — and
 *  even when answered it only nudges the order (Backend/Service/
 *  ClientPropertyMatchingService/scoring.py weighs it lowest of everything),
 *  because furnishing is the easiest thing about a home to change. */
const FURNISHING_OPTIONS = ["Fully furnished", "Semi furnished", "Unfurnished"];

const PHONE_REQUIRED_MESSAGE =
  "Please enter your WhatsApp number first, so we can send you the properties matched to your requirements.";

/** "you haven't typed one" and "that isn't one" are different problems and
 *  used to get the same sentence, which reads as though the box had been
 *  ignored. isPlausiblePhone is deliberately generous (see lib/format.ts)
 *  — any country, any grouping — so anything it turns down really is not a
 *  phone number. */
const PHONE_INVALID_MESSAGE =
  "That doesn't look like a phone number — digits only, please (a country code is fine).";

function phoneProblem(value: string): string {
  return value.trim().length === 0 ? PHONE_REQUIRED_MESSAGE : PHONE_INVALID_MESSAGE;
}

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
  onSubmitted,
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
  /** Called ONLY when a submission was actually saved ("ok") — never for a
   *  refusal, which the visitor has to stay and read. The home page uses it
   *  to acknowledge the submission and carry them up to the properties;
   *  anywhere that passes nothing keeps the plain inline ending. */
  onSubmitted?: () => void;
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
  // How many more times this number may submit before the form refuses (see
  // the backend's MAX_REQUIREMENT_SUBMISSIONS). null until a prefill answers
  // — and for a visitor who has never been looked up, which is exactly the
  // case where there is nothing worth warning anyone about.
  const [updatesRemaining, setUpdatesRemaining] = useState<number | null>(null);

  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [purpose, setPurpose] = useState("");
  // Every type they'd consider, in the order picked — sent as one
  // comma-separated string ("Flat, Bungalow"), which is how the backend
  // stores it and how the matcher reads it (each type scored on its own).
  const [propertyTypes, setPropertyTypes] = useState<string[]>([]);
  // One optional size per picked type, keyed by the type: the number or
  // range on one side, the unit tapped beside it on the other. A size
  // survives its type being un-ticked (ticking it again brings it back),
  // but only sizes for types still picked are ever sent — see sizesToSubmit.
  const [propertySizes, setPropertySizes] = useState<Record<string, string>>({});
  const [propertyUnits, setPropertyUnits] = useState<Record<string, AreaUnit | "">>({});
  const [bhk, setBhk] = useState("");
  // "" means "no preference", which is exactly what most people have — see
  // FURNISHING_OPTIONS. Sent as null in that case.
  const [furnishing, setFurnishing] = useState("");
  // Each budget box holds exactly what was typed — "2.5 cr", "85 L" — and
  // is never rewritten under the visitor's fingers. The rupee figure the
  // backend stores is read out of it (lib/format.ts's readBudget), and a box
  // it can't read is pointed out rather than silently sent as nothing.
  const [budgetMin, setBudgetMin] = useState("");
  const [budgetMax, setBudgetMax] = useState("");
  // A box's error waits until it has been left once, so nobody is told
  // "we couldn't read 2" while they are still typing "2.5 cr".
  const [budgetTouched, setBudgetTouched] = useState({ min: false, max: false });
  const minBudget = readBudget(budgetMin);
  const maxBudget = readBudget(budgetMax);
  const budgetOrderError =
    minBudget.amount !== null && maxBudget.amount !== null && minBudget.amount > maxBudget.amount
      ? "Your minimum budget is higher than the maximum — could you swap them round?"
      : null;
  const budgetMessage =
    (budgetTouched.min && minBudget.error) || (budgetTouched.max && maxBudget.error) || budgetOrderError || null;
  const [preferredAreas, setPreferredAreas] = useState<string[]>([]);
  const [areaOptions, setAreaOptions] = useState<string[]>(SURAT_AREAS);
  const [additionalRequirements, setAdditionalRequirements] = useState(contextNote ?? "");

  // "Change number" was pressed: the field opens back up for typing. It
  // says nothing about whether the number is CONFIRMED — see the three
  // flags below, where that distinction is the whole point.
  const [editingPhone, setEditingPhone] = useState(false);
  const [phoneError, setPhoneError] = useState<string | null>(null);
  // Pulses the Confirm button rather than opening the code dialog on its
  // own — clicking Confirm is the only thing that ever opens it.
  const [confirmPulse, setConfirmPulse] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // Set when the backend deliberately refused to SAVE — a site visit is
  // already assigned, or this number has used up its online updates. A
  // different ending from `done`, and neither of them a failure: the
  // difference that matters is that the requirements on file are unchanged
  // and a person, not the form, is now the way to change them.
  const [refusal, setRefusal] = useState<{ title: string; body: string } | null>(null);

  const phoneRef = useRef<HTMLInputElement>(null);
  const verification = usePhoneVerification();

  // Three flags, and keeping them apart is what makes "Change number"
  // safe to add:
  //
  //  - phoneEstablished: we KNOW this number is theirs. A link-supplied one
  //    is fixed by its token; a typed one, once confirmed. Nothing a
  //    button does can change this — only the number in the box can.
  //  - phoneLocked: purely about the input being read-only. This is the
  //    only thing "Change number" touches.
  //  - phoneSettled: whether the rest of the form may be filled in. Reads
  //    phoneEstablished, deliberately NOT phoneLocked — someone who opened
  //    the field and left the same confirmed number in it has proved
  //    nothing less than they had a second ago, and re-gating the whole
  //    form at them for pressing a button would be nonsense.
  //
  // bypassed is the one escape hatch: we couldn't send a code at all
  // (nothing linked to send from), so the form behaves exactly as it did
  // before verification existed rather than trapping a real visitor.
  const phoneFromToken = fromLink && channel === "whatsapp" && phone.trim().length > 0;
  const phoneEstablished = phoneFromToken || verification.isVerified(phone);
  const phoneLocked = phoneEstablished && !editingPhone;
  const phoneSettled = phoneEstablished || verification.bypassed;

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
    setUpdatesRemaining(data.updates_remaining ?? null);
    setPhone(data.phone ?? "");
    setName(data.name ?? "");
    setEmail(data.email ?? "");
    setPurpose(data.purpose ?? "");
    const types = splitTypes(data.property_type);
    setPropertyTypes(types);
    const sizes = toSizeState(types, data.property_sizes);
    setPropertySizes(sizes.texts);
    setPropertyUnits(sizes.units);
    setBhk(data.bhk ?? "");
    setFurnishing(data.furnishing ?? "");
    // Written back the way they'd type it — a returning visitor reads their
    // own saved budget as "85 L", never as a wall of zeroes to count.
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

  function toggleType(type: string) {
    setPropertyTypes((previous) =>
      previous.includes(type) ? previous.filter((picked) => picked !== type) : [...previous, type],
    );
  }

  /** Sizes for the types still picked, blanks left out — null when none
   *  are left, which is how the backend is told there is nothing to keep.
   *  Each value carries the unit tapped beside it ("1000-1500 sqft",
   *  "150 var"), which is what the matcher reads it by. */
  function sizesToSubmit(): Record<string, string> | null {
    const sizes: Record<string, string> = {};
    for (const type of propertyTypes) {
      const size = joinSizeValue(propertySizes[type] ?? "", propertyUnits[type] ?? "");
      if (size) sizes[type] = size;
    }
    return Object.keys(sizes).length > 0 ? sizes : null;
  }

  /** The first thing wrong with any size box, or null. A box with something
   *  typed in it has to be a number or a range AND has to say which unit
   *  that is: a bare number is ambiguous, and 200 sqft and 200 var are not
   *  the same property. An empty box is never a problem — a size is
   *  optional. */
  function sizeProblem(): string | null {
    for (const type of propertyTypes) {
      const text = (propertySizes[type] ?? "").trim();
      if (!text) continue;
      const shape = sizeTextError(text);
      if (shape) return `${type} size: ${shape}`;
      if (!propertyUnits[type]) return `Please tap sqft or var for the ${type} size.`;
    }
    return null;
  }

  /** Opens the code dialog, which sends the code the moment it mounts.
   *  The ONLY thing that ever opens it — every other gate below just points
   *  at this button instead of triggering it itself.
   *
   *  Except when there is nothing left to confirm. Someone who presses
   *  "Change number", thinks better of it (or retypes the very number they
   *  just confirmed) and presses Confirm is asking us to re-prove something
   *  this browser is still holding the proof of. That closes the field
   *  again on the spot: no dialog, no WhatsApp message, and — the part
   *  that matters for a database billed by the hour — not so much as an
   *  HTTP request. Which also means the loop someone might try to abuse
   *  (confirm, change, confirm, change…) costs the server nothing at all
   *  after the first code, because it never reaches the server. */
  function beginConfirm() {
    if (!isPlausiblePhone(phone)) {
      setPhoneError(phoneProblem(phone));
      phoneRef.current?.focus();
      return;
    }
    setPhoneError(null);
    setConfirmPulse(false);
    if (verification.isVerified(phone)) {
      // Snap back to the canonical E.164 the confirmation was minted for,
      // exactly as a real one would have — "9876543210" and
      // "+919876543210" are the same number to us but only one of them is
      // the string we store and submit, and the field should show that one.
      if (verification.verified) setPhone(verification.verified.phone);
      setEditingPhone(false);
      return;
    }
    verification.startVerification(phone.trim());
  }

  /** Re-opens the number for typing. Deliberately does NOT drop the stored
   *  verification: it is still valid proof for the number it was minted
   *  for, which is exactly what lets the same number be re-confirmed above
   *  without another code. */
  function beginEditPhone() {
    setEditingPhone(true);
    setPhoneError(null);
    // Selected, not just focused — the point of pressing this is to
    // replace the number, so the first keystroke should replace it.
    window.setTimeout(() => {
      phoneRef.current?.focus();
      phoneRef.current?.select();
    }, 0);
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
    setPhoneError(phoneProblem(phone));
    if (phone.trim().length === 0) phoneRef.current?.focus();
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;

    if (!phoneSettled) {
      setFormError(null);
      if (!isPlausiblePhone(phone)) {
        setPhoneError(phoneProblem(phone));
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
    const budgetProblem = minBudget.error ?? maxBudget.error ?? budgetOrderError;
    if (budgetProblem) {
      setBudgetTouched({ min: true, max: true });
      setFormError(budgetProblem);
      return;
    }
    const sizeIssue = sizeProblem();
    if (sizeIssue) {
      setFormError(sizeIssue);
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
      property_type: propertyTypes.join(", ") || null,
      property_sizes: sizesToSubmit(),
      bhk: bhk.trim() || null,
      furnishing: furnishing || null,
      // The full rupee figure, always — "2.5 cr" is only ever what the BOX
      // holds. Nothing downstream (the matcher's budget curve, the agent
      // hand-off, the stored client record) sees anything but the number.
      budget_min_inr: minBudget.amount,
      budget_max_inr: maxBudget.amount,
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
      // Two deliberate refusals, neither of them a failure. "locked": a site
      // visit is assigned and they have just been messaged about it on
      // WhatsApp. "limit_reached": the online update allowance is spent, and
      // deliberately NO message is sent for that one (see the backend) — so
      // the words on screen are the only explanation there will be, which is
      // why they are shown rather than a generic error.
      if (result?.status === "locked") {
        setRefusal({
          title: "We've messaged you on WhatsApp.",
          body:
            result.message ??
            "You have a site visit assigned with one of our agents, so we've kept your requirements as they are for now.",
        });
      } else if (result?.status === "limit_reached") {
        setRefusal({
          title: "We've kept your requirements as they are.",
          body:
            result.message ??
            "You've already updated your requirements three times. Just message us on WhatsApp or give us a call and we'll gladly make any further changes for you.",
        });
      } else {
        // Saved. The page this form is on decides what happens next — the
        // home page acknowledges it and carries them up to the properties.
        onSubmitted?.();
      }
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
        priorToken={verification.verified?.token}
        onVerified={(verifiedPhone, verifiedToken, expiresInSeconds) => {
          // The canonical E.164 form replaces whatever was typed, so what
          // is shown, stored and submitted are all the same string.
          setPhone(verifiedPhone);
          setPhoneError(null);
          setEditingPhone(false);
          verification.completeVerification(verifiedPhone, verifiedToken, expiresInSeconds);
        }}
        onUnavailable={() => {
          verification.markUnavailable();
          setPhoneError(null);
          setEditingPhone(false);
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
        <span className="lead-done__icon">{refusal ? <IconAlert size={22} /> : <IconCheck size={22} />}</span>
        <h3>{refusal ? refusal.title : "Thank you — we've got it."}</h3>
        <p>
          {refusal
            ? refusal.body
            : "Your requirements are saved, and one of us will message you on WhatsApp shortly with the properties that actually fit. No call centre — just a person."}
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

      {/* Said BEFORE they retype everything, never after — the same
          courtesy has_active_assignment above gets, and for the same
          reason. Only ever shown to someone who is UPDATING: a first-time
          visitor has their full allowance and no reason to be told there is
          one. Zero left is the firmer wording, because at that point the
          form will refuse and the way forward is a person. */}
      {!isNewClient && updatesRemaining !== null && updatesRemaining <= 1 && (
        <p className="form-note">
          <IconAlert size={16} />
          {updatesRemaining === 0
            ? "You've already updated your requirements a few times, so this form can't change them again. Do send us a message on WhatsApp or give us a call — we'll happily update them for you."
            : "Just so you know, this is the last update we can take through this form. After it, a quick message or call is all it takes and we'll update things for you ourselves."}
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
        {/* One slot, always the same width and the same place: Confirm
            while the number is open for typing, "Change number" once it is
            settled. A confirmed number used to leave that slot empty, which
            made the number look permanent when it isn't — the way back was
            simply missing. A link-supplied number is the one that really
            is permanent, so it gets no button at all. */}
        <div
          className={
            phoneLocked
              ? phoneFromToken
                ? "field__row"
                : "field__row field__row--action field__row--change"
              : "field__row field__row--action"
          }
        >
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
          {phoneLocked
            ? !phoneFromToken && (
                <button
                  type="button"
                  className="btn btn--ghost field__action field__action--change"
                  onClick={beginEditPhone}
                >
                  <IconEdit size={15} />
                  Change number
                </button>
              )
            : // Turns gold — the site's one "go" colour — the instant the
              // number looks complete, so it's obviously the next thing to
              // press without needing to be told. Pulses on top of that when
              // the visitor tried to move on without pressing it.
              (
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
        ) : phoneError ? (
          <span className="field__error" id={`${idPrefix}-phone-error`}>
            {phoneError}
          </span>
        ) : (
          // Said only while the field is open after a confirmation, and it
          // is the answer to the question that opening it raises: "will
          // this cost me another code?" Not for anyone typing a number for
          // the first time — they have nothing to put back.
          editingPhone && (
            <span className="field__hint">
              {phoneEstablished
                ? "This is still your confirmed number — tap Confirm to keep it, no new code needed."
                : "Tap Confirm when you're done. If you go back to the number you already confirmed, we won't send another code."}
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

        {/* Pills, not a dropdown: someone happy with a flat OR a bungalow
            should be able to say both. Every picked type is matched on its
            own, and the dashboard shows the matches one type at a time. */}
        <div className="field">
          <span className="field__label" id={`${idPrefix}-type-label`}>
            Property type
          </span>
          <div className="seg seg--chips" role="group" aria-labelledby={`${idPrefix}-type-label`}>
            {withStored(propertyTypes).map((type) => {
              const active = propertyTypes.includes(type);
              return (
                <button
                  key={type}
                  type="button"
                  className={`seg__btn${active ? " is-active" : ""}`}
                  onClick={() => toggleType(type)}
                  aria-pressed={active}
                >
                  {active && <IconCheck size={14} />}
                  {type}
                </button>
              );
            })}
          </div>
          <span className="field__hint">Pick every type you'd consider — as many as you like.</span>
        </div>

        {/* One optional box per picked type, with BOTH units offered beside
            it rather than one guessed from the type. A bare number is
            ambiguous — 200 sqft and 200 var are not the same property — and
            guessing it used to make a bungalow quoted in sq ft read as nine
            times its real size. So the unit is asked; a box with anything
            typed in it cannot be sent until one is tapped (sizeProblem).
            Leaving every box empty stays perfectly fine: a size is
            optional, and it only ever nudges the matching — it never rules
            a property out. */}
        {propertyTypes.length > 0 && (
          <div className="field">
            <span className="field__label">Preferred size (optional)</span>
            <div className="req-form__sizes">
              {propertyTypes.map((type) => {
                const inputId = sizeInputId(idPrefix, type);
                const text = propertySizes[type] ?? "";
                const unit = propertyUnits[type] ?? "";
                const needsUnit = Boolean(text.trim()) && !unit;
                return (
                  <div className="req-form__size" key={type}>
                    <label className="req-form__size-type" htmlFor={inputId}>
                      {type}
                    </label>
                    <div className="req-form__size-entry">
                      <input
                        id={inputId}
                        type="text"
                        autoComplete="off"
                        placeholder="e.g. 70, 70 - 80 or 70 to 80"
                        value={text}
                        onChange={(event) =>
                          setPropertySizes((previous) => ({ ...previous, [type]: event.target.value }))
                        }
                        aria-describedby={`${idPrefix}-size-hint`}
                        maxLength={60}
                      />
                      <div
                        className={`unit-caps${needsUnit ? " unit-caps--needed" : ""}`}
                        role="radiogroup"
                        aria-label={`Unit for the ${type} size`}
                      >
                        {AREA_UNITS.map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            role="radio"
                            aria-checked={unit === option.value}
                            className={`unit-caps__opt${unit === option.value ? " is-on" : ""}`}
                            onClick={() =>
                              setPropertyUnits((previous) => ({
                                ...previous,
                                // Tapping the chosen unit again unpicks it,
                                // which is the only way back to unanswered.
                                [type]: previous[type] === option.value ? "" : option.value,
                              }))
                            }
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            <span className="field__hint field__hint--key" id={`${idPrefix}-size-hint`}>
              {SIZE_HINT}
            </span>
          </div>
        )}

        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-bhk`}>
            BHK
          </label>
          <input
            id={`${idPrefix}-bhk`}
            type="text"
            placeholder="e.g. 3 BHK, 2 to 5 BHK, 3+ BHK"
            value={bhk}
            onChange={(event) => setBhk(event.target.value)}
            maxLength={40}
          />
          {/* Each of these maps to a shape the matcher actually parses (see
              normalization.py's parse_bhk_intent) — they are examples of
              real behaviour, not decoration: "2 to 5" takes everything in
              between, "3+" opens up everything larger, "exactly 3" closes
              it down again. */}
          <span className="field__hint">
            Write it however you think of it — “3 BHK”, “2 to 5 BHK”, “2 or 3 BHK”, “3+ BHK”, “exactly 3 BHK”, “1 RK”.
          </span>
        </div>

        {/* A dropdown rather than free text, because this value is compared
            directly against a property's own furnishing, which only ever
            holds these three words. "No preference" is the default and is a
            real answer: it is never scored, and a stated preference only
            nudges the order — see FURNISHING_OPTIONS. */}
        <div className="field">
          <label className="field__label" htmlFor={`${idPrefix}-furnishing`}>
            Furnishing (optional)
          </label>
          <select
            id={`${idPrefix}-furnishing`}
            value={furnishing}
            onChange={(event) => setFurnishing(event.target.value)}
          >
            <option value="">No preference</option>
            {FURNISHING_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <span className="field__hint">
            Only if it matters to you — we'll lean towards it, but you'll still see everything else that fits.
          </span>
        </div>

        <div className="field">
          <span className="field__label" id={`${idPrefix}-budget-label`}>
            Budget (₹)
          </span>
          {/* The key, before the boxes — it answers "how do I write this?"
              before anyone has to wonder. */}
          <div className="req-form__units" id={`${idPrefix}-budget-units`}>
            <span>
              <b>cr</b> crore
            </span>
            <span>
              <b>L</b> lakh
            </span>
            <span>
              <b>K</b> thousand
            </span>
          </div>
          <div className="req-form__pair">
            <input
              type="text"
              autoComplete="off"
              placeholder="Min — e.g. 80 L"
              aria-label="Minimum budget"
              aria-describedby={`${idPrefix}-budget-units ${idPrefix}-budget-note`}
              aria-invalid={Boolean(budgetTouched.min && minBudget.error)}
              value={budgetMin}
              onChange={(event) => setBudgetMin(event.target.value)}
              onBlur={() => setBudgetTouched((previous) => ({ ...previous, min: true }))}
              maxLength={20}
            />
            <input
              type="text"
              autoComplete="off"
              placeholder="Max — e.g. 1.2 cr"
              aria-label="Maximum budget"
              aria-describedby={`${idPrefix}-budget-units ${idPrefix}-budget-note`}
              aria-invalid={Boolean((budgetTouched.max && maxBudget.error) || budgetOrderError)}
              value={budgetMax}
              onChange={(event) => setBudgetMax(event.target.value)}
              onBlur={() => setBudgetTouched((previous) => ({ ...previous, max: true }))}
              maxLength={20}
            />
          </div>
          {budgetMessage ? (
            <span className="field__error" id={`${idPrefix}-budget-note`}>
              {budgetMessage}
            </span>
          ) : (
            <span className="field__hint" id={`${idPrefix}-budget-note`}>
              Write it the way you'd say it — 2.5 cr, 85 L, or 25 K a month to rent.
            </span>
          )}
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
