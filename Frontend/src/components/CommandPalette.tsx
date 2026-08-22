import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTheme } from "./ui/Theme";
import {
  IconArrowRight,
  IconGrid,
  IconLink,
  IconMoon,
  IconRefresh,
  IconSearch,
  IconSliders,
  IconSun,
} from "./ui/Icons";

/**
 * Ctrl/Cmd-K palette.
 *
 * Three screens is small enough that navigation is never *hard* — but this
 * app is something people keep open and dip into, and the palette means
 * every action is reachable without first working out where the mouse has
 * to go. It also gives the keyboard-only path a single entry point, which
 * the old header (three links and nothing else) simply did not have.
 */

export interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: React.ReactNode;
  run: () => void;
}

export function useCommandPalette() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return { open, setOpen };
}

export default function CommandPalette({
  open,
  onClose,
  extraCommands = [],
}: {
  open: boolean;
  onClose: () => void;
  extraCommands?: Command[];
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, toggle } = useTheme();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  // Focus is returned to whatever opened the palette on close, so a
  // keyboard user is never dumped back at the top of the document.
  const restoreFocusTo = useRef<HTMLElement | null>(null);

  const commands = useMemo<Command[]>(() => {
    const navigation: Command[] = [
      {
        id: "nav-connection",
        label: "Go to Connection",
        hint: "QR pairing and what to monitor",
        group: "Navigate",
        icon: <IconLink size={16} />,
        run: () => navigate("/"),
      },
      {
        id: "nav-dashboard",
        label: "Go to Properties",
        hint: "Everything captured so far",
        group: "Navigate",
        icon: <IconGrid size={16} />,
        run: () => navigate("/dashboard"),
      },
      {
        id: "nav-settings",
        label: "Go to Settings",
        hint: "Area keywords and time format",
        group: "Navigate",
        icon: <IconSliders size={16} />,
        run: () => navigate("/settings"),
      },
    ];

    const general: Command[] = [
      {
        id: "theme",
        label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
        group: "General",
        icon: theme === "dark" ? <IconSun size={16} /> : <IconMoon size={16} />,
        run: toggle,
      },
      {
        id: "reload",
        label: "Reload the app",
        group: "General",
        icon: <IconRefresh size={16} />,
        run: () => window.location.reload(),
      },
    ];

    return [...navigation, ...extraCommands, ...general];
  }, [navigate, theme, toggle, extraCommands]);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((command) =>
      `${command.label} ${command.hint ?? ""} ${command.group}`.toLowerCase().includes(needle),
    );
  }, [commands, query]);

  useEffect(() => setActive(0), [query, open]);

  useEffect(() => {
    if (open) {
      restoreFocusTo.current = document.activeElement as HTMLElement | null;
      setQuery("");
      // The page behind must not scroll while a modal owns the screen.
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = previousOverflow;
        restoreFocusTo.current?.focus?.();
      };
    }
  }, [open]);

  // Keep the highlighted row inside the scroll viewport when arrowing past
  // its edge — otherwise the selection silently walks off-screen.
  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  const runActive = () => {
    const command = results[active];
    if (!command) return;
    onClose();
    command.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => (index + 1) % Math.max(1, results.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => (index - 1 + results.length) % Math.max(1, results.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      runActive();
    }
  };

  let lastGroup = "";

  return (
    <div
      className="cmdk-scrim"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKeyDown}>
        <div className="cmdk__field">
          <IconSearch size={18} />
          <input
            autoFocus
            className="cmdk__input"
            placeholder="Search actions and pages…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search commands"
          />
          <kbd>esc</kbd>
        </div>

        <div className="cmdk__list" ref={listRef}>
          {results.length === 0 && <div className="cmdk__group">No matching commands</div>}
          {results.map((command, index) => {
            const heading = command.group !== lastGroup ? command.group : null;
            lastGroup = command.group;
            const isCurrent =
              (command.id === "nav-connection" && location.pathname === "/") ||
              (command.id === "nav-dashboard" && location.pathname === "/dashboard") ||
              (command.id === "nav-settings" && location.pathname === "/settings");
            return (
              <div key={command.id}>
                {heading && <div className="cmdk__group">{heading}</div>}
                <button
                  type="button"
                  data-index={index}
                  data-active={index === active}
                  className="cmdk__item"
                  onMouseEnter={() => setActive(index)}
                  onClick={() => {
                    onClose();
                    command.run();
                  }}
                >
                  {command.icon}
                  <span className="grow">
                    {command.label}
                    {command.hint && <div className="cmdk__item-desc">{command.hint}</div>}
                  </span>
                  {isCurrent ? <span className="cmdk__item-desc">current</span> : <IconArrowRight size={14} />}
                </button>
              </div>
            );
          })}
        </div>

        <div className="cmdk__foot">
          <span>
            <kbd>↑</kbd> <kbd>↓</kbd> navigate
          </span>
          <span>
            <kbd>↵</kbd> run
          </span>
          <span>
            <kbd>esc</kbd> close
          </span>
        </div>
      </div>
    </div>
  );
}
