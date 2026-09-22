import { useEffect, useRef, useState } from "react";

/**
 * Animates a stat's digits up from zero the first time it scrolls into
 * view — same "reveal once" IntersectionObserver pattern as Reveal.tsx,
 * just driving a number instead of an opacity/transform.
 *
 * Works on the raw label ("14+", "1,200+", "48 hrs") rather than a plain
 * number prop: it finds the leading run of digits/commas, animates only
 * that, and reattaches whatever came before and after untouched. So
 * siteConfig.ts stays exactly as editable as it was — nobody has to split
 * a stat into a number and a suffix to get the animation.
 */
export default function CountUp({ value }: { value: string }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [display, setDisplay] = useState(() => zeroed(value));

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const match = value.match(/[\d,]+/);
    if (!match || typeof match.index !== "number") {
      setDisplay(value);
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDisplay(value);
      return;
    }

    const target = Number(match[0].replace(/,/g, ""));
    const prefix = value.slice(0, match.index);
    const suffix = value.slice(match.index + match[0].length);
    const duration = 1100;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer.disconnect();
          const start = performance.now();
          const tick = (now: number) => {
            const progress = Math.min(1, (now - start) / duration);
            const eased = 1 - (1 - progress) ** 3;
            setDisplay(`${prefix}${Math.round(target * eased).toLocaleString("en-IN")}${suffix}`);
            if (progress < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }
      },
      { threshold: 0.4 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [value]);

  return <span ref={ref}>{display}</span>;
}

// The pre-animation frame: every digit zeroed but the surrounding
// characters (",", "+", " hrs") kept, so the layout doesn't jump once the
// real digits arrive.
function zeroed(value: string) {
  return value.replace(/[\d,]+/, (run) => run.replace(/\d/g, "0"));
}
