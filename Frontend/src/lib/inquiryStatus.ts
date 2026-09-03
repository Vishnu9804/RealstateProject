/**
 * Friendly display metadata for Backend/Model/WhatsAppInquiryHandlingModel/
 * inquiry_status.py's WhatsAppInquiryStatus values — the separate, unfiltered
 * WhatsApp connection whatsappInquiryHandling pairs on its own (see
 * lib/whatsappStatus.ts for the other, whatsappDataFetching one; the two
 * are intentionally never conflated in the UI since they're independent
 * linked devices).
 */

import type { StatusDisplay } from "./whatsappStatus";

const STATUS_DISPLAY: Record<string, StatusDisplay> = {
  starting: { label: "Starting up…", tone: "neutral" },
  waiting_for_qr_scan: {
    label: "Waiting for QR scan",
    tone: "info",
    hint: "Pair the inquiry-handling WhatsApp account: fetch GET /api/whatsapp-inquiry/qr and scan it.",
  },
  pairing: { label: "Pairing…", tone: "info" },
  listening: {
    label: "Listening for messages",
    tone: "success",
    hint: "Every inbound WhatsApp message is being classified and routed.",
  },
  disconnected: { label: "Disconnected — reconnecting", tone: "error" },
  logged_out: {
    label: "Logged out of WhatsApp",
    tone: "error",
    hint: "The backend cleared the stale session automatically and is generating a fresh pairing QR code — no restart needed.",
  },
  crashed: { label: "The WhatsApp client crashed", tone: "error" },
};

export function describeInquiryStatus(status: string): StatusDisplay {
  return STATUS_DISPLAY[status] ?? { label: `Unrecognized status: ${status}`, tone: "neutral" };
}
