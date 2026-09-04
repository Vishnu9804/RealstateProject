import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import "../styles/inquiryForm.css";
import { ApiError } from "../api/client";
import { inquiryFormApi } from "../api/inquiryFormApi";
import type { InquiryChannel, InquiryFormPrefill, InquiryFormSubmission } from "../api/types";
import { friendlyError } from "../lib/apiError";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import { Badge, Button, EmptyState, Note, Panel, Segmented, SkeletonRows } from "../components/ui/Primitives";
import { IconAlert, IconBuilding, IconCheckCircle, IconRefresh } from "../components/ui/Icons";

const PROPERTY_TYPES = ["Flat", "Row House", "Bungalow", "Shop", "Office", "Land/Plot", "Warehouse", "Other"];

type PurposeValue = "buy" | "rent" | "sell" | "";
type LoadState = "loading" | "invalid" | "ready" | "error";

/**
 * The public form the WhatsApp welcome/update messages link to — see
 * Backend/Service/WhatsAppInquiryHandlingService/inquiry_pipeline_service.py
 * (which builds the link) and Backend/Controller/WhatsAppInquiryHandlingController/
 * inquiry_form_controller.py (which serves it). Deliberately rendered
 * outside <Layout> (see App.tsx): a prospective client opening this from
 * WhatsApp has no reason to see the internal ops nav/branding, and no
 * reason for their browser to poll the internal whatsapp-status endpoint
 * that page normally drives.
 */
export default function InquiryFormPage() {
  const { token = "" } = useParams<{ token: string }>();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isNewClient, setIsNewClient] = useState(true);
  const [saved, setSaved] = useState<InquiryFormPrefill | null>(null);
  const [channel, setChannel] = useState<InquiryChannel>("whatsapp");

  const [phone, setPhone] = useState("");
  const phoneInputRef = useRef<HTMLInputElement>(null);
  // Only ever relevant on an "instagram" link with no phone entered — set
  // once the visitor explicitly says "No, Continue on Instagram" in the
  // nudge dialog below, so the very next Save actually goes through
  // instead of asking again.
  const [skipPhoneNudge, setSkipPhoneNudge] = useState(false);
  const [showPhoneNudge, setShowPhoneNudge] = useState(false);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [purpose, setPurpose] = useState<PurposeValue>("");
  const [propertyType, setPropertyType] = useState("");
  const [bhk, setBhk] = useState("");
  const [budgetMin, setBudgetMin] = useState("");
  const [budgetMax, setBudgetMax] = useState("");
  const [preferredAreas, setPreferredAreas] = useState("");
  const [additionalRequirements, setAdditionalRequirements] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    inquiryFormApi
      .getPrefill(token)
      .then((data) => {
        if (cancelled) return;
        applyPrefill(data);
        setLoadState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setLoadState("invalid");
        } else {
          setLoadError(friendlyError(err));
          setLoadState("error");
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function applyPrefill(data: InquiryFormPrefill) {
    setSaved(data);
    setIsNewClient(data.is_new_client);
    setChannel(data.channel);
    setPhone(data.phone ?? "");
    setName(data.name ?? "");
    setEmail(data.email ?? "");
    setPurpose((data.purpose as PurposeValue) || "");
    setPropertyType(data.property_type ?? "");
    setBhk(data.bhk ?? "");
    setBudgetMin(data.budget_min_inr != null ? String(data.budget_min_inr) : "");
    setBudgetMax(data.budget_max_inr != null ? String(data.budget_max_inr) : "");
    setPreferredAreas(data.preferred_areas ?? "");
    setAdditionalRequirements(data.additional_requirements ?? "");
  }

  // Undoes local edits back to what the server last had — not a full-page
  // reload, so it also works after the page has been sitting open a while.
  function resetToSaved() {
    if (saved) applyPrefill(saved);
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    // Validated on submit, not by silently disabling the button beforehand
    // — a disabled button with no explanation looks broken/unresponsive
    // (this is what was reported: the button "not available" with no clue
    // why), whereas a submit attempt that fails tells the visitor exactly
    // what's missing.
    if (!name.trim()) {
      setSubmitError("Please enter your name.");
      return;
    }
    if (!purpose) {
      setSubmitError("Please select whether you want to Buy, Rent, or Sell.");
      return;
    }

    // Only an Instagram-originated link ever has an empty, still-editable
    // phone field — a WhatsApp one is always pre-filled and locked, so it
    // can never hit this. Ask once per visit; "No, Continue on Instagram"
    // sets skipPhoneNudge so this same Save then goes straight through.
    if (channel === "instagram" && !phone.trim() && !skipPhoneNudge) {
      setShowPhoneNudge(true);
      return;
    }

    void doSubmit();
  }

  async function doSubmit() {
    setSubmitting(true);
    setSubmitError(null);

    // Blank text fields are sent as null, not "" — that's what tells the
    // backend a field was deliberately cleared (see
    // inquiry_form_service.py's _blank_to_none), not left untouched.
    const body: InquiryFormSubmission = {
      // Only ever meaningful for an "instagram" token — a "whatsapp" one's
      // identity is fixed by the link itself, so sending its (locked,
      // read-only) value here would be redundant at best; omitting it
      // entirely keeps that invariant visible in the request itself, not
      // just enforced silently server-side.
      phone: channel === "instagram" ? phone.trim() || null : undefined,
      name: name.trim(),
      email: email.trim() || null,
      purpose: purpose || null,
      property_type: propertyType || null,
      bhk: bhk.trim() || null,
      budget_min_inr: budgetMin ? Number(budgetMin) : null,
      budget_max_inr: budgetMax ? Number(budgetMax) : null,
      preferred_areas: preferredAreas.trim() || null,
      additional_requirements: additionalRequirements.trim() || null,
    };

    try {
      await inquiryFormApi.submit(token, body);
      setSubmitted(true);
    } catch (err) {
      setSubmitError(friendlyError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="inquiry-page">
      <div className="inquiry-card">
        <div className="inquiry-brand">
          <span className="inquiry-brand__mark">
            <IconBuilding size={19} />
          </span>
          <span>
            <div className="inquiry-brand__name">Manibhadra Real Estate</div>
            <div className="inquiry-brand__sub">Property requirements form</div>
          </span>
        </div>

        {loadState === "loading" && (
          <Panel className="stack stack-4">
            <SkeletonRows rows={6} />
          </Panel>
        )}

        {loadState === "invalid" && (
          <Panel>
            <EmptyState
              icon={<IconAlert size={34} />}
              title="This link is no longer valid"
              body="It may have expired. Please message us on WhatsApp again and we'll send you a fresh link."
            />
          </Panel>
        )}

        {loadState === "error" && (
          <Panel>
            <Note tone="bad" icon={<IconAlert size={16} />}>
              {loadError ?? "Something went wrong loading this form."}
            </Note>
          </Panel>
        )}

        {loadState === "ready" && submitted && (
          <Panel className="inquiry-center stack stack-3">
            <IconCheckCircle size={40} />
            <h2 style={{ margin: 0 }}>Thank you!</h2>
            <p className="section-head__sub">
              We've received your requirements. Our agent will contact you soon on WhatsApp.
            </p>
          </Panel>
        )}

        {loadState === "ready" && !submitted && (
          <Panel className="stack stack-5">
            <div>
              {!isNewClient && (
                <Badge tone="info" title="We already have requirements on file for you">
                  Updating your requirements
                </Badge>
              )}
              <h1 className="page-title" style={{ marginTop: isNewClient ? 0 : 8 }}>
                {isNewClient ? "Tell us what you're looking for" : "Update your requirements"}
              </h1>
              <p className="section-head__sub">
                {isNewClient
                  ? "A couple of details help us match you with the right property faster."
                  : "Change anything below, or clear a field you'd rather leave blank, then save."}
              </p>
            </div>

            <form className="stack stack-4" onSubmit={handleSubmit}>
              <div className="field">
                <label className="field__label" htmlFor="phone">
                  WhatsApp number{channel === "instagram" && " (optional)"}
                </label>
                <input
                  id="phone"
                  ref={phoneInputRef}
                  type="tel"
                  className="input"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="e.g. 9876543210"
                  disabled={channel === "whatsapp"}
                  style={channel === "whatsapp" ? { opacity: 0.7, cursor: "not-allowed" } : undefined}
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor="name">
                  Your name
                </label>
                <input
                  id="name"
                  type="text"
                  className="input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Rahul Sharma"
                  required
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor="email">
                  Email (optional)
                </label>
                <input
                  id="email"
                  type="email"
                  className="input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>

              <div className="field">
                <span className="field__label">I want to (required)</span>
                <Segmented<PurposeValue>
                  ariaLabel="Purpose"
                  value={purpose}
                  onChange={setPurpose}
                  options={[
                    { value: "buy", label: "Buy" },
                    { value: "rent", label: "Rent" },
                    { value: "sell", label: "Sell" },
                  ]}
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor="property-type">
                  Property type
                </label>
                <select
                  id="property-type"
                  className="select"
                  value={propertyType}
                  onChange={(e) => setPropertyType(e.target.value)}
                >
                  <option value="">Select…</option>
                  {PROPERTY_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label className="field__label" htmlFor="bhk">
                  BHK / configuration
                </label>
                <input
                  id="bhk"
                  type="text"
                  className="input"
                  value={bhk}
                  onChange={(e) => setBhk(e.target.value)}
                  placeholder="e.g. 2 BHK"
                />
              </div>

              <div className="field">
                <span className="field__label">Budget (₹)</span>
                <div className="inquiry-budget-row">
                  <input
                    type="number"
                    inputMode="numeric"
                    min={0}
                    className="input"
                    value={budgetMin}
                    onChange={(e) => setBudgetMin(e.target.value)}
                    placeholder="Min"
                    aria-label="Minimum budget"
                  />
                  <input
                    type="number"
                    inputMode="numeric"
                    min={0}
                    className="input"
                    value={budgetMax}
                    onChange={(e) => setBudgetMax(e.target.value)}
                    placeholder="Max"
                    aria-label="Maximum budget"
                  />
                </div>
              </div>

              <div className="field">
                <label className="field__label" htmlFor="areas">
                  Preferred areas
                </label>
                <input
                  id="areas"
                  type="text"
                  className="input"
                  value={preferredAreas}
                  onChange={(e) => setPreferredAreas(e.target.value)}
                  placeholder="e.g. Althan, Vesu"
                />
              </div>

              <div className="field">
                <label className="field__label" htmlFor="notes">
                  Anything else? (optional)
                </label>
                <textarea
                  id="notes"
                  className="textarea"
                  value={additionalRequirements}
                  onChange={(e) => setAdditionalRequirements(e.target.value)}
                  placeholder="Any other requirements…"
                />
              </div>

              {submitError && (
                <Note tone="bad" icon={<IconAlert size={16} />}>
                  {submitError}
                </Note>
              )}

              <div className="row-flex">
                <Button type="submit" variant="primary" busy={submitting} disabled={submitting}>
                  {submitting ? "Submitting…" : isNewClient ? "Submit requirements" : "Save changes"}
                </Button>
                {!isNewClient && (
                  <Button
                    type="button"
                    variant="ghost"
                    icon={<IconRefresh size={15} />}
                    onClick={resetToSaved}
                    disabled={submitting}
                  >
                    Reset
                  </Button>
                )}
              </div>
            </form>
          </Panel>
        )}
      </div>

      {showPhoneNudge && (
        <ConfirmDialog
          title="Add your WhatsApp number?"
          body={<p>Sharing your WhatsApp number makes it much easier for us to send you property details and stay in touch.</p>}
          confirmLabel="No, Continue on Instagram"
          cancelLabel="Okay"
          onConfirm={() => {
            setShowPhoneNudge(false);
            setSkipPhoneNudge(true);
            void doSubmit();
          }}
          onClose={() => {
            // Covers the Okay button as well as the dialog's X/outside-
            // click/Escape — all of them mean the same thing here: don't
            // submit yet, let them come back and add the number.
            setShowPhoneNudge(false);
            phoneInputRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}
