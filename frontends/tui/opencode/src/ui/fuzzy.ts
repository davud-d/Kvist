// Fuzzy matching for the pickers: every typed word must match, and a match at the start
// of a word beats one in the middle, which beats scattered letters.

function scoreWord(word: string, text: string): number {
  const at = text.indexOf(word)
  if (at === 0) return 3
  if (at > 0) return text[at - 1] === " " || text[at - 1] === "/" || text[at - 1] === "-" ? 2.5 : 2
  // Letters in order, anywhere: "gpt5" finds "gpt-5-mini".
  let position = 0
  for (const letter of word) {
    position = text.indexOf(letter, position)
    if (position < 0) return 0
    position += 1
  }
  return 1
}

// 0 means no match; a higher score ranks first.
export function score(query: string, text: string): number {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return 1
  const lowered = text.toLowerCase()
  let total = 0
  for (const word of words) {
    const part = scoreWord(word, lowered)
    if (part === 0) return 0
    total += part
  }
  return total / words.length
}

export function rank<T>(items: readonly T[], query: string, text: (item: T) => string): T[] {
  if (!query.trim()) return [...items]
  return items
    .map((item, index) => ({ item, index, value: score(query, text(item)) }))
    .filter((entry) => entry.value > 0)
    .sort((a, b) => b.value - a.value || a.index - b.index)
    .map((entry) => entry.item)
}
