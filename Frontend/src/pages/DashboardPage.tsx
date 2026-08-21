import { Fragment, useMemo, useState } from "react";
import { propertyApi } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatPrice } from "../lib/formatters";
import { TONE_COLORS } from "../lib/whatsappStatus";

const REFRESH_INTERVAL_MS = 8000;
const FETCH_LIMIT = 500;

type ReviewFilter = "all" | "accepted" | "needs_review";

const cellStyle: React.CSSProperties = { padding: "8px 10px", verticalAlign: "top" };
const truncateStyle: React.CSSProperties = {
  ...cellStyle,
  maxWidth: 180,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export default function DashboardPage() {
  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState("");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  usePolling(async () => {
    setRefreshing(true);
    try {
      const data = await propertyApi.getProperties(FETCH_LIMIT);
      setProperties(data);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setRefreshing(false);
    }
  }, REFRESH_INTERVAL_MS);

  const needsReviewCount = useMemo(
    () => (properties ?? []).filter((p) => p.review_status === "needs_review").length,
    [properties],
  );

  const visibleProperties = useMemo(() => {
    if (!properties) return [];
    const query = search.trim().toLowerCase();
    const filtered = properties.filter((property) => {
      if (reviewFilter !== "all" && property.review_status !== reviewFilter) return false;
      if (!query) return true;
      const haystack = [
        property.society_name,
        property.area_name,
        property.address,
        property.contact_name,
        property.contact_phone,
        property.description,
        property.group_name,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
    // Newest first — the API returns oldest-to-newest within its window,
    // which reads backwards for a dashboard people check for what's new.
    return [...filtered].reverse();
  }, [properties, search, reviewFilter]);

  return (
    <div>
      <h1>Properties</h1>

      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
        <input
          type="text"
          placeholder="Search society, area, address, contact…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ padding: 6, width: 280 }}
        />
        <select value={reviewFilter} onChange={(e) => setReviewFilter(e.target.value as ReviewFilter)} style={{ padding: 6 }}>
          <option value="all">All statuses</option>
          <option value="accepted">Accepted only</option>
          <option value="needs_review">Needs review only</option>
        </select>
        <button
          type="button"
          onClick={() => {
            setRefreshing(true);
            propertyApi
              .getProperties(FETCH_LIMIT)
              .then((data) => {
                setProperties(data);
                setLastUpdated(new Date());
                setError(null);
              })
              .catch((err) => setError(friendlyError(err)))
              .finally(() => setRefreshing(false));
          }}
          disabled={refreshing}
        >
          {refreshing ? "Refreshing…" : "Refresh now"}
        </button>
        {lastUpdated && (
          <span style={{ color: "#999", fontSize: 13 }}>Last updated {lastUpdated.toLocaleTimeString("en-IN")}</span>
        )}
      </div>

      {error && <p style={{ color: TONE_COLORS.error }}>Backend unreachable: {error}</p>}

      {properties === null && !error && <p>Loading…</p>}

      {properties !== null && (
        <p style={{ color: "#666" }}>
          Showing {visibleProperties.length} of {properties.length} stored propert{properties.length === 1 ? "y" : "ies"}
          {needsReviewCount > 0 && (
            <>
              {" "}
              — <strong style={{ color: TONE_COLORS.warning }}>{needsReviewCount} need review</strong>
            </>
          )}
          .
        </p>
      )}

      {properties !== null && properties.length === 0 && (
        <p>No properties stored yet — they'll appear here once qualified WhatsApp messages have been structured.</p>
      )}

      {visibleProperties.length > 0 && (
        <div style={{ overflowX: "auto", border: "1px solid #ddd", borderRadius: 6, background: "#fff" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 1100 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc", background: "#f9fafb" }}>
                <th style={cellStyle}>Status</th>
                <th style={cellStyle}>Society</th>
                <th style={cellStyle}>Area</th>
                <th style={cellStyle}>Address</th>
                <th style={cellStyle}>BHK</th>
                <th style={cellStyle}>Type</th>
                <th style={cellStyle}>Carpet area</th>
                <th style={cellStyle}>Price</th>
                <th style={cellStyle}>Contact</th>
                <th style={cellStyle}>Group</th>
                <th style={cellStyle}>Timestamp (IST)</th>
              </tr>
            </thead>
            <tbody>
              {visibleProperties.map((property) => {
                const isExpanded = expandedId === property.source_message_id;
                return (
                  <Fragment key={property.source_message_id}>
                    <tr
                      onClick={() => setExpandedId(isExpanded ? null : property.source_message_id)}
                      style={{ borderBottom: "1px solid #eee", cursor: "pointer" }}
                    >
                      <td style={cellStyle}>
                        <ReviewBadge status={property.review_status} title={property.review_notes ?? undefined} />
                      </td>
                      <td style={truncateStyle} title={property.society_name ?? undefined}>
                        {property.society_name ?? "—"}
                      </td>
                      <td style={truncateStyle} title={property.area_name ?? undefined}>
                        {property.area_name ?? "—"}
                      </td>
                      <td style={truncateStyle} title={property.address ?? undefined}>
                        {property.address ?? "—"}
                      </td>
                      <td style={cellStyle}>{property.bhk ?? "—"}</td>
                      <td style={cellStyle}>{property.property_type ?? "—"}</td>
                      <td style={cellStyle}>{formatCarpetArea(property.carpet_area_sqft)}</td>
                      <td style={cellStyle}>{formatPrice(property.price_text, property.price_amount_inr)}</td>
                      <td style={truncateStyle} title={`${property.contact_name ?? ""} ${property.contact_phone ?? ""}`.trim()}>
                        {property.contact_name ?? "—"}
                        {property.contact_phone && (
                          <div style={{ color: "#999", fontSize: 12 }}>{property.contact_phone}</div>
                        )}
                      </td>
                      <td style={truncateStyle} title={property.group_name}>
                        {property.group_name}
                      </td>
                      <td style={cellStyle}>{property.formatted_timestamp}</td>
                    </tr>
                    {isExpanded && (
                      <tr style={{ background: "#fafbfc", borderBottom: "1px solid #eee" }}>
                        <td colSpan={11} style={{ padding: "12px 16px" }}>
                          {property.review_status === "needs_review" && property.review_notes && (
                            <p style={{ color: TONE_COLORS.warning, margin: "0 0 8px" }}>
                              <strong>Flagged for review:</strong> {property.review_notes}
                            </p>
                          )}
                          {property.description && (
                            <p style={{ margin: "0 0 8px" }}>
                              <strong>Description:</strong> {property.description}
                            </p>
                          )}
                          <p style={{ margin: "0 0 8px", color: "#666" }}>
                            <strong>Sender:</strong> {property.sender_name} (saved as {property.sender_saved_name}),{" "}
                            {property.sender_phone}
                          </p>
                          <p style={{ margin: 0, color: "#666", whiteSpace: "pre-wrap" }}>
                            <strong>Original message:</strong> {property.message_text}
                          </p>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {properties !== null && properties.length > 0 && visibleProperties.length === 0 && (
        <p style={{ color: "#666" }}>No properties match the current search/filter.</p>
      )}
    </div>
  );
}

function ReviewBadge({ status, title }: { status: PropertyRecord["review_status"]; title?: string }) {
  const isNeedsReview = status === "needs_review";
  return (
    <span
      title={title}
      style={{
        display: "inline-block",
        background: isNeedsReview ? "#fdecc8" : "#e6f4ea",
        color: isNeedsReview ? "#8a5a00" : "#1e7e34",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {isNeedsReview ? "Needs review" : "Accepted"}
    </span>
  );
}
