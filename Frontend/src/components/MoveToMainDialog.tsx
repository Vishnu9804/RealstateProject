import { useEffect, useState } from "react";
import { propertyApi } from "../api/propertyApi";
import { settingsApi } from "../api/settingsApi";
import type { PropertyRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import ConfirmDialog from "./ui/ConfirmDialog";
import { useToast } from "./ui/Toast";

/**
 * Outsider -> Main, asking which of the Settings areas this property really
 * belongs to. Picking an area sets it as the property's area, keeps the old
 * area in the address, and teaches the area knowledge base (backend:
 * POST /properties/{id}/move-to-main). "Other" is the plain move, exactly as
 * before — nothing about the property changes.
 */

const OTHER = "__other__";

export default function MoveToMainDialog({
  property,
  onClose,
  onMoved,
}: {
  property: PropertyRecord;
  onClose: () => void;
  onMoved: (updated: PropertyRecord) => void;
}) {
  const toast = useToast();
  const [areas, setAreas] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    settingsApi
      .getAreaKeywords()
      .then((settings) => {
        if (!cancelled) setAreas(settings.keywords);
      })
      .catch(() => {
        if (!cancelled) {
          setAreas([]);
          setLoadError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function confirm() {
    if (!choice || busy) return;
    setBusy(true);
    try {
      const updated =
        choice === OTHER
          ? await propertyApi.updateProperty(property.record_id, { review_status: "accepted" })
          : await propertyApi.moveToMainWithArea(property.record_id, choice);
      onMoved(updated);
      toast.push({
        tone: "ok",
        title: "Moved",
        message: choice === OTHER ? "Moved to Main." : `Moved to Main under ${updated.area_name ?? choice}.`,
      });
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't move property", message: friendlyError(err) });
      setBusy(false);
    }
  }

  const option = (value: string, label: string) => (
    <button
      key={value}
      type="button"
      className={`btn btn--sm${choice === value ? " btn--primary" : ""}`}
      aria-pressed={choice === value}
      disabled={busy}
      onClick={() => setChoice(value)}
    >
      {label}
    </button>
  );

  return (
    <ConfirmDialog
      title="Move to Main"
      body={
        <>
          <p>
            <strong>{property.society_name ?? property.area_name ?? "Unnamed property"}</strong>
          </p>
          <p style={{ marginTop: 8 }}>
            <span className="faint">Area:</span> {property.area_name?.trim() || "—"}
          </p>
          <p style={{ marginTop: 4, wordBreak: "break-word" }}>
            <span className="faint">Address:</span> {property.address?.trim() || "—"}
          </p>
          <p style={{ marginTop: 12 }}>Which of your areas does this belong to?</p>
          {areas === null ? (
            <div className="row-flex faint small" style={{ marginTop: 8 }}>
              <span className="spinner" /> Loading your areas…
            </div>
          ) : (
            <div
              style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8, maxHeight: 240, overflowY: "auto" }}
            >
              {areas.map((area) => option(area, area))}
              {option(OTHER, "Other")}
            </div>
          )}
          {loadError && (
            <p className="faint small" style={{ marginTop: 8 }}>
              Couldn't load your Settings areas — "Other" still moves it to Main unchanged.
            </p>
          )}
          <p className="faint small" style={{ marginTop: 10 }}>
            {choice === OTHER
              ? "Moves it to Main without changing anything."
              : choice
                ? `Area becomes ${choice}; the current area is kept in the address and remembered under ${choice}.`
                : "Pick an area, or Other to move it unchanged."}
          </p>
        </>
      }
      confirmLabel="Move"
      busy={busy}
      confirmDisabled={!choice}
      onConfirm={confirm}
      onClose={() => !busy && onClose()}
    />
  );
}
