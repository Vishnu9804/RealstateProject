import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { whatsappApi } from "../api/whatsappApi";
import type { WhatsAppGroup, WhatsAppPersonalChat, WhatsAppStatusResponse } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { describeWhatsAppStatus, TONE_COLORS } from "../lib/whatsappStatus";

const STATUS_POLL_INTERVAL_MS = 3000;
const QR_POLL_INTERVAL_MS = 3000;
const GROUPS_POLL_INTERVAL_MS = 10000;

function friendlyError(err: unknown): string {
  return err instanceof ApiError ? `${err.status}: ${err.message}` : "Could not reach the backend.";
}

export default function ConnectionPage() {
  const [status, setStatus] = useState<WhatsAppStatusResponse | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [groups, setGroups] = useState<WhatsAppGroup[] | null>(null);
  const [groupFilter, setGroupFilter] = useState("");
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
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  // --- status: polled continuously, drives everything else on this page ---
  usePolling(async () => {
    try {
      const data = await whatsappApi.getStatus();
      setStatus(data);
      setStatusError(null);
    } catch (err) {
      setStatusError(friendlyError(err));
    }
  }, STATUS_POLL_INTERVAL_MS);

  // --- QR code: only polled while a scan is actually being waited on ---
  const waitingForQr = status?.status === "waiting_for_qr_scan";
  usePolling(
    () => {
      setQrTick((t) => t + 1);
      setQrLoadFailed(false);
    },
    QR_POLL_INTERVAL_MS,
    waitingForQr,
  );

  // --- groups + current selection: only meaningful once pairing is done ---
  const groupsRelevant =
    status !== null && status.status !== "starting" && status.status !== "waiting_for_qr_scan" && status.status !== "pairing";

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
    const query = groupFilter.trim().toLowerCase();
    if (!query) return groups;
    return groups.filter((g) => g.name.toLowerCase().includes(query));
  }, [groups, groupFilter]);

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

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitError(null);
    setSubmitSuccess(false);
    const personalNumbers = personalNumbersInput
      .split(/[,\n]/)
      .map((n) => n.trim())
      .filter(Boolean);
    try {
      const result = await whatsappApi.submitMonitoringSelection(Array.from(selectedGroupJids), personalNumbers);
      setMonitoredGroups(result.monitored_groups);
      setMonitoredPersonalChats(result.monitored_personal_chats);
      setSubmitSuccess(true);
    } catch (err) {
      setSubmitError(friendlyError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const statusDisplay = status ? describeWhatsAppStatus(status.status) : null;

  return (
    <div style={{ maxWidth: 720 }}>
      <h1>WhatsApp Connection</h1>

      {statusError && (
        <p style={{ color: TONE_COLORS.error }}>
          Backend unreachable: {statusError}. Is <code>uvicorn main:app --reload --port 8000</code> running?
        </p>
      )}

      {statusDisplay && (
        <p style={{ color: TONE_COLORS[statusDisplay.tone], fontWeight: 600, fontSize: 16 }}>{statusDisplay.label}</p>
      )}

      {waitingForQr && (
        <div style={{ margin: "16px 0" }}>
          {!qrLoadFailed ? (
            <img
              src={whatsappApi.getQrCodeUrl(qrTick)}
              alt="WhatsApp pairing QR code"
              onError={() => setQrLoadFailed(true)}
              style={{ width: 280, height: 280, border: "1px solid #ddd", borderRadius: 8 }}
            />
          ) : (
            <p style={{ color: "#666" }}>Waiting for the QR code to be generated…</p>
          )}
          <p style={{ color: "#666", maxWidth: 400 }}>
            Open WhatsApp on your phone → Settings → Linked Devices → Link a Device, then scan this code.
          </p>
        </div>
      )}

      {groupsRelevant && groups !== null && (
        <div style={{ marginTop: 24 }}>
          <h2>Select what to monitor</h2>
          <p style={{ color: "#666" }}>
            Currently monitoring {monitoredGroups.length} group(s) and {monitoredPersonalChats.length} personal
            number(s). Change the selection below and save — this can be updated any time without restarting the
            backend.
          </p>

          <h3>Groups ({groups.length} found)</h3>
          <input
            type="text"
            placeholder="Filter groups by name…"
            value={groupFilter}
            onChange={(e) => setGroupFilter(e.target.value)}
            style={{ padding: 6, width: "100%", maxWidth: 360, marginBottom: 8 }}
          />
          <div style={{ marginBottom: 8 }}>
            <button type="button" onClick={selectAllFiltered} style={{ marginRight: 8 }}>
              Select all {groupFilter ? "filtered" : ""}
            </button>
            <button type="button" onClick={clearAllFiltered}>
              Clear {groupFilter ? "filtered" : "all"}
            </button>
          </div>
          <div
            style={{
              border: "1px solid #ddd",
              borderRadius: 6,
              maxHeight: 280,
              overflowY: "auto",
              padding: 8,
              background: "#fff",
            }}
          >
            {filteredGroups.length === 0 && <p style={{ color: "#666", margin: 4 }}>No groups match that filter.</p>}
            {filteredGroups.map((group) => (
              <label key={group.jid} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
                <input
                  type="checkbox"
                  checked={selectedGroupJids.has(group.jid)}
                  onChange={() => toggleGroup(group.jid)}
                />
                <span>
                  {group.name} <span style={{ color: "#999" }}>({group.member_count} members)</span>
                </span>
              </label>
            ))}
          </div>
          <p style={{ color: "#666", fontSize: 13 }}>{selectedGroupJids.size} group(s) selected.</p>

          <h3>Personal chats</h3>
          <p style={{ color: "#666" }}>
            Phone numbers with country code, digits only, separated by commas or new lines (e.g. 919876543210).
          </p>
          <textarea
            value={personalNumbersInput}
            onChange={(e) => setPersonalNumbersInput(e.target.value)}
            rows={3}
            style={{ width: "100%", maxWidth: 480, padding: 8, fontFamily: "inherit" }}
          />

          <div style={{ marginTop: 16 }}>
            <button type="button" onClick={handleSubmit} disabled={submitting}>
              {submitting ? "Saving…" : "Save monitoring selection"}
            </button>
            {submitSuccess && <span style={{ color: TONE_COLORS.success, marginLeft: 12 }}>Saved.</span>}
            {submitError && <span style={{ color: TONE_COLORS.error, marginLeft: 12 }}>{submitError}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
