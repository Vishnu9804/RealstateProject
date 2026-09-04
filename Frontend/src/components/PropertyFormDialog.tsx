import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { propertyApi, type PropertyContentFields } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { useToast } from "./ui/Toast";
import { Button, Segmented } from "./ui/Primitives";
import { IconInstagram, IconX } from "./ui/Icons";

/**
 * The Properties page's Add/Edit dialog — the same field set either way
 * (everything the LLM structuring stage would otherwise fill in, plus
 * instagram_reel_url, which only a human ever sets). Every field is
 * optional: there is nothing here that blocks Save.
 *
 * Cancel and the header's X both discard the in-progress edit without
 * calling the backend — onClose is the only thing either one does, and
 * neither is wired to onSave.
 */

interface FormState {
  property_type: string;
  bhk: string;
  society_name: string;
  area_name: string;
  address: string;
  carpet_area_sqft: string;
  carpet_area_unit: string;
  price_text: string;
  price_amount_inr: string;
  price_per_unit_text: string;
  price_per_unit_amount_inr: string;
  listing_type: "Sale" | "Rent";
  contact_name: string;
  contact_phone: string;
  description: string;
  instagram_reel_url: string;
}

const BLANK_FORM: FormState = {
  property_type: "",
  bhk: "",
  society_name: "",
  area_name: "",
  address: "",
  carpet_area_sqft: "",
  carpet_area_unit: "",
  price_text: "",
  price_amount_inr: "",
  price_per_unit_text: "",
  price_per_unit_amount_inr: "",
  listing_type: "Sale",
  contact_name: "",
  contact_phone: "",
  description: "",
  instagram_reel_url: "",
};

function toFormState(property: PropertyRecord): FormState {
  return {
    property_type: property.property_type ?? "",
    bhk: property.bhk ?? "",
    society_name: property.society_name ?? "",
    area_name: property.area_name ?? "",
    address: property.address ?? "",
    carpet_area_sqft: property.carpet_area_sqft?.toString() ?? "",
    carpet_area_unit: property.carpet_area_unit ?? "",
    price_text: property.price_text ?? "",
    price_amount_inr: property.price_amount_inr?.toString() ?? "",
    price_per_unit_text: property.price_per_unit_text ?? "",
    price_per_unit_amount_inr: property.price_per_unit_amount_inr?.toString() ?? "",
    listing_type: property.listing_type,
    contact_name: property.contact_name ?? "",
    contact_phone: property.contact_phone ?? "",
    description: property.description ?? "",
    instagram_reel_url: property.instagram_reel_url ?? "",
  };
}

/** Blank strings become null, not "" — an empty field must actually clear
 *  the value server-side, not overwrite it with an empty string. */
function toPayload(form: FormState): PropertyContentFields {
  const text = (value: string) => (value.trim() ? value.trim() : null);
  const num = (value: string) => (value.trim() ? Number(value) : null);
  return {
    property_type: text(form.property_type),
    bhk: text(form.bhk),
    society_name: text(form.society_name),
    area_name: text(form.area_name),
    address: text(form.address),
    carpet_area_sqft: num(form.carpet_area_sqft),
    carpet_area_unit: text(form.carpet_area_unit),
    price_text: text(form.price_text),
    price_amount_inr: num(form.price_amount_inr),
    price_per_unit_text: text(form.price_per_unit_text),
    price_per_unit_amount_inr: num(form.price_per_unit_amount_inr),
    listing_type: form.listing_type,
    contact_name: text(form.contact_name),
    contact_phone: text(form.contact_phone),
    description: text(form.description),
    instagram_reel_url: text(form.instagram_reel_url),
  };
}

const GRID_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 14,
};

function Field({
  label,
  hint,
  span,
  children,
}: {
  label: string;
  hint?: string;
  span?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="field" style={span ? { gridColumn: "1 / -1" } : undefined}>
      <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
        {label}
      </label>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}

export default function PropertyFormDialog({
  mode,
  property,
  onClose,
  onSaved,
}: {
  mode: "add" | "edit";
  property?: PropertyRecord;
  onClose: () => void;
  onSaved: (property: PropertyRecord, mode: "add" | "edit") => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(property ? toFormState(property) : BLANK_FORM);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, saving]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const payload = toPayload(form);
      const saved =
        mode === "add" ? await propertyApi.createProperty(payload) : await propertyApi.updateProperty(property!.record_id, payload);
      toast.push({
        tone: "ok",
        title: mode === "add" ? "Property added" : "Property updated",
        message: saved.society_name ?? saved.area_name ?? "Saved.",
      });
      onSaved(saved, mode);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't save this property", message: friendlyError(err) });
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label={mode === "add" ? "Add property" : "Edit property"}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">{mode === "add" ? "New property" : "Edit property"}</div>
            <h2 className="detail-modal__title cell-truncate">
              {mode === "add" ? "Add a property" : (property?.society_name ?? property?.area_name ?? "Edit property")}
            </h2>
            <div className="detail-modal__sub">Every field here is optional — fill in only what you know.</div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="stack stack-4">
            <div style={GRID_STYLE}>
              <Field label="Society / building name">
                <input className="input" value={form.society_name} onChange={(e) => set("society_name", e.target.value)} placeholder="e.g. Black Residency" />
              </Field>
              <Field label="Area / locality">
                <input className="input" value={form.area_name} onChange={(e) => set("area_name", e.target.value)} placeholder="e.g. Vesu" />
              </Field>
              <Field label="Address" span>
                <input className="input" value={form.address} onChange={(e) => set("address", e.target.value)} placeholder="Street, road or landmark" />
              </Field>
              <Field label="Property type">
                <input className="input" value={form.property_type} onChange={(e) => set("property_type", e.target.value)} placeholder="e.g. Flat, Shop, Land/Plot" />
              </Field>
              <Field label="BHK">
                <input className="input" value={form.bhk} onChange={(e) => set("bhk", e.target.value)} placeholder="e.g. 2 BHK" />
              </Field>
              <Field label="Sale or Rent">
                <Segmented
                  ariaLabel="Sale or Rent"
                  value={form.listing_type}
                  onChange={(value) => set("listing_type", value)}
                  options={[
                    { value: "Sale", label: "Sale" },
                    { value: "Rent", label: "Rent" },
                  ]}
                />
              </Field>

              <Field label="Carpet area">
                <input className="input" type="number" inputMode="decimal" value={form.carpet_area_sqft} onChange={(e) => set("carpet_area_sqft", e.target.value)} placeholder="e.g. 1200" />
              </Field>
              <Field label="Area unit">
                <select className="select" value={form.carpet_area_unit} onChange={(e) => set("carpet_area_unit", e.target.value)}>
                  <option value="">—</option>
                  <option value="sqft">sqft</option>
                  <option value="vaar">vaar</option>
                  <option value="vigha">vigha</option>
                </select>
              </Field>

              <Field label="Price (as written)" hint="e.g. 45L, 1.25cr, 15k/month">
                <input className="input" value={form.price_text} onChange={(e) => set("price_text", e.target.value)} placeholder="e.g. 45L" />
              </Field>
              <Field label="Price (₹ amount)">
                <input className="input" type="number" inputMode="decimal" value={form.price_amount_inr} onChange={(e) => set("price_amount_inr", e.target.value)} placeholder="e.g. 4500000" />
              </Field>
              <Field label="Price per unit (as written)" hint="e.g. 1L/sq ft — only if a rate was quoted">
                <input className="input" value={form.price_per_unit_text} onChange={(e) => set("price_per_unit_text", e.target.value)} placeholder="e.g. 1L/sq ft" />
              </Field>
              <Field label="Price per unit (₹ amount)">
                <input className="input" type="number" inputMode="decimal" value={form.price_per_unit_amount_inr} onChange={(e) => set("price_per_unit_amount_inr", e.target.value)} placeholder="e.g. 100000" />
              </Field>

              <Field label="Contact name">
                <input className="input" value={form.contact_name} onChange={(e) => set("contact_name", e.target.value)} placeholder="e.g. Ramesh Broker" />
              </Field>
              <Field label="Contact phone">
                <input className="input" value={form.contact_phone} onChange={(e) => set("contact_phone", e.target.value)} placeholder="Digits, with country code" />
              </Field>

              <Field
                label="Instagram reel link"
                hint="If this property was posted as an Instagram reel, paste its link — comments and shares of that reel get matched back to this property."
                span
              >
                <div className="input-wrap">
                  <span className="input-wrap__icon">
                    <IconInstagram size={16} />
                  </span>
                  <input
                    className="input"
                    style={{ paddingLeft: 40 }}
                    value={form.instagram_reel_url}
                    onChange={(e) => set("instagram_reel_url", e.target.value)}
                    placeholder="https://www.instagram.com/reel/…"
                  />
                </div>
              </Field>
            </div>

            <Field label="Description">
              <textarea className="textarea" rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Any other details worth noting" />
            </Field>
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving}>
              {mode === "add" ? "Add property" : "Save changes"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
