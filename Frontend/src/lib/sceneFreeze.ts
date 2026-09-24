/**
 * Freezes the decorative background scene while a near-fullscreen dialog is
 * open, and thaws it again when the last one closes.
 *
 * Why this exists: `components/Scene.tsx` paints three hugely blurred aurora
 * blobs that drift on infinite 34s/43s/51s animations. That is cheap on its
 * own, but it means the page's backdrop is *dirty on every frame* — and
 * anything that filters its backdrop (the topbar, dialog chrome, popovers)
 * must therefore re-run that filter every frame too. With a dialog covering
 * nearly the whole window, that is a permanent, full-screen GPU cost that
 * buys nothing, because the scene it is animating is entirely hidden behind
 * the dialog. Stopping the blobs makes the backdrop static, so the
 * compositor can reuse what it already has.
 *
 * Ref-counted rather than a plain set/unset: a matches dialog can have a
 * property dialog, a planner and a confirm on top of it, and the first one
 * to unmount must not thaw the scene while the others are still up. The
 * returned release function is idempotent, so React 18's development
 * double-invoke of effects cannot drive the count negative.
 */

const ATTRIBUTE = "data-modal-open";

let openCount = 0;

export function freezeScene(): () => void {
  openCount += 1;
  if (openCount === 1)
    document.documentElement.setAttribute(ATTRIBUTE, "");

  let released = false;
  return () => {
    if (released) return;
    released = true;
    openCount = Math.max(0, openCount - 1);
    if (openCount === 0)
      document.documentElement.removeAttribute(ATTRIBUTE);
  };
}
