import { useEffect, useState } from "react";
import { settingsApi } from "../api/settingsApi";
import { friendlyError } from "../lib/apiError";
import { TONE_COLORS } from "../lib/whatsappStatus";

export default function SettingsPage() {
  return (
    <div style={{ maxWidth: 560 }}>
      <h1>Settings</h1>
      <AreaKeywordsSection />
      <TimeFormatSection />
    </div>
  );
}

function AreaKeywordsSection() {
  const [keywords, setKeywords] = useState<string[] | null>(null);
  const [newKeyword, setNewKeyword] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    settingsApi
      .getAreaKeywords()
      .then((data) => {
        if (!cancelled) setKeywords(data.keywords);
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyError(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function addKeyword() {
    const cleaned = newKeyword.trim();
    if (!cleaned) return;
    if (keywords?.some((k) => k.toLowerCase() === cleaned.toLowerCase())) {
      setNewKeyword("");
      return; // already present — the backend also dedupes, but no point adding a visible duplicate row
    }
    setKeywords((prev) => [...(prev ?? []), cleaned]);
    setNewKeyword("");
    setDirty(true);
    setSaved(false);
  }

  function removeKeyword(keyword: string) {
    setKeywords((prev) => (prev ?? []).filter((k) => k !== keyword));
    setDirty(true);
    setSaved(false);
  }

  async function save() {
    if (!keywords) return;
    setSaving(true);
    setError(null);
    try {
      const result = await settingsApi.setAreaKeywords(keywords);
      setKeywords(result.keywords);
      setDirty(false);
      setSaved(true);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section style={{ marginBottom: 32 }}>
      <h2>Area keywords</h2>
      <p style={{ color: "#666" }}>
        A message only reaches the property pipeline if it mentions one of these areas (case-insensitive, whole
        word) — e.g. Althan, Bamroli, Udhna. Without any keywords here, nothing qualifies.
      </p>

      {keywords === null && !error && <p>Loading…</p>}

      {keywords !== null && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {keywords.length === 0 && <span style={{ color: "#999" }}>No area keywords configured yet.</span>}
            {keywords.map((keyword) => (
              <span
                key={keyword}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  background: "#eef2f7",
                  borderRadius: 16,
                  padding: "4px 6px 4px 12px",
                }}
              >
                {keyword}
                <button
                  type="button"
                  onClick={() => removeKeyword(keyword)}
                  aria-label={`Remove ${keyword}`}
                  style={{
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    fontWeight: 700,
                    color: "#666",
                  }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              placeholder="Add an area, e.g. Althan"
              value={newKeyword}
              onChange={(e) => setNewKeyword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addKeyword();
                }
              }}
              style={{ padding: 6, flex: 1, maxWidth: 280 }}
            />
            <button type="button" onClick={addKeyword}>
              Add
            </button>
          </div>

          <div style={{ marginTop: 12 }}>
            <button type="button" onClick={save} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save changes"}
            </button>
            {saved && !dirty && <span style={{ color: TONE_COLORS.success, marginLeft: 12 }}>Saved.</span>}
          </div>
        </>
      )}

      {error && <p style={{ color: TONE_COLORS.error }}>{error}</p>}
    </section>
  );
}

function TimeFormatSection() {
  const [use24Hour, setUse24Hour] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    settingsApi
      .getTimeFormat()
      .then((data) => {
        if (!cancelled) setUse24Hour(data.use_24_hour_format);
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyError(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleChange(value: boolean) {
    const previous = use24Hour;
    setUse24Hour(value); // optimistic — reverted below if the save fails
    setSaving(true);
    setError(null);
    try {
      const result = await settingsApi.setTimeFormat(value);
      setUse24Hour(result.use_24_hour_format);
    } catch (err) {
      setUse24Hour(previous);
      setError(friendlyError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <h2>Timestamp format</h2>
      <p style={{ color: "#666" }}>Dates are always DD/MM/YYYY in Indian Standard Time. Choose the clock format.</p>

      {use24Hour === null && !error && <p>Loading…</p>}

      {use24Hour !== null && (
        <div style={{ display: "flex", gap: 16 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="radio"
              name="time-format"
              checked={!use24Hour}
              disabled={saving}
              onChange={() => handleChange(false)}
            />
            12-hour (e.g. 2:05 PM)
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="radio"
              name="time-format"
              checked={use24Hour}
              disabled={saving}
              onChange={() => handleChange(true)}
            />
            24-hour (e.g. 14:05)
          </label>
        </div>
      )}

      {error && <p style={{ color: TONE_COLORS.error }}>{error}</p>}
    </section>
  );
}
