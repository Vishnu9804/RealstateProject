import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/**
 * Fades and lifts its children in the first time they scroll into view.
 *
 * An IntersectionObserver that disconnects after firing, rather than a CSS
 * scroll-timeline: it works everywhere, and "once" is the important part —
 * an element that re-animates every time it re-enters the viewport turns
 * scrolling back up a long page into a strobe.
 *
 * `delay` staggers siblings (a grid of cards reading in sequence looks
 * composed; twelve cards arriving on the same frame looks like a glitch).
 */
export default function Reveal({
  children,
  delay = 0,
  className = "",
  style,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
  style?: CSSProperties;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    // Anything already on screen at mount (the top of the page) is revealed
    // immediately — waiting for a scroll that may never come would leave
    // the first screenful invisible.
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            observer.disconnect();
          }
        }
      },
      // A negative bottom margin holds the reveal until the element is
      // genuinely in the reader's field of view, not just clipping the very
      // bottom edge of the window.
      { threshold: 0.06, rootMargin: "0px 0px -8% 0px" },
    );

    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`reveal${shown ? " is-in" : ""}${className ? ` ${className}` : ""}`}
      style={{ ...style, ["--reveal-delay" as string]: `${delay}ms` }}
    >
      {children}
    </div>
  );
}
