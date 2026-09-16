import { useState, type ReactNode } from "react";
import { formatHourRange, formatIstDay, istHourStart, nowSeconds, retentionFloor } from "../lib/ist";
import { Badge } from "./Primitives";
import { IconChevronDown } from "./Icons";

export interface HourGroup<T> {
  /** Unix seconds of the IST hour's start. */
  start: number;
  items: T[];
}

export interface HourStat {
  label: string;
  value: string;
  accent?: boolean;
}

/** Buckets items into IST hours inside the 48-hour window, newest hour first.
 *  Hours with nothing in them are not listed. */
export function groupByHour<T>(items: T[], timeOf: (item: T) => number): HourGroup<T>[] {
  const floor = retentionFloor();
  const byHour = new Map<number, T[]>();
  for (const item of items) {
    const time = timeOf(item);
    if (!(time >= floor)) continue;
    const start = istHourStart(time);
    const bucket = byHour.get(start);
    if (bucket) bucket.push(item);
    else byHour.set(start, [item]);
  }
  return Array.from(byHour, ([start, grouped]) => ({ start, items: grouped })).sort((a, b) => b.start - a.start);
}

/**
 * One collapsible card per IST hour ("1 PM – 2 PM"): the hour's totals on the
 * card itself, and that hour's own entries — in each tab's usual card form —
 * dropped down underneath when it is clicked. Several hours can be open at
 * once; only open hours render their entries.
 */
export function HourlyCards<T>({
  groups,
  stats,
  renderBody,
  emptyState,
}: {
  groups: HourGroup<T>[];
  stats: (group: HourGroup<T>) => HourStat[];
  renderBody: (group: HourGroup<T>) => ReactNode;
  emptyState: ReactNode;
}) {
  const [open, setOpen] = useState<ReadonlySet<number>>(() => new Set());

  if (groups.length === 0) return <>{emptyState}</>;

  const currentHour = istHourStart(nowSeconds());
  const toggle = (start: number) =>
    setOpen((previous) => {
      const next = new Set(previous);
      if (next.has(start)) next.delete(start);
      else next.add(start);
      return next;
    });

  return (
    <div className="hour-list">
      {groups.map((group) => {
        const isOpen = open.has(group.start);
        return (
          <div key={group.start} className={`hour-card${isOpen ? " hour-card--open" : ""}`}>
            <button
              type="button"
              className="hour-card__head"
              aria-expanded={isOpen}
              onClick={() => toggle(group.start)}
            >
              <span className="hour-card__when">
                <span className="hour-card__range">{formatHourRange(group.start)}</span>
                <span className="hour-card__day">
                  {formatIstDay(group.start)} · IST
                  {group.start === currentHour && <Badge tone="ok">This hour</Badge>}
                </span>
              </span>
              <span className="hour-card__stats">
                {stats(group).map((stat) => (
                  <span key={stat.label} className={`hour-card__stat${stat.accent ? " hour-card__stat--accent" : ""}`}>
                    <span className="hour-card__stat-value">{stat.value}</span>
                    <span className="hour-card__stat-label">{stat.label}</span>
                  </span>
                ))}
              </span>
              <span className="hour-card__chevron" aria-hidden="true">
                <IconChevronDown size={18} />
              </span>
            </button>
            {isOpen && <div className="hour-card__body">{renderBody(group)}</div>}
          </div>
        );
      })}
    </div>
  );
}
