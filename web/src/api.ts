export type Session = { authenticated: boolean; userName: string | null; csrfToken: string; allowRegistration: boolean }
export type Model = { id: string; name: string }
export type ParameterSpec = { type: string; role?: string; required?: boolean; default?: unknown; minimum?: number; maximum?: number; multiple_of?: number; enum?: (string | number | boolean)[]; pattern?: string; min_length?: number; max_length?: number; max_items?: number; min_items?: number }
export type Workflow = { id: string; kind: string; name: string; selectable: boolean; reason: string | null; definitionVersion: number | null; definitionDigest: string | null; image: { mode: string; profile: string; dimensions: { mode: string; width?: number; height?: number } }; parameters: Record<string, ParameterSpec> }
export type ManagedInput = { id: string; available: boolean; expiresAt?: string; thumbnailUrl: string | null; sourceAssetId?: string | null }
export type Discovery = { available: boolean; templates: string[]; checkpoints: Model[]; loras: Model[]; workflows?: Workflow[]; managedInputReady?: boolean }
export type Asset = { id: string; executionId: string; displayName: string; mimeType: string; mediaKind: string; sizeBytes: number | null; width: number | null; height: number | null; createdAt: string; hasThumbnail: boolean; previewUrl: string; downloadUrl: string; thumbnailUrl: string }
export type ImageSettings = { workflowId?: string; workflowKind?: string; definitionVersion?: number | null; definitionDigest?: string | null; referenceInputId?: string | null; additionalParameters?: Record<string, unknown>; positivePrompt: string; negativePrompt: string; checkpoint: string; width: number; height: number; steps: number; cfg: number; seed: number; sampler?: string; scheduler?: string; denoise?: number; loras?: { name: string; strengthModel: number; strengthClip: number }[] }
export type ImagePreferences = Pick<ImageSettings, 'width' | 'height' | 'steps' | 'cfg'>
export type ImageStyle = { id: string; name: string; positivePrompt: string; negativePrompt: string; createdAt: string; updatedAt: string }
export type AssetDetail = Asset & { state: string; submittedAt: string; settings: Partial<ImageSettings>; referenceInput?: ManagedInput | null; workflowAvailable?: boolean }
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
  changeEmail: (email: string, currentPassword: string, csrf: string) =>
    post<{ userName: string }>('/api/account/email', { email, currentPassword }, csrf),
  changePassword: (currentPassword: string, newPassword: string, confirmPassword: string, csrf: string) =>
    post<{ ok: boolean }>('/api/account/password', { currentPassword, newPassword, confirmPassword }, csrf),
  discovery: () => request<Discovery>('/api/generation/image/discovery'),
  imagePreferences: () => request<ImagePreferences>('/api/generation/image/preferences'),
  saveImagePreferences: (preferences: ImagePreferences, csrf: string) => request<ImagePreferences>('/api/generation/image/preferences', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf }, body: JSON.stringify(preferences) }),
  styles: () => request<{ items: ImageStyle[] }>('/api/generation/image/styles'),
  createStyle: (style: Pick<ImageStyle, 'name' | 'positivePrompt' | 'negativePrompt'>, csrf: string) => post<ImageStyle>('/api/generation/image/styles', style, csrf),
  updateStyle: (id: string, style: Pick<ImageStyle, 'name' | 'positivePrompt' | 'negativePrompt'>, csrf: string) => request<ImageStyle>(`/api/generation/image/styles/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf }, body: JSON.stringify(style) }),
  duplicateStyle: (id: string, name: string, csrf: string) => post<ImageStyle>(`/api/generation/image/styles/${encodeURIComponent(id)}/duplicate`, { name }, csrf),
  deleteStyle: (id: string, csrf: string) => request<{ ok: boolean }>(`/api/generation/image/styles/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { 'X-CSRF-TOKEN': csrf } }),
  createInput: (assetId: string, csrf: string) => post<ManagedInput>('/api/generation/inputs', { assetId }, csrf),
  input: (id: string) => request<ManagedInput>(`/api/generation/inputs/${encodeURIComponent(id)}`),
  deleteInput: (id: string, csrf: string) => request<ManagedInput>(`/api/generation/inputs/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { 'X-CSRF-TOKEN': csrf } }),
  submit: (image: unknown, csrf: string) => post<Execution>('/api/generation/image/jobs', image, csrf),
  execution: (id: string) => request<Execution>(`/api/executions/${encodeURIComponent(id)}`),
  result: (id: string) => request<Execution>(`/api/executions/${encodeURIComponent(id)}/result`),
  cancel: (id: string, csrf: string) => post<Execution>(`/api/executions/${encodeURIComponent(id)}/cancel`, {}, csrf),
  assets: (offset = 0) => request<AssetPage>(`/api/assets?offset=${offset}`),
  asset: (id: string) => request<AssetDetail>(`/api/assets/${encodeURIComponent(id)}`),
  deleteAssets: (ids: string[], csrf: string) => post<Deletion>('/api/assets/delete', { ids }, csrf),
}

