import { useLayoutEffect, useRef, useState } from "react";
import { useTilt } from "../../hooks/useTilt";
import { useCopy, useCountUp, useReducedMotion } from "../../hooks/useUi";
import { IconCheck, IconCopy, IconSearch, IconX } from "./Icons";

/* =========================================================================
   Panel — the base floating surface.
   ========================================================================= */

interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  pad?: boolean;
  raised?: boolean;
  interactive?: boolean;
  selected?: boolean;
  /** Adds pointer-driven tilt + a moving specular highlight. Reserved for
   *  hero surfaces; tilting everything at once makes the page seasick. */
  tilt?: boolean;
  delay?: number;
  as?: "div" | "section" | "article";
}

export function Panel({
  pad = true,
  raised = false,
  interactive = false,
  selected = false,
  tilt = false,
  delay = 0,
  as: Tag = "div",
  className = "",
  children,
  style,
  ...rest
}: PanelProps) {
  const reduced = useReducedMotion();
  const tiltProps = useTilt(reduced ? 0 : 5, reduced ? 0 : 14);

  const classes = [
    "panel",
    pad && "panel--pad",
    raised && "panel--raised",
    interactive && "panel--interactive",
    selected && "panel--selected",
    tilt && "tilt",
    "anim-rise",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Tag
      {...rest}
      ref={tilt ? (tiltProps.ref as React.Ref<HTMLDivElement>) : undefined}
      onPointerMove={tilt ? tiltProps.onPointerMove : rest.onPointerMove}
      onPointerLeave={tilt ? tiltProps.onPointerLeave : rest.onPointerLeave}
      className={classes}
      style={{ ...style, ["--d" as string]: `${delay}ms` }}
    >
      {tilt && <span className="tilt__glare" aria-hidden="true" />}
      {children}
    </Tag>
  );
}

/* =========================================================================
   Button
   ========================================================================= */

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "ghost";
  size?: "md" | "sm";
  icon?: React.ReactNode;
  /** Swaps the icon for a spinner and disables the button, so a slow save
   *  can never be double-submitted and always says it is working. */
  busy?: boolean;
  iconOnly?: boolean;
}

export function Button({
  variant = "default",
  size = "md",
  icon,
  busy = false,
  iconOnly = false,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const classes = [
    "btn",
    variant !== "default" && `btn--${variant}`,
    size === "sm" && "btn--sm",
    iconOnly && "btn--icon",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button {...rest} className={classes} disabled={disabled || busy} aria-busy={busy || undefined}>
      {busy ? <span className="spinner" /> : icon}
      {!iconOnly && children}
    </button>
  );
}

/* =========================================================================
   Search input — with a clear button, because a filter you can't quickly
   undo is a trap: the user sees an empty result set and has to guess why.
   ========================================================================= */

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  inputRef?: React.Ref<HTMLInputElement>;
  ariaLabel?: string;
}

export function SearchInput({ value, onChange, placeholder, inputRef, ariaLabel }: SearchInputProps) {
  return (
    <div className="input-wrap">
      <span className="input-wrap__icon">
        <IconSearch size={16} />
      </span>
      <input
        ref={inputRef}
        type="search"
        className="input"
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // Escape clears rather than blurring: the fastest way back to the
          // full result set without reaching for the mouse.
          if (event.key === "Escape" && value) {
            event.stopPropagation();
            onChange("");
          }
        }}
      />
      {value && (
        <button type="button" className="input-wrap__clear" onClick={() => onChange("")} aria-label="Clear search">
          <IconX size={13} />
        </button>
      )}
    </div>
  );
}

/* =========================================================================
   Segmented control — a radiogroup that behaves like one physical switch.
   ========================================================================= */

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: React.ReactNode;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: SegmentOption<T>[];
  /** `null` shows the capsule with no option selected — no thumb, no
   *  option marked active — for when a different control (e.g. a "Needs
   *  review" button elsewhere) currently owns the view instead. */
  value: T | null;
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ left: number; width: number } | null>(null);
  const index = value === null ? -1 : options.findIndex((option) => option.value === value);

  // Measured rather than computed from a fixed width, so the thumb stays
  // aligned when labels differ in length or the font renders differently.
  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container || index < 0) {
      setThumb(null);
      return;
    }
    const measure = () => {
      const active = container.querySelectorAll<HTMLButtonElement>(".segmented__opt")[index];
      if (!active) return;
      setThumb({ left: active.offsetLeft, width: active.offsetWidth });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [index, options.length]);

  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel} ref={containerRef}>
      {thumb && (
        <span className="segmented__thumb" style={{ width: thumb.width, transform: `translateX(${thumb.left - 4}px)` }} />
      )}
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className="segmented__opt"
          onClick={() => onChange(option.value)}
        >
          {option.icon}
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* =========================================================================
   Checkbox — the lifting switch plate.
   ========================================================================= */

export function Check({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: () => void;
  children: React.ReactNode;
}) {
  return (
    <label className={`check${checked ? " check--on" : ""}`}>
      <input type="checkbox" checked={checked} onChange={onChange} />
      <span className="check__box" aria-hidden="true">
        <IconCheck size={13} strokeWidth={3} />
      </span>
      <span className="check__text">{children}</span>
    </label>
  );
}

/* =========================================================================
   Stat tile
   ========================================================================= */

export function Stat({
  label,
  value,
  icon,
  tone,
  hint,
  delay = 0,
}: {
  label: string;
  value: number | string;
  icon?: React.ReactNode;
  tone?: "accent" | "ok" | "warn" | "bad";
  hint?: string;
  delay?: number;
}) {
  const numeric = typeof value === "number";
  const animated = useCountUp(numeric ? value : 0);

  return (
    <div
      className={`stat anim-rise${tone ? ` stat--${tone}` : ""}`}
      style={{ ["--d" as string]: `${delay}ms` }}
      title={hint}
    >
      <div className="stat__label">
        {icon}
        {label}
      </div>
      <div className="stat__value tnum">{numeric ? animated.toLocaleString("en-IN") : value}</div>
    </div>
  );
}

/* =========================================================================
   Feedback surfaces
   ========================================================================= */

export function Note({
  tone = "info",
  icon,
  children,
}: {
  tone?: "info" | "ok" | "warn" | "bad";
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={`note note--${tone}`} role={tone === "bad" ? "alert" : "status"}>
      {icon && <span className="note__icon">{icon}</span>}
      <div>{children}</div>
    </div>
  );
}

export function Badge({
  tone,
  children,
  live = false,
  title,
}: {
  tone: "ok" | "warn" | "bad" | "info";
  children: React.ReactNode;
  live?: boolean;
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}${live ? " badge--live" : ""}`} title={title}>
      <span className="badge__dot" />
      {children}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty anim-rise">
      <div className="empty__art">{icon}</div>
      <div className="empty__title">{title}</div>
      <p className="empty__body">{body}</p>
      {action}
    </div>
  );
}

export function Skeleton({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return <span className={`skel ${className}`} style={style} aria-hidden="true" />;
}

/** A skeleton shaped like the content it stands in for. Generic spinners
 *  say "wait"; a shaped skeleton says "here is what is coming and roughly
 *  how much", which makes the same wait feel shorter. */
export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="stack stack-2" aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="skel--line" style={{ opacity: 1 - index * 0.13 }} />
      ))}
    </div>
  );
}

/* =========================================================================
   Copyable value — phone numbers exist to be dialled, not retyped.
   ========================================================================= */

export function Copyable({ text, children }: { text: string; children?: React.ReactNode }) {
  const [copied, copy] = useCopy();
  const done = copied === text;
  return (
    <button
      type="button"
      className={`copy${done ? " copy--done" : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        copy(text);
      }}
      title={done ? "Copied" : `Copy ${text}`}
      aria-label={done ? "Copied" : `Copy ${text}`}
    >
      {children ?? text}
      {done ? <IconCheck size={12} /> : <IconCopy size={12} />}
    </button>
  );
}

/* =========================================================================
   Search-term highlighting — shows *why* a row matched, so a surprising
   result reads as explained rather than as a bug.
   ========================================================================= */

export function Highlight({ text, query }: { text: string; query: string }) {
  const needle = query.trim();
  if (!needle || !text) return <>{text}</>;

  const lower = text.toLowerCase();
  const target = needle.toLowerCase();
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let found = lower.indexOf(target, cursor);

  while (found !== -1) {
    if (found > cursor) parts.push(text.slice(cursor, found));
    parts.push(
      <mark className="hl" key={`${found}-${parts.length}`}>
        {text.slice(found, found + target.length)}
      </mark>,
    );
    cursor = found + target.length;
    found = lower.indexOf(target, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

export function Tip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="tip">
      {children}
      <span className="tip__bubble" role="tooltip">
        {label}
      </span>
    </span>
  );
}
