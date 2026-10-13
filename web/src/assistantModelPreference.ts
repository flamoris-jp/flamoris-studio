// Only the picker preference is retained, never an Agent session or authorization.
const memory = new Map<string, string>()
const key = (accountKey: string) => `flamoris.assistant.model:${accountKey}`

export function readAssistantModel(accountKey: string): string | null {
  if (!accountKey) return null
  try {
    const stored = localStorage.getItem(key(accountKey))
    if (stored !== null) { memory.set(accountKey, stored); return stored }
  } catch { /* Navigation still retains the choice when browser storage is blocked. */ }
  return memory.get(accountKey) ?? null
}

export function saveAssistantModel(accountKey: string, modelId: string): boolean {
  if (!accountKey) return false
  memory.set(accountKey, modelId)
  try { localStorage.setItem(key(accountKey), modelId); return true }
  catch { return false }
}
