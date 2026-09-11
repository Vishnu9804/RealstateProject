import { useEffect } from "react";
import { IconCheck } from "./Icons";

/**
 * The site's one transient notification.
 *
 * App.tsx's docstring says this site has no toasts, and for almost
 * everything it still doesn't: a message about the thing you are looking at
 * belongs next to it, not in a corner. This is the single case where that
 * rule genuinely breaks down — submitting the requirements form now MOVES
 * the visitor, up to the properties, and the acknowledgement has to travel
 * with them. An inline panel left behind at the bottom of the page would be
 * an acknowledgement nobody ever sees, which is exactly the "did that
 * work?" doubt this is here to remove.
 *
 * Top-centred, under the fixed header, for two reasons: it is where the eye
 * already is after a scroll begins, and it is the one edge of the screen
 * with nothing else on it (the floating enquire button owns the bottom
 * right, and a bottom-centred toast collides with it on a phone).
 *
 * Dismisses itself, and can also be dismissed — it is never modal, never
 * blocks a click, and never waits to be acknowledged.
 */
export default function Toast({
  message,
  onDismiss,
  durationMs = 5200,
}: {
  /** Rendered when non-null; null renders nothing at all. */
  message: string | null;
  onDismiss: () => void;
  durationMs?: number;
}) {
  useEffect(() => {
    if (!message) return;
    // Keyed on the message itself, so a second notification while one is up
    // restarts the clock rather than inheriting the first one's remaining
    // time and vanishing a moment after it appeared.
    const timer = window.setTimeout(onDismiss, durationMs);
    return () => window.clearTimeout(timer);
  }, [message, durationMs, onDismiss]);

  if (!message) return null;

  return (
    // "status", not "alert": this is a confirmation of something that went
    // right, so it is announced politely rather than interrupting whatever
    // a screen reader is in the middle of saying.
    <div className="toast" role="status" aria-live="polite">
      <span className="toast__icon">
        <IconCheck size={15} />
      </span>
      <p className="toast__text">{message}</p>
      <button type="button" className="toast__close" onClick={onDismiss} aria-label="Dismiss">
        {/* A plain multiplication sign rather than an icon import: this is
            the smallest control on the site and an outlined glyph at this
            size reads as a smudge. */}
        <span aria-hidden>×</span>
      </button>
    </div>
  );
}
