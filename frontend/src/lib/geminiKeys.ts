// Gemini keys typed or imported in Settings: one per line, optionally written `api_key01=AIza…`. Pure, so `npm test`
// covers it. The backend does the same parsing (config.parse_gemini_keys) and is the one that validates.

export const MAX_GEMINI_KEYS = 50

/** The keys in a pasted text or a .txt file: split on lines or commas, `label=` dropped, blanks and repeats ignored. */
export function parseKeys(text: string): string[] {
  const keys: string[] = []
  for (let item of text.split(/[\r\n,]+/)) {
    item = item.trim()
    const eq = item.indexOf('=')
    if (eq >= 0) item = item.slice(eq + 1).trim()
    if (item && !keys.includes(item)) keys.push(item)
  }
  return keys
}

/** The box's text after importing a file: the file's keys replace what was typed (saving replaces every saved key too). */
export function importedKeys(fileText: string): { text: string; count: number } {
  const keys = parseKeys(fileText)
  return { text: keys.join('\n'), count: keys.length }
}

/** "3 keys active" / "1 key active" / "Not set" */
export function keysBadge(count: number): string {
  return count <= 0 ? 'Not set' : count === 1 ? '1 key active' : `${count} keys active`
}
