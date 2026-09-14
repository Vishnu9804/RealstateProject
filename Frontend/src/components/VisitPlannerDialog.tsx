import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AgentSummary } from "../api/types";
import { formatVisitTime } from "../lib/formatters";
import { Avatar, Button } from "./ui/Primitives";
import { IconCheck, IconChevron, IconTag, IconX } from "./ui/Icons";

/**
 * The matches dialog's small "who, and when" dialog — the one place a site
 * visit's agent and time are picked, in three situations:
 *
 *   - "assign":     a property was just ticked. Pick an agent, then a time
 *                   (or skip the time). Nothing is sent or saved here — the
 *                   choice rides on the ticked card until "Assign & send".
 *   - "revisit":    the Revisit button on a Completed card. Same two steps;
 *                   continuing leads straight into the hand-off messages.
 *   - "reschedule": the Assigned tab's "Set visit time" / edit-time action.
 *                   The agent is already fixed, so only the time step shows.
 *
 * Deliberately tiny — compact agent rows instead of the full agent cards,
 * and once an agent is picked every other agent disappears and the date and
 * time pickers take their place — so the whole decision is a few clicks in
 * one small box. It never calls the backend itself: the caller decides what
 * a confirmed choice means, which is what keeps the "assign" case free of
 * any network traffic at all.
 */

export type PlannerMode = "assign" | "revisit" | "reschedule";

type Minute = 0 | 15 | 30 | 45;
type Meridiem = "AM" | "PM";

const MINUTES: Minute[] = [0, 15, 30, 45];
const MERIDIEMS: Meridiem[] = ["AM", "PM"];
const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const QUARTER_MS = 15 * 60 * 1000;
/** Default time offered when nothing was picked before — late morning is
 *  the most common site-visit slot, and it's one click to change. */
const DEFAULT_HOUR = 11;

interface TimeValue {
  /** "YYYY-MM-DD" in the browser's own timezone; null = no day picked yet. */
  dateKey: string | null;
  hour: number; // 1..12
  minute: Minute;
  meridiem: Meridiem;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function dateKeyOf(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** 13 → 1, 0 → 12: the hour wheel goes round, it never stops at an end. */
function wrapHour(hour: number): number {
  return ((((hour - 1) % 12) + 12) % 12) + 1;
}

function toTimeValue(iso: string | null): TimeValue {
  const fallback: TimeValue = { dateKey: null, hour: DEFAULT_HOUR, minute: 0, meridiem: "AM" };
  if (!iso) return fallback;
  const ms = new Date(iso).getTime();
  if (Number.isNaN(ms)) return fallback;
  // Snapped to the nearest quarter hour, since that is all the minute
  // buttons can show — a time this dialog wrote is always on one already.
  const rounded = new Date(Math.round(ms / QUARTER_MS) * QUARTER_MS);
  const hours24 = rounded.getHours();
  return {
    dateKey: dateKeyOf(rounded),
    hour: hours24 % 12 === 0 ? 12 : hours24 % 12,
    minute: rounded.getMinutes() as Minute,
    meridiem: hours24 >= 12 ? "PM" : "AM",
  };
}

function toIso(value: TimeValue): string | null {
  if (!value.dateKey) return null;
  const [year, month, day] = value.dateKey.split("-").map(Number);
  const hours24 = (value.hour % 12) + (value.meridiem === "PM" ? 12 : 0);
  return new Date(year, month - 1, day, hours24, value.minute, 0, 0).toISOString();
}

export default function VisitPlannerDialog({
  mode,
  clientName,
  propertyLabel,
  propertyArea,
  wantedAreas,
  agents,
  initialAgentId,
  initialScheduledAt,
  revisitNumber,
  busy = false,
  onConfirm,
  onClose,
}: {
  mode: PlannerMode;
  clientName: string;
  propertyLabel: string;
  /** The property's own area — what "Covers this area" is checked against.
   *  Falls back to the client's wanted areas when the property has none. */
  propertyArea: string | null;
  wantedAreas: string | null;
  /** null while the agent list is still loading. */
  agents: AgentSummary[] | null;
  /** Pre-picked agent (editing an earlier choice, or the fixed agent in
   *  "reschedule") — the dialog then opens straight on the time step. */
  initialAgentId: string | null;
  initialScheduledAt: string | null;
  /** Shown in the header for a re-visit ("visit #2"); null otherwise. */
  revisitNumber: number | null;
  /** The caller is saving — locks the dialog so it can't be closed or
   *  confirmed twice mid-write. */
  busy?: boolean;
  /** scheduledAt is null when the time was skipped. */
  onConfirm: (agentId: string, scheduledAt: string | null) => void;
  onClose: () => void;
}) {
  const [agentId, setAgentId] = useState<string | null>(() =>
    initialAgentId && agents?.some((agent) => agent.agent_id === initialAgentId) ? initialAgentId : null,
  );
  const [time, setTime] = useState<TimeValue>(() => toTimeValue(initialScheduledAt));
  // Re-read every 30s so "that time has already passed" stays true while
  // the dialog sits open — cheap, local, and stops when it closes.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  // Escape closes THIS dialog only. Listening on window in the capture
  // phase and stopping the event there means it never reaches the matches
  // dialog's own document-level Escape handler underneath, so one press
  // never closes two layers at once.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (!busyRef.current) onCloseRef.current();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);

  const areaNeedles = useMemo(() => {
    const own = propertyArea?.trim().toLowerCase();
    if (own) return [own];
    return (wantedAreas ?? "")
      .split(/[,/]/)
      .map((area) => area.trim().toLowerCase())
      .filter(Boolean);
  }, [propertyArea, wantedAreas]);

  // Agents covering the area first; otherwise the team's own order (a
  // stable sort keeps it).
  const rankedAgents = useMemo(
    () =>
      (agents ?? [])
        .map((agent) => ({
          agent,
          covers: agent.coverage_areas.some((area) => areaNeedles.includes(area.trim().toLowerCase())),
        }))
        .sort((a, b) => Number(b.covers) - Number(a.covers)),
    [agents, areaNeedles],
  );

  // An agent removed from the list meanwhile simply sends the dialog back
  // to the agent step, rather than confirming a choice that no longer exists.
  const chosen = agentId ? (agents ?? []).find((agent) => agent.agent_id === agentId) ?? null : null;

  const iso = toIso(time);
  const inPast = iso !== null && new Date(iso).getTime() <= now;
  const unchanged =
    mode === "reschedule" &&
    iso !== null &&
    initialScheduledAt !== null &&
    new Date(iso).getTime() === new Date(initialScheduledAt).getTime();
  const canConfirm = chosen !== null && iso !== null && !inPast && !unchanged && !busy;

  const eyebrow =
    mode === "assign"
      ? "Assign site visit"
      : mode === "revisit"
        ? `Re-visit${revisitNumber ? ` · visit #${revisitNumber}` : ""}`
        : `Visit time${revisitNumber ? ` · re-visit #${revisitNumber}` : ""}`;
  const confirmLabel = mode === "assign" ? "Done" : mode === "revisit" ? "Continue" : "Save time";

  let summary: string;
  if (iso === null) summary = "Pick a day for the visit";
  else if (inPast) summary = "That time has already passed — pick a later one";
  else if (unchanged) summary = `Already booked for ${formatVisitTime(iso)}`;
  else summary = formatVisitTime(iso);

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <div className="mini-modal anim-rise" role="dialog" aria-modal="true" aria-label={`${eyebrow} — ${propertyLabel}`}>
        <div className="mini-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="mini-modal__eyebrow">{eyebrow}</div>
            <div className="mini-modal__title cell-truncate">{propertyLabel}</div>
            <div className="faint small cell-truncate">for {clientName}</div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={busy} aria-label="Close">
            <IconX size={14} />
          </button>
        </div>

        <div className="mini-modal__body">
          {!chosen ? (
            agents === null ? (
              <div className="row-flex faint small">
                <span className="spinner" /> Loading agents…
              </div>
            ) : agents.length === 0 ? (
              <p className="faint small" style={{ margin: 0 }}>
                No agents added yet — add one from the Agents page first.
              </p>
            ) : (
              <div className="agent-mini-list">
                <div className="mini-modal__hint">Who takes {clientName} there?</div>
                {rankedAgents.map(({ agent, covers }) => (
                  <button key={agent.agent_id} type="button" className="agent-mini" onClick={() => setAgentId(agent.agent_id)}>
                    <Avatar name={agent.name} size={30} />
                    <span className="agent-mini__main">
                      <span className="agent-mini__name">{agent.name}</span>
                      <span className="agent-mini__meta">
                        Active {agent.active_clients.length} · Visits/mo {agent.visits_this_month}
                      </span>
                    </span>
                    {covers && (
                      <span className="agent-mini__covers">
                        <IconTag size={10} /> Covers area
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )
          ) : (
            <div className="stack stack-3">
              <div className="agent-mini agent-mini--chosen">
                <Avatar name={chosen.name} size={30} />
                <span className="agent-mini__main">
                  <span className="agent-mini__name">
                    <IconCheck size={12} strokeWidth={2.6} /> {chosen.name} selected
                  </span>
                  <span className="agent-mini__meta">
                    Active {chosen.active_clients.length} · Visits/mo {chosen.visits_this_month}
                  </span>
                </span>
                {mode !== "reschedule" && (
                  <button type="button" className="agent-mini__change" onClick={() => setAgentId(null)} disabled={busy}>
                    Change
                  </button>
                )}
              </div>

              <MonthCalendar value={time.dateKey} onChange={(dateKey) => setTime((previous) => ({ ...previous, dateKey }))} />

              <div className="vt-time">
                <HourWheel value={time.hour} onChange={(hour) => setTime((previous) => ({ ...previous, hour }))} />
                <span className="vt-time__colon">:</span>
                <div className="vt-min" role="group" aria-label="Minutes">
                  {MINUTES.map((minute) => (
                    <button
                      key={minute}
                      type="button"
                      className={`vt-chip${time.minute === minute ? " vt-chip--on" : ""}`}
                      aria-pressed={time.minute === minute}
                      onClick={() => setTime((previous) => ({ ...previous, minute }))}
                    >
                      {pad(minute)}
                    </button>
                  ))}
                </div>
                <div className="vt-ampm" role="group" aria-label="AM or PM">
                  {MERIDIEMS.map((meridiem) => (
                    <button
                      key={meridiem}
                      type="button"
                      className={`vt-chip${time.meridiem === meridiem ? " vt-chip--on" : ""}`}
                      aria-pressed={time.meridiem === meridiem}
                      onClick={() => setTime((previous) => ({ ...previous, meridiem }))}
                    >
                      {meridiem}
                    </button>
                  ))}
                </div>
              </div>

              <div className={`vt-summary${inPast ? " vt-summary--bad" : iso && !unchanged ? " vt-summary--ok" : ""}`}>
                {summary}
              </div>
            </div>
          )}
        </div>

        <div className="mini-modal__foot">
          {!chosen ? (
            <Button size="sm" variant="ghost" onClick={onClose}>
              {mode === "assign" ? "Not now" : "Cancel"}
            </Button>
          ) : mode === "reschedule" ? (
            <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
          ) : (
            // Skipping the time is always allowed on the way in — it can be
            // set later from the Assigned tab.
            <Button size="sm" variant="ghost" onClick={() => onConfirm(chosen.agent_id, null)} disabled={busy}>
              Skip time
            </Button>
          )}
          {chosen && (
            <span style={{ marginLeft: "auto" }}>
              <Button
                size="sm"
                variant="primary"
                busy={busy}
                disabled={!canConfirm}
                onClick={() => canConfirm && onConfirm(chosen.agent_id, iso)}
              >
                {confirmLabel}
              </Button>
            </span>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------- calendar */

function MonthCalendar({ value, onChange }: { value: string | null; onChange: (dateKey: string) => void }) {
  const today = new Date();
  const todayKey = dateKeyOf(today);
  // Opens on the picked day's month — unless that day is in a month already
  // gone (an old booking being changed), where nothing could be picked.
  const [view, setView] = useState(() => {
    const picked = value ? new Date(`${value}T00:00:00`) : null;
    const base = picked && dateKeyOf(picked).slice(0, 7) >= todayKey.slice(0, 7) ? picked : today;
    return { year: base.getFullYear(), month: base.getMonth() };
  });

  // Visits are booked forward, so there is nothing to browse before this month.
  const atOrBeforeThisMonth =
    view.year < today.getFullYear() || (view.year === today.getFullYear() && view.month <= today.getMonth());
  const first = new Date(view.year, view.month, 1);
  const leadingBlanks = (first.getDay() + 6) % 7; // Monday-first
  const daysInMonth = new Date(view.year, view.month + 1, 0).getDate();
  const monthLabel = first.toLocaleString("en-IN", { month: "long", year: "numeric" });

  function shift(delta: number) {
    setView((previous) => {
      const next = new Date(previous.year, previous.month + delta, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });
  }

  return (
    <div className="vt-cal">
      <div className="vt-cal__head">
        <button type="button" className="vt-cal__nav" onClick={() => shift(-1)} disabled={atOrBeforeThisMonth} aria-label="Previous month">
          <span className="vt-rot vt-rot--left">
            <IconChevron size={14} />
          </span>
        </button>
        <span className="vt-cal__month">{monthLabel}</span>
        <button type="button" className="vt-cal__nav" onClick={() => shift(1)} aria-label="Next month">
          <span className="vt-rot vt-rot--right">
            <IconChevron size={14} />
          </span>
        </button>
      </div>
      <div className="vt-cal__grid">
        {WEEKDAYS.map((weekday) => (
          <span key={weekday} className="vt-cal__wd">
            {weekday}
          </span>
        ))}
        {Array.from({ length: leadingBlanks }, (_, index) => (
          <span key={`blank-${index}`} />
        ))}
        {Array.from({ length: daysInMonth }, (_, index) => {
          const day = index + 1;
          const key = `${view.year}-${pad(view.month + 1)}-${pad(day)}`;
          const selected = key === value;
          return (
            <button
              key={key}
              type="button"
              className={["vt-cal__day", key === todayKey && "vt-cal__day--today", selected && "vt-cal__day--on"]
                .filter(Boolean)
                .join(" ")}
              // "YYYY-MM-DD" strings compare in date order.
              disabled={key < todayKey}
              aria-pressed={selected}
              onClick={() => onChange(key)}
            >
              {day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- hour wheel */

/**
 * 1–12 on a drum that goes round: the hour above and below peek out either
 * side of the chosen one. Turn it with the mouse wheel / trackpad, a finger
 * drag, the arrow buttons, the arrow keys, or by clicking the number above
 * or below.
 */
function HourWheel({ value, onChange }: { value: number; onChange: (hour: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const valueRef = useRef(value);
  valueRef.current = value;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const drag = useRef<{ y: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);

  // A native, non-passive listener: React's own onWheel is passive, so it
  // could not stop the dialog body from scrolling while the wheel turns.
  // Deltas are accumulated so a trackpad's stream of tiny events moves one
  // hour per deliberate flick instead of spinning past several.
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let accumulated = 0;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 100 : 1;
      accumulated += event.deltaY * scale;
      if (Math.abs(accumulated) < 40) return;
      const step = accumulated > 0 ? 1 : -1;
      accumulated = 0;
      onChangeRef.current(wrapHour(valueRef.current + step));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, []);

  const previous = wrapHour(value - 1);
  const next = wrapHour(value + 1);

  function pick(hour: number) {
    if (suppressClick.current) return;
    onChange(hour);
  }

  return (
    <div className="vt-wheel-wrap">
      <button type="button" className="vt-wheel__arrow" onClick={() => onChange(previous)} aria-label="Earlier hour">
        <span className="vt-rot vt-rot--up">
          <IconChevron size={13} />
        </span>
      </button>
      <div
        ref={ref}
        className="vt-wheel"
        tabIndex={0}
        role="group"
        aria-label={`Hour, ${value}`}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp") {
            event.preventDefault();
            onChange(previous);
          } else if (event.key === "ArrowDown") {
            event.preventDefault();
            onChange(next);
          }
        }}
        onPointerDown={(event) => {
          drag.current = { y: event.clientY, moved: false };
        }}
        onPointerMove={(event) => {
          const current = drag.current;
          if (!current) return;
          const delta = current.y - event.clientY;
          if (Math.abs(delta) < 22) return;
          current.y = event.clientY;
          current.moved = true;
          onChange(wrapHour(value + (delta > 0 ? 1 : -1)));
        }}
        onPointerUp={() => {
          if (drag.current?.moved) {
            // The click that follows this pointerup belongs to the drag,
            // not to whichever number the finger happened to lift over.
            suppressClick.current = true;
            window.setTimeout(() => {
              suppressClick.current = false;
            }, 0);
          }
          drag.current = null;
        }}
        onPointerLeave={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <button type="button" tabIndex={-1} className="vt-wheel__row" onClick={() => pick(previous)} aria-hidden="true">
          {pad(previous)}
        </button>
        <span className="vt-wheel__row vt-wheel__row--on" key={value}>
          {pad(value)}
        </span>
        <button type="button" tabIndex={-1} className="vt-wheel__row" onClick={() => pick(next)} aria-hidden="true">
          {pad(next)}
        </button>
      </div>
      <button type="button" className="vt-wheel__arrow" onClick={() => onChange(next)} aria-label="Later hour">
        <span className="vt-rot">
          <IconChevron size={13} />
        </span>
      </button>
    </div>
  );
}
