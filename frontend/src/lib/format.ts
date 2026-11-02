/** "Apple Inc. Common Stock" -> "Apple Inc." (Alpaca asset names carry the share class). */
export function shortCompanyName(name: string | null | undefined): string | null {
  if (!name) return null;
  return name
    .replace(/\s+(Class [A-Z]\s+)?(Common Stock|Ordinary Shares|Common Shares|Capital Stock)\b.*$/i, "")
    .trim();
}
