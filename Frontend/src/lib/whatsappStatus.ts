/**
 * Friendly display metadata for Backend/Model/whatsapp_status.py's
 * WhatsAppStatus values. Deliberately tolerant of an unrecognized value
 * (falls back to showing the raw string) rather than crashing — the
 * backend and frontend are deployed and versioned independently, so a
 * status value the frontend doesn't know about yet must degrade
 * gracefully, not break the page.
 */

export type StatusTone = "neutral" | "info" | "success" | "warning" | "error";

export interface StatusDisplay {
  label: string;
  tone: StatusTone;
  /** What the user should do about it, if anything. The raw status says
   *  what the system is doing; this says what that means for the person
   *  looking at it, which is the part they actually need. */
  hint?: string;
}

const STATUS_DISPLAY: Record<string, StatusDisplay> = {
  starting: { label: "Starting up…", tone: "neutral", hint: "The WhatsApp client is booting. This usually takes a few seconds." },
  waiting_for_qr_scan: {
    label: "Waiting for QR scan",
    tone: "info",
    hint: "Scan the code with your phone to link this device.",
  },
  pairing: { label: "Pairing…", tone: "info", hint: "Code accepted — finishing the handshake with WhatsApp." },
  fetching_groups: { label: "Fetching your groups…", tone: "info", hint: "Reading the chats your account belongs to." },
  awaiting_monitoring_selection: {
    label: "Choose what to monitor",
    tone: "warning",
    hint: "Nothing is being watched until you pick at least one group or number.",
  },
  listening: { label: "Listening for messages", tone: "success", hint: "Everything is running. New messages flow straight into Properties." },
  listening_nothing_selected: {
    label: "Connected — nothing selected",
    tone: "warning",
    hint: "The connection is healthy, but no group or number is selected, so nothing is being captured.",
  },
  disconnected: {
    label: "Disconnected — reconnecting",
    tone: "error",
    hint: "The link to WhatsApp dropped. Reconnection is automatic; no action needed yet.",
  },
  logged_out: {
    label: "Logged out of WhatsApp",
    tone: "error",
    hint: "The backend cleared the stale session automatically and is generating a fresh pairing QR code — no restart needed.",
  },
  crashed: {
    label: "The WhatsApp client crashed",
    tone: "error",
    hint: "Check the backend terminal output for the error, then restart it.",
  },
};

export function describeWhatsAppStatus(status: string): StatusDisplay {
  return STATUS_DISPLAY[status] ?? { label: `Unrecognized status: ${status}`, tone: "neutral" };
}

/** Maps a status tone onto the visual tone vocabulary the components use
 *  (`ok` / `warn` / `bad` / `info` / `neutral`) so colour lives in CSS
 *  variables and follows the active theme instead of being hard-coded. */
export function statusTone(tone: StatusTone): "ok" | "warn" | "bad" | "info" | "neutral" {
  switch (tone) {
    case "success":
      return "ok";
    case "warning":
      return "warn";
    case "error":
      return "bad";
    case "info":
      return "info";
    default:
      return "neutral";
  }
}

