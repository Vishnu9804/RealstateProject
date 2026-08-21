import { useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { WhatsAppStatusResponse } from "../api/types";

/**
 * Placeholder for now — proves the frontend can actually reach the real
 * backend rather than shipping a static mock. The QR code display, group
 * selection, and area-filter/settings UI that belong here are built in a
 * later step; this just confirms the wiring works end-to-end.
 */
export default function ConnectionPage() {
  const [status, setStatus] = useState<WhatsAppStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await apiClient.get<WhatsAppStatusResponse>("/whatsapp/status");
        if (!cancelled) setStatus(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? `${err.status}: ${err.message}` : "Could not reach the backend.");
        }
      }
    }

    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div>
      <h1>WhatsApp Connection</h1>
      <p style={{ color: "#666" }}>
        Full QR-code pairing and group selection UI comes in a later step. This page currently just confirms
        the backend is reachable.
      </p>

      {error && (
        <p style={{ color: "#c0392b" }}>
          Backend unreachable: {error}. Is <code>uvicorn main:app --reload --port 8000</code> running?
        </p>
      )}

      {status && (
        <table style={{ borderCollapse: "collapse" }}>
          <tbody>
            {Object.entries(status).map(([key, value]) => (
              <tr key={key}>
                <td style={{ padding: "4px 12px 4px 0", color: "#666" }}>{key}</td>
                <td style={{ padding: "4px 0", fontWeight: 600 }}>{String(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
