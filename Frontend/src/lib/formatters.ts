const INR_FORMATTER = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

/** Prefers the LLM's own readable text ("45 Lakh") — it's what a broker
 * actually wrote and is never wrong the way a computed formatting of the
 * numeric amount could be if the extraction was approximate. The numeric
 * amount is only a fallback for when the text wasn't captured. */
export function formatPrice(priceText: string | null, priceAmountInr: number | null): string {
  if (priceText) return priceText;
  if (priceAmountInr !== null) return INR_FORMATTER.format(priceAmountInr);
  return "—";
}

export function formatCarpetArea(sqft: number | null): string {
  if (sqft === null) return "—";
  return `${sqft.toLocaleString("en-IN")} sqft`;
}
