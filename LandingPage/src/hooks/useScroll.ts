import { useEffect, useState } from "react";

/**
 * The three things the fixed header needs to know about the page's scroll
 * position, computed in ONE listener rather than three.
 *
 * They're bundled deliberately: each of them is cheap on its own, but a
 * scroll handler runs on every frame of every scroll, and three separate
 * hooks would mean three listeners, three rAF loops and three renders per
 * frame on what is meant to be the smoothest page in the project.
 *
 *  - `stuck`    — has the page moved at all (header goes from transparent
 *                 over the hero to solid glass).
 *  - `progress` — 0..1 through the whole document, for the reading bar.
 *  - `activeId` — which section the reader is currently in, for the nav.
 */
export function useScrollState(sectionIds: string[]) {
  const [stuck, setStuck] = useState(false);
  const [progress, setProgress] = useState(0);
  const [activeId, setActiveId] = useState(sectionIds[0] ?? "");

  useEffect(() => {
    let frame = 0;

    const measure = () => {
      frame = 0;
      const scrollY = window.scrollY;
      setStuck(scrollY > 12);

      const scrollable = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(scrollable > 0 ? Math.min(1, Math.max(0, scrollY / scrollable)) : 0);

      // "Active" is whichever section's top has most recently passed a line
      // a third of the way down the viewport — not whichever is merely
      // visible. With tall sections, several are on screen at once, and
      // picking by visibility makes the nav flicker between two links while
      // the reader sits still.
      const line = scrollY + window.innerHeight * 0.34;
      let current = sectionIds[0] ?? "";
      for (const id of sectionIds) {
        const element = document.getElementById(id);
        if (element && element.offsetTop <= line) current = id;
      }
      // The last section can be shorter than the space below that line, so
      // it would never win on its own. Hitting the bottom of the page means
      // you are unambiguously in it.
      if (scrollable > 0 && scrollY >= scrollable - 4) current = sectionIds[sectionIds.length - 1] ?? current;
      setActiveId(current);
    };

    const onScroll = () => {
      // Coalesced to one measurement per frame; scroll events can fire far
      // more often than that, and every extra run is layout work for a
      // value that cannot have changed twice within a frame.
      if (frame === 0) frame = window.requestAnimationFrame(measure);
    };

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [sectionIds]);

  return { stuck, progress, activeId };
}

/**
 * Scrolls a section into view. Lives here rather than in the header because
 * the hero buttons and the footer links scroll to sections too, and all
 * three must land in exactly the same place — the CSS `scroll-margin-top`
 * on `.section` is what keeps the heading clear of the fixed header.
 */
export function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}
