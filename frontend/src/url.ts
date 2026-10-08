/** Turn bare domains into https URLs for the scrape API. */
export function normalizeSeedUrl(input: string): string {
  const raw = input.trim()
  if (!raw) return raw
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(raw)) {
    return raw
  }
  return `https://${raw}`
}
