import { useEffect, useMemo, useRef, useState } from "react";
import { whatsappApi } from "../api/whatsappApi";
import type { WhatsAppGroup, WhatsAppPersonalChat } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced, useUnsavedGuard } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import type { StatusTone } from "../lib/whatsappStatus";
import { canSelectMonitoring, describeWhatsAppStatus, PIPELINE_STEPS, pipelineStage, statusTone } from "../lib/whatsappStatus";
import { useAppStatus } from "../state/StatusProvider";
import { useToast } from "../components/ui/Toast";
import {
  Badge,
  Button,
  Check,
  EmptyState,
  Highlight,
  Note,
  Panel,
  SearchInput,
  SkeletonRows,
  Stat,
} from "../components/ui/Primitives";
import {
  IconAlert,
  IconCheck,
  IconDatabase,
  IconInbox,
  IconInfo,
  IconLayers,
  IconMessage,
  IconPhone,
  IconPin,
  IconPower,
  IconQr,
  IconRefresh,
  IconUsers,
  IconZap,
} from "../components/ui/Icons";

/** The badge palette has no neutral slot; an unrecognized status is still
 *  information worth showing, so it borrows the informational tone rather
 *  than disappearing. */
function badgeTone(tone: StatusTone): "ok" | "warn" | "bad" | "info" {
  const mapped = statusTone(tone);
  return mapped === "neutral" ? "info" : mapped;
}

const QR_POLL_INTERVAL_MS = 3000;
const GROUPS_POLL_INTERVAL_MS = 10000;

/** WhatsApp JIDs are country code + number, digits only. Catching a bad
 *  entry here — while the user can still see what they typed — beats a
 *  silent no-match hours later when the messages never arrive. */
const VALID_NUMBER = /^\d{8,15}$/;

export default function ConnectionPage() {
  const { status, error: statusError, initialLoading, failures, refresh } = useAppStatus();
  const toast = useToast();

  const [groups, setGroups] = useState<WhatsAppGroup[] | null>(null);
  const [groupFilter, setGroupFilter] = useState("");
  const debouncedFilter = useDebounced(groupFilter, 140);
  const [selectedGroupJids, setSelectedGroupJids] = useState<Set<string>>(new Set());
  const [monitoredGroups, setMonitoredGroups] = useState<WhatsAppGroup[]>([]);
  const [monitoredPersonalChats, setMonitoredPersonalChats] = useState<WhatsAppPersonalChat[]>([]);
  const [personalNumbersInput, setPersonalNumbersInput] = useState("");
  // A ref, not state: this must be read/written inside a closure that's
  // captured once per effect run (every GROUPS_POLL_INTERVAL_MS tick lives
  // inside the same closure) — state read there would stay stale forever
  // within that closure and silently re-run the pre-fill on every poll,
  // overwriting whatever the user is actively editing.
  const selectionInitializedRef = useRef(false);

  const [qrTick, setQrTick] = useState(0);
  const [qrLoadFailed, setQrLoadFailed] = useState(false);

  const [submitting, setSubmitting] = useState(false);

  const waitingForQr = status?.status === "waiting_for_qr_scan";
  const groupsRelevant = canSelectMonitoring(status?.status);
  const display = status ? describeWhatsAppStatus(status.status) : null;
  const stage = pipelineStage(status?.status);
  const backendDown = failures >= 2 && statusError !== null;

  // --- QR code: only polled while a scan is actually being waited on ---
  usePolling(
    () => {
      setQrTick((t) => t + 1);
      setQrLoadFailed(false);
    },
    QR_POLL_INTERVAL_MS,
    waitingForQr,
  );

  // --- groups + current selection: only meaningful once pairing is done ---
  useEffect(() => {
    if (!groupsRelevant) return;
    let cancelled = false;

    async function load() {
      try {
        const [allGroups, monGroups, monChats] = await Promise.all([
          whatsappApi.getGroups(),
          whatsappApi.getMonitoredGroups(),
          whatsappApi.getMonitoredPersonalChats(),
        ]);
        if (cancelled) return;
        setGroups(allGroups);
        setMonitoredGroups(monGroups);
        setMonitoredPersonalChats(monChats);
        // Pre-fill the editable selection from what's currently monitored,
        // but only the first time — afterwards this is the user's own
        // in-progress edit and must not be silently overwritten by a
        // background refresh.
        if (!selectionInitializedRef.current) {
          setSelectedGroupJids(new Set(monGroups.map((g) => g.jid)));
          setPersonalNumbersInput(monChats.map((c) => c.phone_number).join(", "));
          selectionInitializedRef.current = true;
        }
      } catch {
        // Secondary information — a transient failure here shouldn't blank
        // out the primary status banner above.
      }
    }

    load();
    const interval = setInterval(load, GROUPS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [groupsRelevant]);

  const filteredGroups = useMemo(() => {
    if (!groups) return [];
    const query = debouncedFilter.trim().toLowerCase();
    if (!query) return groups;
    return groups.filter((g) => g.name.toLowerCase().includes(query));
  }, [groups, debouncedFilter]);

  const parsedNumbers = useMemo(
    () =>
      personalNumbersInput
        .split(/[,\n]/)
        .map((n) => n.trim())
        .filter(Boolean),
    [personalNumbersInput],
  );
  const invalidNumbers = useMemo(() => parsedNumbers.filter((n) => !VALID_NUMBER.test(n)), [parsedNumbers]);

  /* Dirty tracking exists so the Save button can answer "is there anything
     to save?" honestly. Before, it was always enabled — you could never
     tell whether what is on screen matches what the backend has. */
  const dirty = useMemo(() => {
    const savedGroups = new Set(monitoredGroups.map((g) => g.jid));
    if (savedGroups.size !== selectedGroupJids.size) return true;
    for (const jid of selectedGroupJids) if (!savedGroups.has(jid)) return true;
    const savedNumbers = monitoredPersonalChats.map((c) => c.phone_number).join(",");
    return savedNumbers !== parsedNumbers.join(",");
  }, [monitoredGroups, monitoredPersonalChats, selectedGroupJids, parsedNumbers]);

  useUnsavedGuard(dirty);

  function toggleGroup(jid: string) {
    setSelectedGroupJids((prev) => {
      const next = new Set(prev);
      if (next.has(jid)) next.delete(jid);
      else next.add(jid);
      return next;
    });
  }

  function selectAllFiltered() {
    setSelectedGroupJids((prev) => new Set([...prev, ...filteredGroups.map((g) => g.jid)]));
  }

  function clearAllFiltered() {
    const filteredJids = new Set(filteredGroups.map((g) => g.jid));
    setSelectedGroupJids((prev) => new Set([...prev].filter((jid) => !filteredJids.has(jid))));
  }

  function revert() {
    setSelectedGroupJids(new Set(monitoredGroups.map((g) => g.jid)));
    setPersonalNumbersInput(monitoredPersonalChats.map((c) => c.phone_number).join(", "));
    toast.push({ tone: "info", title: "Changes discarded", message: "Back to what the backend currently monitors." });
  }

  async function handleSubmit() {
    setSubmitting(true);
    try {
      const result = await whatsappApi.submitMonitoringSelection(Array.from(selectedGroupJids), parsedNumbers);
      setMonitoredGroups(result.monitored_groups);
      setMonitoredPersonalChats(result.monitored_personal_chats);
      setPersonalNumbersInput(result.monitored_personal_chats.map((c) => c.phone_number).join(", "));
      toast.push({
        tone: "ok",
        title: "Monitoring updated",
        message: `Watching ${result.monitored_groups.length} group(s) and ${result.monitored_personal_chats.length} number(s).`,
      });
      refresh();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not save selection", message: friendlyError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  const allFilteredSelected =
    filteredGroups.length > 0 && filteredGroups.every((group) => selectedGroupJids.has(group.jid));

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Step 1 — Connection</div>
          <h1 className="page-title">WhatsApp connection</h1>
          <p className="section-head__sub">
            Link this machine to WhatsApp, then choose which groups and personal chats feed the property pipeline.
            Everything here can be changed later without restarting the backend.
          </p>
        </div>
        <Button variant="ghost" icon={<IconRefresh size={15} />} onClick={refresh}>
          Refresh
        </Button>
      </header>

      {backendDown && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {statusError} — check that{" "}
          <code>uvicorn main:app --reload --host 0.0.0.0 --port 8000</code> is running, then this page will recover on its own.
        </Note>
      )}

      <div className="two-col">
        {/* ---- live state + pipeline ---- */}
        <Panel tilt raised className="stack stack-5">
          <div className="row-between">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                Live state
              </div>
              {initialLoading ? (
                <div className="skel skel--text" style={{ width: 180, height: 22 }} />
              ) : (
                <h2>{display?.label ?? "Unknown"}</h2>
              )}
              {display?.hint && <p className="muted small" style={{ marginTop: 6 }}>{display.hint}</p>}
            </div>
            {display && <Badge tone={badgeTone(display.tone)} live>{status?.status}</Badge>}
          </div>

          <div className="steps">
            {PIPELINE_STEPS.map((step, index) => {
              const state = index < stage ? "done" : index === stage ? "active" : "todo";
              return (
                <div key={step.key} className={`step step--${state}`}>
                  <div className="step__rail">
                    <span className="step__dot">
                      {state === "done" ? <IconCheck size={14} strokeWidth={3} /> : index + 1}
                    </span>
                  </div>
                  <div>
                    <div className="step__label">{step.label}</div>
                    <div className="step__note">{step.note}</div>
                  </div>
                </div>
              );
            })}
          </div>

          {status && !status.database_configured && (
            <Note tone="warn" icon={<IconDatabase size={17} />}>
              <strong>No database configured.</strong> Messages are being captured, but nothing is being stored — set{" "}
              <code>DATABASE_URL</code> in the backend environment to keep them.
            </Note>
          )}
        </Panel>

        {/* ---- QR or summary ---- */}
        <Panel className="stack stack-4" delay={90}>
          <div className="section-head__eyebrow" style={{ marginBottom: 0 }}>
            {waitingForQr ? "Pair this device" : "At a glance"}
          </div>

          {waitingForQr ? (
            <div className="stack stack-4" style={{ alignItems: "center" }}>
              {!qrLoadFailed ? (
                <div className="qr">
                  <img src={whatsappApi.getQrCodeUrl(qrTick)} alt="WhatsApp pairing QR code" onError={() => setQrLoadFailed(true)} />
                  <span className="qr__corner qr__corner--tl" />
                  <span className="qr__corner qr__corner--tr" />
                  <span className="qr__corner qr__corner--bl" />
                  <span className="qr__corner qr__corner--br" />
                </div>
              ) : (
                <div className="qr-skeleton">
                  <span className="spinner" style={{ width: 22, height: 22 }} />
                  <span>Waiting for WhatsApp to generate a code…</span>
                </div>
              )}
              <ol className="stack stack-2 small muted" style={{ margin: 0, paddingLeft: 18 }}>
                <li>Open WhatsApp on your phone.</li>
                <li>
                  Go to <strong>Settings → Linked devices</strong>.
                </li>
                <li>
                  Tap <strong>Link a device</strong> and scan the code.
                </li>
              </ol>
              <p className="faint small" style={{ textAlign: "center" }}>
                The code refreshes automatically every few seconds — you never need to reload the page.
              </p>
            </div>
          ) : initialLoading ? (
            <SkeletonRows rows={4} />
          ) : status ? (
            <div className="stat-grid">
              <Stat label="Groups joined" value={status.joined_group_count} icon={<IconUsers size={13} />} delay={0} />
              <Stat label="Monitored" value={status.monitored_group_count + status.monitored_personal_chat_count} icon={<IconLayers size={13} />} tone="accent" delay={60} />
              <Stat label="Messages seen" value={status.captured_message_count} icon={<IconMessage size={13} />} delay={120} />
              <Stat label="Qualified" value={status.qualified_message_count} icon={<IconZap size={13} />} tone="ok" delay={180} hint="Messages that looked property-related and reached the pipeline" />
              <Stat label="Structured" value={status.structured_property_count} icon={<IconDatabase size={13} />} delay={240} />
              <Stat label="Need review" value={status.needs_review_property_count} icon={<IconAlert size={13} />} tone={status.needs_review_property_count > 0 ? "warn" : undefined} delay={300} />
              <Stat label="Outsider" value={status.outsider_property_count} icon={<IconPin size={13} />} delay={360} hint="Structured properties outside every client-selected area" />
            </div>
          ) : null}
        </Panel>
      </div>

      {/* ---- monitoring selection ---- */}
      {groupsRelevant && (
        <Panel className="stack stack-5" delay={140}>
          <div className="row-between">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                Sources
              </div>
              <h2>What should be monitored?</h2>
              <p className="section-head__sub" style={{ marginTop: 6 }}>
                Currently watching <strong>{monitoredGroups.length}</strong> group{monitoredGroups.length === 1 ? "" : "s"} and{" "}
                <strong>{monitoredPersonalChats.length}</strong> personal number
                {monitoredPersonalChats.length === 1 ? "" : "s"}.
              </p>
            </div>
            {dirty && (
              <Badge tone="warn" live title="You have changes that have not been sent to the backend yet">
                Unsaved changes
              </Badge>
            )}
          </div>

          <div className="two-col">
            {/* groups */}
            <div className="stack stack-3">
              <div className="row-between">
                <h3>
                  Groups{" "}
                  <span className="faint small">
                    {groups ? `(${filteredGroups.length} of ${groups.length})` : ""}
                  </span>
                </h3>
                <span className="badge badge--info">{selectedGroupJids.size} selected</span>
              </div>

              <SearchInput
                value={groupFilter}
                onChange={setGroupFilter}
                placeholder="Filter groups by name…"
                ariaLabel="Filter groups by name"
              />

              <div className="row-flex">
                <Button size="sm" onClick={selectAllFiltered} disabled={filteredGroups.length === 0 || allFilteredSelected}>
                  Select {groupFilter ? "filtered" : "all"}
                </Button>
                <Button size="sm" variant="ghost" onClick={clearAllFiltered} disabled={selectedGroupJids.size === 0}>
                  Clear {groupFilter ? "filtered" : "all"}
                </Button>
              </div>

              {groups === null ? (
                <SkeletonRows rows={5} />
              ) : filteredGroups.length === 0 ? (
                <EmptyState
                  icon={<IconUsers size={34} />}
                  title={groups.length === 0 ? "No groups found" : "Nothing matches that filter"}
                  body={
                    groups.length === 0
                      ? "This WhatsApp account is not in any groups yet, or they are still loading."
                      : "Try a shorter search term — filtering only looks at the group name."
                  }
                />
              ) : (
                <div className="scroll-list">
                  {filteredGroups.map((group) => (
                    <Check key={group.jid} checked={selectedGroupJids.has(group.jid)} onChange={() => toggleGroup(group.jid)}>
                      <span className="cell-truncate" style={{ maxWidth: "100%" }} title={group.name}>
                        <Highlight text={group.name} query={debouncedFilter} />
                      </span>
                      <span className="faint small">{group.member_count} members</span>
                    </Check>
                  ))}
                </div>
              )}
            </div>

            {/* personal numbers */}
            <div className="stack stack-3">
              <h3>Personal chats</h3>
              <p className="faint small">
                Phone numbers with country code, digits only, separated by commas or new lines — e.g.{" "}
                <code>919876543210</code>.
              </p>
              <textarea
                className="textarea"
                value={personalNumbersInput}
                onChange={(e) => setPersonalNumbersInput(e.target.value)}
                rows={4}
                aria-label="Personal phone numbers to monitor"
                placeholder="919876543210, 919812345678"
              />

              {parsedNumbers.length > 0 && (
                <div className="row-flex" style={{ gap: 7 }}>
                  {parsedNumbers.map((number, index) => (
                    <span
                      key={`${number}-${index}`}
                      className={`badge ${VALID_NUMBER.test(number) ? "badge--info" : "badge--bad"}`}
                      title={VALID_NUMBER.test(number) ? undefined : "Digits only, 8–15 characters, including country code"}
                    >
                      <IconPhone size={11} />
                      {number}
                    </span>
                  ))}
                </div>
              )}

              {invalidNumbers.length > 0 && (
                <Note tone="warn" icon={<IconAlert size={16} />}>
                  {invalidNumbers.length} entr{invalidNumbers.length === 1 ? "y does" : "ies do"} not look like a phone
                  number. Use digits only, including the country code and no <code>+</code> or spaces.
                </Note>
              )}

              {parsedNumbers.length === 0 && (
                <Note tone="info" icon={<IconInfo size={16} />}>
                  Leave this empty to monitor groups only.
                </Note>
              )}
            </div>
          </div>

          <div className="row-flex">
            <Button variant="primary" onClick={handleSubmit} busy={submitting} disabled={!dirty} icon={<IconPower size={16} />}>
              {submitting ? "Saving…" : dirty ? "Save monitoring selection" : "Everything is saved"}
            </Button>
            {dirty && (
              <Button variant="ghost" onClick={revert} disabled={submitting}>
                Discard changes
              </Button>
            )}
            {selectedGroupJids.size === 0 && parsedNumbers.length === 0 && (
              <span className="small" style={{ color: "var(--warn)" }}>
                Nothing selected — saving this will stop all capture.
              </span>
            )}
          </div>
        </Panel>
      )}

      {!groupsRelevant && !waitingForQr && !initialLoading && (
        <Panel delay={140}>
          <EmptyState
            icon={<IconQr size={36} />}
            title="Waiting on the connection"
            body="Group and chat selection unlocks as soon as this device is linked to WhatsApp. Nothing to do here yet."
          />
        </Panel>
      )}

      {initialLoading && (
        <Panel delay={140}>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Contacting the backend…
            </div>
            <SkeletonRows rows={3} />
          </div>
        </Panel>
      )}

      {!initialLoading && status && status.captured_message_count === 0 && groupsRelevant && (
        <Note tone="info" icon={<IconInbox size={17} />}>
          No messages captured yet. Once a monitored chat receives a message that looks property-related, it will
          appear on the Properties page within a few seconds.
        </Note>
      )}
    </div>
  );
}
