import { useEffect, useRef } from "react";

/**
 * The pointer: an exact accent dot with a ring easing along behind it.
 *
 * This replaces the large radial "spotlight" gradient that used to follow
 * the mouse. That effect had two problems: a pale wash of colour dragging
 * across the page looks like a smudge rather than a cursor, and repainting a
 * 600px gradient over the whole viewport on every pointer move was one of
 * the main reasons the interface felt like it stuttered.
 *
 * Both elements move by `transform` only, so the browser can composite them
 * on the GPU without repainting anything underneath.
 */
export default function Cursor() {
  const dotRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Only for real pointing devices, and only where motion is welcome. On
    // touch or with reduced motion the native cursor is left alone and none
    // of this runs.
    if (!window.matchMedia("(pointer: fine)").matches) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const dot = dotRef.current;
    const ring = ringRef.current;
    if (!dot || !ring) return;

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

    const draw = () => {
      ringX += (targetX - ringX) * 0.2;
      ringY += (targetY - ringY) * 0.2;
      ring.style.transform = `translate3d(${ringX}px, ${ringY}px, 0) translate(-50%, -50%)`;
      dot.style.transform = `translate3d(${targetX}px, ${targetY}px, 0) translate(-50%, -50%)`;

      // Stop once the ring has caught up. A permanently running rAF costs a
      // frame of work forever, including while the page sits idle.
      if (Math.abs(targetX - ringX) > 0.2 || Math.abs(targetY - ringY) > 0.2) {
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
        "a, button, input, textarea, select, label, [role='button'], .row, .popover__opt, .cmdk__item",
      );
      ring.dataset.state = interactive ? "active" : "idle";
      schedule();
    };

    const onDown = () => (ring.dataset.pressed = "true");
    const onUp = () => (ring.dataset.pressed = "false");
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
      root.classList.remove("has-cursor", "cursor-on");
    };
  }, []);

  return (
    <>
      <div ref={ringRef} className="cursor-ring" aria-hidden="true">
        <span />
      </div>
      <div ref={dotRef} className="cursor-dot" aria-hidden="true" />
    </>
  );
}
