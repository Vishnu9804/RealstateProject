import { useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { PropertyRecord } from "../api/types";

/**
 * Placeholder for now — the real "Excel-like" tabular dashboard comes in a
 * later step. This proves the /properties endpoint is reachable and shows
 * the raw fields the LLM/duplicate-detection pipeline produces.
 */
export default function DashboardPage() {
  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiClient
      .get<PropertyRecord[]>("/properties")
      .then(setProperties)
      .catch((err) => setError(err instanceof ApiError ? `${err.status}: ${err.message}` : "Could not reach the backend."));
  }, []);

  return (
    <div>
      <h1>Properties</h1>
      <p style={{ color: "#666" }}>
        The proper tabular dashboard comes in a later step. This is a placeholder confirming the pipeline's
        output is reachable from the frontend.
      </p>

      {error && <p style={{ color: "#c0392b" }}>Backend unreachable: {error}</p>}

      {properties && properties.length === 0 && <p>No properties stored yet.</p>}

      {properties && properties.length > 0 && (
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
              <th style={{ padding: 8 }}>Society</th>
              <th style={{ padding: 8 }}>Area</th>
              <th style={{ padding: 8 }}>BHK</th>
              <th style={{ padding: 8 }}>Price</th>
              <th style={{ padding: 8 }}>Review status</th>
              <th style={{ padding: 8 }}>Timestamp (IST)</th>
            </tr>
          </thead>
          <tbody>
            {properties.map((property) => (
              <tr key={property.source_message_id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: 8 }}>{property.society_name ?? "—"}</td>
                <td style={{ padding: 8 }}>{property.area_name ?? "—"}</td>
                <td style={{ padding: 8 }}>{property.bhk ?? "—"}</td>
                <td style={{ padding: 8 }}>{property.price_text ?? "—"}</td>
                <td style={{ padding: 8 }}>{property.review_status}</td>
                <td style={{ padding: 8 }}>{property.formatted_timestamp}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
