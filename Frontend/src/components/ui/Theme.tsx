import { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import { usePersistentState } from "../../hooks/useUi";
import { IconMoon, IconSun } from "./Icons";

/**
 * Theme is stamped on <html data-theme> because the CSS token layer keys off
 * that attribute, and because putting it on the root means the choice is
 * applied before any component decides what colour it is. The default is
 * dark — this is a monitoring dashboard people leave open, and the depth
 * language reads strongest against a dark ground — but the preference is
 * remembered so nobody has to re-pick it every session.
 */

type Theme = "dark" | "light";

const ThemeContext = createContext<{ theme: Theme; toggle: () => void } | null>(null);

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside <ThemeProvider>");
  return context;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = usePersistentState<Theme>("ui.theme", "dark");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const toggle = useCallback(() => setTheme(theme === "dark" ? "light" : "dark"), [theme, setTheme]);
  const value = useMemo(() => ({ theme, toggle }), [theme, toggle]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="btn btn--ghost btn--icon theme-toggle"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      {theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
    </button>
  );
}
