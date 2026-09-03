import { useEffect, useRef } from "react";

/**
 * The pointer: a single ring that eases toward the pointer, grows over
 * anything clickable, and gives a quick tactile shrink on press before
 * settling back. Previously a small solid dot plus a separately-lagging
 * ring — two moving parts reading as disconnected from each other rather
 * than as one cursor. One element only, now.
 *
 * Positioned by `transform` only, so the browser composites it on the GPU
 * without repainting anything underneath.
 */
export default function Cursor() {
  const ringRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Only for real pointing devices, and only where motion is welcome. On
    // touch or with reduced motion the native cursor is left alone and none
    // of this runs.
    if (!window.matchMedia("(pointer: fine)").matches) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const ring = ringRef.current;
    if (!ring) return;

    const root = document.documentElement;
    // Added from here, never in the stylesheet: if this component fails to
    // mount for any reason, the native cursor must still be there.
    root.classList.add("has-cursor");

    let targetX = window.innerWidth / 2;
    let targetY = window.innerHeight / 2;
    let ringX = targetX;
    let ringY = targetY;
    let frame = 0;
    let shown = false;
    let pressTimer = 0;

    const draw = () => {
      // A tighter follow than the old two-element version (0.2 -> 0.4) —
      // with only one shape on screen, any visible lag reads as the cursor
      // failing to keep up rather than as a deliberate trailing effect.
      ringX += (targetX - ringX) * 0.4;
      ringY += (targetY - ringY) * 0.4;
      ring.style.transform = `translate3d(${ringX}px, ${ringY}px, 0) translate(-50%, -50%)`;

      // Stop once it has caught up. A permanently running rAF costs a frame
      // of work forever, including while the page sits idle.
      if (Math.abs(targetX - ringX) > 0.15 || Math.abs(targetY - ringY) > 0.15) {
        frame = requestAnimationFrame(draw);
      } else {
        frame = 0;
      }
    };

    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(draw);
    };

    const onMove = (event: PointerEvent) => {
      targetX = event.clientX;
      targetY = event.clientY;
      if (!shown) {
        shown = true;
        root.classList.add("cursor-on");
      }
      const element = event.target as Element | null;
      const interactive = element?.closest?.(
        "a, button, input, textarea, select, label, [role='button'], .row, .pcard, .popover__opt, .cmdk__item",
      );
      ring.dataset.state = interactive ? "hover" : "idle";
      schedule();
    };

    // The shrink-on-press holds briefly even for a fast click/tap — a
    // press that un-shrinks the instant the button is released is too
    // quick to actually see, which defeats the point of the feedback.
    const onDown = () => {
      window.clearTimeout(pressTimer);
      ring.dataset.pressed = "true";
    };
    const onUp = () => {
      window.clearTimeout(pressTimer);
      pressTimer = window.setTimeout(() => {
        ring.dataset.pressed = "false";
      }, 160);
    };
    const onLeave = () => {
      shown = false;
      root.classList.remove("cursor-on");
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onDown, { passive: true });
    window.addEventListener("pointerup", onUp, { passive: true });
    document.addEventListener("mouseleave", onLeave);
    window.addEventListener("blur", onLeave);

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      document.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("blur", onLeave);
      if (frame) cancelAnimationFrame(frame);
      window.clearTimeout(pressTimer);
      root.classList.remove("has-cursor", "cursor-on");
    };
  }, []);

  return (
    <div ref={ringRef} className="cursor-ring" aria-hidden="true">
      <span />
    </div>
  );
}
