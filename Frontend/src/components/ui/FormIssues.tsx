import { useEffect } from "react";
import { IconAlert } from "./Icons";

/**
 * Everything a dialog refuses to save over, shown ALL AT ONCE and pinned
 * where the Save button is.
 *
 * WHY ALL AT ONCE
 *
 * Every form here used to answer with the FIRST problem it found and stop.
 * Fixing that one and pressing Save then produced the second, and so on:
 * a form with three mistakes in it took three round trips to discover,
 * each one framed as a fresh refusal. One pass, one list, one fix — that
 * is the only version where the reader can see how much work is left.
 *
 * WHY IT IS NOT INSIDE THE SCROLLING BODY
 *
 * It used to be, with a scrollIntoView on it, so pressing Save on a long
 * form jumped to the message and away from the field the message was
 * about. This component is rendered BETWEEN a dialog's `.detail-modal__body`
 * and its `.detail-modal__foot`, so it is always on screen without moving
 * anything. `focusFirstIssue` below is what moves the body — to the first
 * offending field, which is where the reader actually has to go.
 */

/** One refusal, and which box it belongs to. */
export interface FieldIssue {
  /** The `data-field` of the box this is about, which is what
   *  focusFirstIssue looks for and what marks that box red. Empty for a
   *  problem that belongs to the form as a whole ("fill in at least one
   *  of…"), which has no single box to point at. */
  field: string;
  message: string;
}

/** Whether `field` is one of the boxes `issues` names — what a Field passes
 *  to its own `invalid`. */
export function hasIssue(issues: FieldIssue[], field: string): boolean {
  return issues.some((issue) => issue.field === field);
}

/**
 * The bar itself. Renders nothing when there is nothing wrong, so a caller
 * can drop it in unconditionally.
 *
 * `error` is the server's own answer to the last save (a 409, a "locked"
 * message) and always leads, because it is about the attempt rather than
 * about a box; `issues` are what this browser refused to send in the first
 * place. They never appear together — a save either fails here or reaches
 * the API — but the component handles both rather than making four dialogs
 * remember that.
 */
export function FormIssues({ issues, error }: { issues: FieldIssue[]; error?: string | null }) {
  // De-duplicated: three empty phone boxes with the same fault are one
  // thing to fix, not three identical lines.
  const messages = [...new Set([...(error ? [error] : []), ...issues.map((issue) => issue.message)])];
  if (messages.length === 0) return null;
  return (
    // role="alert" so a screen reader announces it the moment it appears —
    // it is the answer to a button press the reader cannot see the result of.
    <div className="form-issues" role="alert">
      <IconAlert size={16} />
      {messages.length === 1 ? (
        <span>{messages[0]}</span>
      ) : (
        <div>
          <span className="form-issues__count">{messages.length} things to fix:</span>
          <ul className="form-issues__list">
            {messages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Scrolls the first box with a problem into view and puts the cursor in it.
 *
 * `root` is the dialog's scrolling body. Matching is by `data-field`, which
 * every Field sets from the same key its issues are raised under — so a
 * field can never be marked red and then not be the one scrolled to.
 *
 * Silent when there is nothing to find: an issue with no field (a
 * whole-form rule) or a field that is not currently rendered (a size box
 * for a property type that has just been un-ticked) leaves the view where
 * it is rather than jumping somewhere arbitrary. The bar above still says
 * what is wrong.
 */
/**
 * Runs focusFirstIssue whenever `issues` changes to a non-empty list.
 *
 * A dialog used to call focusFirstIssue directly, inline, right after
 * setIssues(found) in the same handleSave — and that was the bug. setIssues
 * only SCHEDULES the re-render that inserts the warning bar; the DOM at the
 * moment focusFirstIssue actually ran still had the OLD layout, without the
 * bar. scrollIntoView measured against that old, taller body, and the offset
 * it picked was then invalidated the instant React shrank the body to make
 * room for the bar — so the page landed away from the field it was
 * supposedly centering (a client dialog's WhatsApp number box, well above
 * the fold, left the reader looking at the bottom of the form with the
 * warning bar but no field in sight).
 *
 * A `useEffect` keyed on `issues` runs after React has committed that DOM
 * change, so it measures the real, final layout. Every dialog now calls
 * this once instead of calling focusFirstIssue itself. */
export function useFocusFirstIssue(root: React.RefObject<HTMLElement | null>, issues: FieldIssue[]): void {
  useEffect(() => {
    if (issues.length > 0) focusFirstIssue(root.current, issues);
    // Only `issues` — root is a ref and reading root.current here would not
    // belong in the dependency list even if included.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issues]);
}

export function focusFirstIssue(root: HTMLElement | null, issues: FieldIssue[]): void {
  const field = issues.find((issue) => issue.field)?.field;
  if (!root || !field) return;
  const box = root.querySelector<HTMLElement>(`[data-field="${CSS.escape(field)}"]`);
  if (!box) return;
  box.scrollIntoView({ block: "center", behavior: "smooth" });
  // The marked element is usually a field WRAPPER holding one control, but
  // where a field is several controls (the two budget boxes, one size box
  // per picked type) the mark is on the control itself — so try it before
  // looking inside it.
  const control = box.matches("input, textarea, select")
    ? box
    : box.querySelector<HTMLElement>("input, textarea, select");
  // preventScroll because scrollIntoView above has already chosen where
  // this lands; letting focus() scroll as well fights it and ends up with
  // the field jammed against the top edge.
  control?.focus({ preventScroll: true });
}
