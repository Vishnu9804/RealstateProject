import type { ReactNode } from "react";

/* ------------------------------------------------------------- Panel */

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{children}</section>;
}

/* ------------------------------------------------------------- Button */

interface ButtonProps {
  children: ReactNode;
  onClick?: () => void;
  icon?: ReactNode;
  size?: "sm" | "md";
  busy?: boolean;
  tone?: "default" | "accent" | "danger";
  disabled?: boolean;
  title?: string;
}

export function Button({ children, onClick, icon, size = "md", busy, tone = "default", disabled, title }: ButtonProps) {
  return (
    <button
      type="button"
      className={`btn btn--${size} btn--${tone}${busy ? " btn--busy" : ""}`}
      onClick={onClick}
      disabled={disabled || busy}
      title={title}
    >
      {icon && <span className="btn__icon">{icon}</span>}
      <span>{children}</span>
    </button>
  );
}

/* ------------------------------------------------------------- Badge */

export function Badge({
  children,
  tone = "neutral",
  live,
  title,
}: {
  children: ReactNode;
  tone?: "info" | "ok" | "warn" | "bad" | "neutral";
  live?: boolean;
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {live && <span className="badge__dot" />}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------- Stat */

export function Stat({
  label,
  value,
  icon,
  hint,
  tone,
  delay = 0,
}: {
  label: string;
  value: string | number;
  icon?: ReactNode;
  hint?: string;
  tone?: "ok" | "warn" | "accent";
  delay?: number;
}) {
  return (
    <div className={`stat${tone ? ` stat--${tone}` : ""}`} style={{ animationDelay: `${delay}ms` }} title={hint}>
      <div className="stat__head">
        {icon && <span className="stat__icon">{icon}</span>}
        <span className="stat__label">{label}</span>
      </div>
      <div className="stat__value">{value}</div>
    </div>
  );
}

/* ------------------------------------------------------------- Note */

export function Note({ children, tone = "info", icon }: { children: ReactNode; tone?: "info" | "warn"; icon?: ReactNode }) {
  return (
    <div className={`note note--${tone}`}>
      {icon && <span className="note__icon">{icon}</span>}
      <div>{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------- Empty state */

export function EmptyState({ icon, title, body }: { icon?: ReactNode; title: string; body?: string }) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state__icon">{icon}</div>}
      <h3>{title}</h3>
      {body && <p>{body}</p>}
    </div>
  );
}

/* ------------------------------------------------------------- Skeleton */

export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-rows">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton-row" style={{ animationDelay: `${i * 90}ms` }} />
      ))}
    </div>
  );
}
