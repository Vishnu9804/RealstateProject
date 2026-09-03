import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { IconX } from "./Icons";

/**
 * A centered, portal-rendered confirmation dialog — used for the two
 * destructive/structural property actions (Delete, Move to) that must never
 * fire from a single accidental click. Modeled on FilterPopover's portal +
 * escape/outside-click pattern, but centered with a backdrop scrim rather
 * than anchored to a trigger, since there is no single "column heading" to
 * anchor a delete confirmation to.
 */

interface ConfirmDialogProps {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "default";
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export default function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "default",
  busy = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, busy]);

  return createPortal(
    <div
      className="modal-scrim"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div className="modal anim-rise" role="alertdialog" aria-modal="true" aria-label={title}>
        <div className="modal__head">
          <span className="modal__title">{title}</span>
          <button type="button" className="toast__close" onClick={onClose} disabled={busy} aria-label="Close">
            <IconX size={13} />
          </button>
        </div>

        <div className="modal__body">{body}</div>

        <div className="modal__foot">
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`btn btn--sm ${tone === "danger" ? "btn--danger" : "btn--primary"}`}
            onClick={onConfirm}
            disabled={busy}
            aria-busy={busy || undefined}
          >
            {busy && <span className="spinner" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
