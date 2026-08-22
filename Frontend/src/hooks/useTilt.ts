import { useCallback, useRef } from "react";

/**
 * Pointer-driven 3D tilt for a surface.
 *
 * Writes CSS custom properties straight onto the node instead of going
 * through React state: this fires on every mousemove, and re-rendering a
 * component tree at pointer frequency is exactly how a "premium" animation
 * turns into a janky one. The style layer can absorb that rate; the
 * reconciler can't.
 *
 * Also positions the glare highlight so the specular hotspot tracks the
 * pointer — a card that tilts without its highlight moving looks like a
 * picture of a tilted card rather than a real surface catching light.
 */
export function useTilt(maxDeg = 7, lift = 16) {
  const ref = useRef<HTMLElement | null>(null);
  const frame = useRef(0);

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const node = ref.current;
      if (!node || event.pointerType === "touch") return;
      // Coalesce to one write per frame — pointermove can outpace paint.
      if (frame.current) cancelAnimationFrame(frame.current);
      const { clientX, clientY } = event;
      frame.current = requestAnimationFrame(() => {
        const rect = node.getBoundingClientRect();
        const px = (clientX - rect.left) / rect.width;
        const py = (clientY - rect.top) / rect.height;
        node.style.setProperty("--ry", `${(px - 0.5) * maxDeg * 2}deg`);
        node.style.setProperty("--rx", `${(0.5 - py) * maxDeg * 2}deg`);
        node.style.setProperty("--tz", `${lift}px`);
        node.style.setProperty("--gx", `${px * 100}%`);
        node.style.setProperty("--gy", `${py * 100}%`);
        node.style.setProperty("--glare", "1");
      });
    },
    [maxDeg, lift],
  );

  const onPointerLeave = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    if (frame.current) cancelAnimationFrame(frame.current);
    node.style.setProperty("--ry", "0deg");
    node.style.setProperty("--rx", "0deg");
    node.style.setProperty("--tz", "0px");
    node.style.setProperty("--glare", "0");
  }, []);

  return { ref, onPointerMove, onPointerLeave };
}
