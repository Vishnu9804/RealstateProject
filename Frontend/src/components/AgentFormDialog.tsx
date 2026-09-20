import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { agentApi } from "../api/agentApi";
import type { AgentSummary } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { whatsappNumberError } from "../lib/fieldChecks";
import { useToast } from "./ui/Toast";
import { Button, Note } from "./ui/Primitives";
import { IconAlert, IconPlus, IconTag, IconX } from "./ui/Icons";

/**
 * The Agents page's Add/Edit agent dialog — name, WhatsApp number, and the
 * areas this agent covers (a tag list, same add/remove pattern as
 * SettingsPage.tsx's area-keywords editor). Every field here maps straight
 * to Backend/Controller/AgentManagementController/agent_controller.py's
 * AgentCreateRequest, used for both create (POST) and edit (PATCH).
 */
export default function AgentFormDialog({
  agent,
  onClose,
  onSaved,
}: {
  /** Omitted for Add; passed for Edit — its fields prefill the form. */
  agent?: AgentSummary;
  onClose: () => void;
  onSaved: (agent: AgentSummary) => void;
}) {
  const mode: "add" | "edit" = agent ? "edit" : "add";
  const toast = useToast();
  const [name, setName] = useState(agent?.name ?? "");
  const [phone, setPhone] = useState(agent?.phone ?? "");
  const [areas, setAreas] = useState<string[]>(agent?.coverage_areas ?? []);
  const [newArea, setNewArea] = useState("");
  const [saving, setSaving] = useState(false);
  const areaInputRef = useRef<HTMLInputElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const phoneInputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  // Why the last Save attempt didn't go through — the missing-field case
  // included. It used to be silent: Save was simply disabled while a
  // required field was blank, so pressing it did nothing, said nothing, and
  // read as a broken button. The "Add a client" dialog has always answered
  // this properly (ClientFormDialog's formError); this is the same answer.
  const [formError, setFormError] = useState<string | null>(null);
  // Which required boxes to outline. Cleared per field as it is filled in,
  // so the red goes away as the problem is fixed rather than on the next
  // Save.
  const [invalid, setInvalid] = useState<{ name: boolean; phone: boolean }>({ name: false, phone: false });

  useEffect(() => {
    if (formError) errorRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [formError]);

  function addArea() {
    const cleaned = newArea.trim();
    if (!cleaned) return;
    if (areas.some((a) => a.toLowerCase() === cleaned.toLowerCase())) {
      setNewArea("");
      return;
    }
    setAreas((prev) => [...prev, cleaned]);
    setNewArea("");
    areaInputRef.current?.focus();
  }

  function removeArea(area: string) {
    setAreas((prev) => prev.filter((a) => a !== area));
  }

  /**
   * Save is always pressable (except while a save is in flight) — a disabled
   * button is the one control that cannot explain itself. Whatever is
   * missing is named here instead, in the order the fields are read.
   */
  async function handleSave() {
    if (saving) return;
    const trimmedName = name.trim();
    const trimmedPhone = phone.trim();
    const missingName = trimmedName.length === 0;
    const missingPhone = trimmedPhone.length === 0;
    if (missingName || missingPhone) {
      setInvalid({ name: missingName, phone: missingPhone });
      setFormError(
        missingName && missingPhone
          ? "Enter the agent's name and WhatsApp number — both are needed before they can be sent a site visit."
          : missingName
            ? "Enter the agent's name — it's what every client and visit is listed under."
            : "Enter the agent's WhatsApp number — it's where every site-visit hand-off is sent.",
      );
      (missingName ? nameInputRef : phoneInputRef).current?.focus();
      return;
    }

    // Present, but is it a number? The Inquiries page's client dialog has
    // always answered this (through the backend, in these exact words) and
    // this one never did: "abc" saved happily and the agent card then read
    // "abc" — an agent nothing can ever be sent to. The DUPLICATE check is
    // deliberately not attempted here: only the backend can see the other
    // agents, and it answers with a 409 that lands in formError below.
    const badPhone = whatsappNumberError(trimmedPhone);
    if (badPhone) {
      setInvalid({ name: false, phone: true });
      setFormError(badPhone);
      phoneInputRef.current?.focus();
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const body = { name: trimmedName, phone: trimmedPhone, coverage_areas: areas };
      if (mode === "edit" && agent) {
        const updated = await agentApi.updateAgent(agent.agent_id, body);
        toast.push({ tone: "ok", title: "Agent updated", message: updated.name });
        onSaved({ ...agent, ...updated });
      } else {
        const created = await agentApi.createAgent(body);
        toast.push({ tone: "ok", title: "Agent added", message: created.name });
        onSaved({ ...created, active_clients: [], completed_visits: [], visits_this_month: 0 });
      }
    } catch (err) {
      // Also kept in the dialog, not only in a toast: the toast fades while
      // this modal is still open, and the backend's own wording ("already
      // exists", "not a valid number") is the explanation the user needs
      // while they are looking at the field it is about.
      const message = friendlyError(err);
      setFormError(message);
      toast.push({ tone: "bad", title: mode === "edit" ? "Couldn't update this agent" : "Couldn't add this agent", message });
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label={mode === "edit" ? "Edit agent" : "Add an agent"}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Field team</div>
            <h2 className="detail-modal__title">{mode === "edit" ? "Edit agent" : "Add an agent"}</h2>
            <div className="detail-modal__sub">
              {mode === "edit" ? "Update their number or coverage areas." : "Every site visit needs someone to run it — add them here."}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="stack stack-4">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
              <div className="field">
                <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }} htmlFor="agent-name">
                  Name <span style={{ color: "var(--bad)" }}>*</span>
                </label>
                <input
                  id="agent-name"
                  ref={nameInputRef}
                  className={`input${invalid.name ? " input--bad" : ""}`}
                  aria-invalid={invalid.name || undefined}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (invalid.name && e.target.value.trim()) setInvalid((prev) => ({ ...prev, name: false }));
                  }}
                  placeholder="e.g. Ravi Patel"
                  autoFocus
                />
              </div>
              <div className="field">
                <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }} htmlFor="agent-phone">
                  WhatsApp number <span style={{ color: "var(--bad)" }}>*</span>
                </label>
                <input
                  id="agent-phone"
                  ref={phoneInputRef}
                  className={`input${invalid.phone ? " input--bad" : ""}`}
                  aria-invalid={invalid.phone || undefined}
                  value={phone}
                  onChange={(e) => {
                    setPhone(e.target.value);
                    if (invalid.phone && e.target.value.trim()) setInvalid((prev) => ({ ...prev, phone: false }));
                  }}
                  placeholder="90034 55667"
                />
              </div>
            </div>

            <div className="field">
              <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
                Areas they cover
              </label>

              {areas.length > 0 && (
                <div className="row-flex" style={{ gap: 9, marginBottom: 4 }}>
                  {areas.map((area) => (
                    <span key={area} className="chip">
                      <IconTag size={12} />
                      {area}
                      <button type="button" className="chip__x" onClick={() => removeArea(area)} aria-label={`Remove ${area}`}>
                        <IconX size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="row-flex" style={{ alignItems: "flex-start" }}>
                <input
                  ref={areaInputRef}
                  className="input"
                  style={{ flex: "1 1 220px" }}
                  value={newArea}
                  onChange={(e) => setNewArea(e.target.value)}
                  placeholder="Add an area, e.g. Vesu"
                  aria-label="New coverage area"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addArea();
                    }
                  }}
                />
                <Button icon={<IconPlus size={15} />} variant="ghost" onClick={addArea} disabled={!newArea.trim()}>
                  Add
                </Button>
              </div>
              <span className="field__hint">Optional — used to flag "Covers this area" when assigning a client.</span>
            </div>

            {formError && (
              <div ref={errorRef}>
                <Note tone="bad" icon={<IconAlert size={16} />}>
                  {formError}
                </Note>
              </div>
            )}
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving}>
              {mode === "edit" ? "Save changes" : "Add agent"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
