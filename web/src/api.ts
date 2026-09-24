export type Session = { authenticated: boolean; userName: string | null; csrfToken: string; allowRegistration: boolean }
export type Model = { id: string; name: string }
export type Discovery = { available: boolean; templates: string[]; checkpoints: Model[]; loras: Model[] }
export type Asset = { id: string; executionId: string; displayName: string; mimeType: string; mediaKind: string; sizeBytes: number | null; width: number | null; height: number | null; createdAt: string; hasThumbnail: boolean; previewUrl: string; downloadUrl: string; thumbnailUrl: string }
export type AssetDetail = Asset & { state: string; submittedAt: string; settings: Record<string, string | number> }
export type AssetPage = { items: Asset[]; nextOffset: number | null }
export type Deletion = { results: { id: string; deleted: boolean; error?: string }[] }
export type Execution = { id: string; state: string; submittedAt: string; assets: Asset[] }

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401) throw new Error('Session expired. Refresh and sign in again.')
    throw new Error(body.message || body.error || `Request failed (${response.status}).`)
  }
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}
const post = <T>(path: string, body: unknown, csrf: string) => request<T>(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf }, body: JSON.stringify(body),
})
export const api = {
  session: () => request<Session>('/api/session'),
  authenticate: (action: 'login' | 'register', email: string, password: string, csrf: string) => post(`/api/auth/${action}`, { email, password }, csrf),
  logout: (csrf: string) => post('/api/auth/logout', {}, csrf),
  discovery: () => request<Discovery>('/api/generation/image/discovery'),
  submit: (image: unknown, csrf: string) => post<Execution>('/api/generation/image/jobs', image, csrf),
  execution: (id: string) => request<Execution>(`/api/executions/${encodeURIComponent(id)}`),
  result: (id: string) => request<Execution>(`/api/executions/${encodeURIComponent(id)}/result`),
  cancel: (id: string, csrf: string) => post<Execution>(`/api/executions/${encodeURIComponent(id)}/cancel`, {}, csrf),
  assets: (offset = 0) => request<AssetPage>(`/api/assets?offset=${offset}`),
  asset: (id: string) => request<AssetDetail>(`/api/assets/${encodeURIComponent(id)}`),
  deleteAssets: (ids: string[], csrf: string) => post<Deletion>('/api/assets/delete', { ids }, csrf),
}
