import type { PropertySource } from "../../api/types";

/** The label for a PropertySource, as every match surface prints it. */
export function sourceLabel(source: PropertySource | null | undefined): string {
  return source === "builder_project" ? "Builder project" : "Property";
}

/**
 * The small "Property" / "Builder project" tag on every card and detail view
 * a matched listing appears in — the client inquiry dialog's Main/Outsider,
 * Assigned and Completed views, the broker requirement dialog, and an
 * agent's visits. Both kinds of listing are matched by the same engine and
 * sit side by side, so this is the one thing on the card that says which
 * one you are looking at.
 *
 * Small on purpose (it qualifies the card, it isn't what the card is
 * about), but always rendered — absent `source` means "property", which is
 * what every listing was before builder projects were matched.
 */
export default function SourceTag({ source }: { source?: PropertySource | null }) {
  const builder = source === "builder_project";
  return (
    <span className={`source-tag${builder ? " source-tag--builder" : ""}`}>{sourceLabel(source)}</span>
  );
}
