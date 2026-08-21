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
}

const STATUS_DISPLAY: Record<string, StatusDisplay> = {
  starting: { label: "Starting up…", tone: "neutral" },
  waiting_for_qr_scan: { label: "Scan the QR code below with WhatsApp", tone: "info" },
  pairing: { label: "Pairing…", tone: "info" },
  fetching_groups: { label: "Fetching your WhatsApp groups…", tone: "info" },
  awaiting_monitoring_selection: { label: "Select the groups/numbers to monitor below", tone: "warning" },
  listening: { label: "Connected and listening for messages", tone: "success" },
  listening_nothing_selected: { label: "Connected, but nothing is selected to monitor yet", tone: "warning" },
  disconnected: { label: "Disconnected — attempting to reconnect automatically", tone: "error" },
  logged_out: {
    label: "Logged out of WhatsApp — delete Backend/Service/session and restart the backend to re-pair",
    tone: "error",
  },
  crashed: { label: "The WhatsApp client crashed — check the backend's terminal output", tone: "error" },
};

export function describeWhatsAppStatus(status: string): StatusDisplay {
  return STATUS_DISPLAY[status] ?? { label: `Unrecognized status: ${status}`, tone: "neutral" };
}

export const TONE_COLORS: Record<StatusTone, string> = {
  neutral: "#666",
  info: "#1a73e8",
  success: "#188038",
  warning: "#b06000",
  error: "#c0392b",
};
