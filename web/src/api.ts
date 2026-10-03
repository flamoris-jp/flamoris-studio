export type Session = { authenticated: boolean; userName: string | null; csrfToken: string; allowRegistration: boolean; accountKey?: string | null }
export type Model = { id: string; name: string }
export type ParameterSpec = { type: string; role?: string; required?: boolean; default?: unknown; minimum?: number; maximum?: number; multiple_of?: number; enum?: (string | number | boolean)[]; pattern?: string; min_length?: number; max_length?: number; max_items?: number; min_items?: number; max_bytes?: number }
export type Workflow = { id: string; kind: string; name: string; selectable: boolean; reason: string | null; definitionVersion: number | null; definitionDigest: string | null; image: { mode: string; profile: string; dimensions: { mode: string; width?: number; height?: number } }; parameters: Record<string, ParameterSpec> }
export type ManagedInput = { id: string; available: boolean; expiresAt?: string; thumbnailUrl: string | null; sourceAssetId?: string | null }
export type Discovery = { available: boolean; templates: string[]; checkpoints: Model[]; loras: Model[]; workflows?: Workflow[]; managedInputReady?: boolean }
export type OutputRole = { port: string; role: string; index: number }
export type Asset = { id: string; executionId: string; displayName: string; mimeType: string; mediaKind: string; sizeBytes: number | null; width: number | null; height: number | null; createdAt: string; hasThumbnail: boolean; previewUrl: string; downloadUrl: string; thumbnailUrl: string; source?: 'generation'; origin?: 'studio' | 'external'; previewKind?: 'image' | 'audio' | 'video' | 'file'; outputRole?: OutputRole | null }
export type ImageSettings = { workflowId?: string; workflowKind?: string; definitionVersion?: number | null; definitionDigest?: string | null; referenceInputId?: string | null; additionalParameters?: Record<string, unknown>; positivePrompt: string; negativePrompt: string; checkpoint: string; width: number; height: number; steps: number; cfg: number; seed: number; sampler?: string; scheduler?: string; denoise?: number; loras?: { name: string; strengthModel: number; strengthClip: number }[] }
export type ImagePreferences = Pick<ImageSettings, 'width' | 'height' | 'steps' | 'cfg'>
export type ImageStyle = { id: string; name: string; positivePrompt: string; negativePrompt: string; createdAt: string; updatedAt: string }
export type AssetDetail = Asset & { state: string; submittedAt: string; settings: Partial<ImageSettings>; referenceInput?: ManagedInput | null; workflowAvailable?: boolean }
export type AssetPage = { items: Asset[]; nextOffset: number | null }
export type Deletion = { results: { id: string; deleted: boolean; error?: string }[] }
export type Execution = { id: string; state: string; submittedAt: string; assets: Asset[]; category?: string; operation?: string; warnings?: string[] }
export type MusicOperation = 'generate' | 'transcribe'
export type MusicInput = { style: string; lyrics: string; seconds: number; steps: number; seed: number | null; lm_seed: number | null }
export type TranscriptionInput = { referenceInputId: string; max_seconds: number; melody_only: boolean }
export type MusicDiscovery = { generate: boolean; transcribe: boolean }
export type SpeechInput = { text: string; caption: string; seconds: number; steps: number; seed: number | null }

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401) throw new HTTPFailure('Session expired. Refresh and sign in again.', 401)
    throw new HTTPFailure(body.message || body.error || `Request failed (${response.status}).`, response.status)
  }
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}
const post = <T>(path: string, body: unknown, csrf: string) => request<T>(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf }, body: JSON.stringify(body),
})
export class HTTPFailure extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}
export type AgentModel = { id: string; display_name: string; data_flow: "local_only" | "remote_authorized" }
export type Personality = { revision: number; display_name: string; sections: { title: string; content: string }[]; can_edit: boolean; scope: "shared_agent"; updated_at: string; sessionKey: string }
export type AssistantAvailability = { available: boolean; state: 'ready' | 'offline' | 'busy' | 'unknown' | 'unavailable'; sessionKey?: string; expiresAt?: string; sessionExpiresAt?: string; modelId?: string; remoteConsent?: boolean }
export type AssistantAnswer = { requestHandle: string; sessionKey: string; text: string; provenance: { model: string; provider: string; execution_id?: string } }
export class AssistantFailure extends Error {
  uncertain: boolean
  constructor(message: string, uncertain: boolean) { super(message); this.uncertain = uncertain }
}
export type IntelligenceModel = { id: string; contextTokens: number; maxOutputTokens: number; available: boolean }
export type IntelligenceDiscovery = { available: boolean; models: IntelligenceModel[]; capabilities: string[]; dataFlow?: string; reason?: string }
export type IntelligenceInput = { requestId: string; modelId: string; capabilityId: string; input: string; instruction: string; maxOutputTokens: number; temperature: number }
export type IntelligenceAnswer = { requestId: string; executionId: string; text: string; finishReason: 'stop' | 'length'; modelId: string; capabilityId: string; elapsedMs: number; usage: { input_tokens: number; output_tokens: number; total_tokens: number } | null }
export class IntelligenceFailure extends Error {
  constructor(message: string, readonly uncertain: boolean) { super(message) }
}
async function intelligenceExecute(payload: IntelligenceInput, csrf: string): Promise<IntelligenceAnswer> {
  const controller = new AbortController()
  // The backend's 150-second bound cannot bound a stalled browser/proxy connection.
  // Aborting receipt is uncertain; it does not prove provider cancellation.
  const deadline = globalThis.setTimeout(() => controller.abort(), 170000)
  try {
    let response: Response
    try {
      response = await fetch('/api/intelligence/execute', { credentials: 'same-origin', method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf }, body: JSON.stringify(payload) })
    } catch { throw new IntelligenceFailure('Inference outcome could not be confirmed. No automatic retry was made.', true) }
    let body: Record<string, unknown>
    try { body = await response.json() }
    catch { throw new IntelligenceFailure('Inference outcome could not be confirmed. No automatic retry was made.', true) }
    if (!response.ok) {
      const message = typeof body.message === 'string' ? body.message : typeof body.detail === 'string' ? body.detail : 'Inference unavailable.'
      const uncertain = typeof body.uncertain === 'boolean' ? body.uncertain : response.status >= 500 || response.status === 401 || /already recorded|result withheld/.test(message)
      throw new IntelligenceFailure(message, uncertain)
    }
    if (body.requestId !== payload.requestId || body.modelId !== payload.modelId || body.capabilityId !== payload.capabilityId || typeof body.text !== 'string' || !['stop', 'length'].includes(String(body.finishReason))) {
      throw new IntelligenceFailure('Inference result could not be confirmed.', true)
    }
    return body as IntelligenceAnswer
  } finally { globalThis.clearTimeout(deadline) }
}
async function assistantAsk(payload: unknown, csrf: string): Promise<AssistantAnswer> {
  let response: Response
  try {
    response = await fetch('/api/assistant/ask', { credentials: 'same-origin', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf }, body: JSON.stringify(payload) })
  } catch { throw new AssistantFailure('Assistant outcome could not be confirmed.', true) }
  if (!response.ok) {
    const body = await response.json().catch(() => { throw new AssistantFailure('Assistant outcome could not be confirmed.', true) })
    const message = typeof body.detail === 'string' ? body.detail : 'Assistant request failed.'
    throw new AssistantFailure(message, response.status >= 500 || response.status === 401 || /already recorded|result withheld/.test(message))
  }
  try { return await response.json() as AssistantAnswer }
  catch { throw new AssistantFailure('Assistant outcome could not be confirmed.', true) }
}
export const api = {
  intelligenceDiscovery: () => request<IntelligenceDiscovery>('/api/intelligence/discovery'),
  intelligenceExecute,
  assistantModels: (csrf: string) => post<{ models: AgentModel[]; defaultModelId?: string }>('/api/assistant/models', {}, csrf),
  assistantStart: (modelId: string, remoteConsent: boolean, csrf: string) => post<{ sessionKey: string; modelId: string }>('/api/assistant/start', { modelId, remoteConsent }, csrf),
  personality: (csrf: string) => post<Personality>('/api/assistant/personality', {}, csrf),
  personalityHistory: (csrf: string, beforeRevision?: number) => post<{ versions: Personality[]; beforeRevision: number | null }>('/api/assistant/personality/history', { ...(beforeRevision ? { beforeRevision } : {}) }, csrf),
  personalitySave: (payload: unknown, csrf: string) => post<{ revision: number; duplicate: boolean }>('/api/assistant/personality/save', payload, csrf),
  assistantAvailability: (csrf: string) => post<AssistantAvailability>('/api/assistant/availability', {}, csrf),
  assistantAsk,
  session: () => request<Session>('/api/session'),
  authenticate: (action: 'login' | 'register', email: string, password: string, csrf: string) => post(`/api/auth/${action}`, { email, password }, csrf),
  logout: (csrf: string) => post('/api/auth/logout', {}, csrf),
  changeEmail: (email: string, currentPassword: string, csrf: string) =>
    post<{ userName: string }>('/api/account/email', { email, currentPassword }, csrf),
  changePassword: (currentPassword: string, newPassword: string, confirmPassword: string, csrf: string) =>
    post<{ ok: boolean }>('/api/account/password', { currentPassword, newPassword, confirmPassword }, csrf),
  discovery: () => request<Discovery>('/api/generation/image/discovery'),
  musicDiscovery: () => request<MusicDiscovery>('/api/generation/music/discovery'),
  musicSubmit: (input: MusicInput | TranscriptionInput, operation: MusicOperation, requestId: string, csrf: string) => post<Execution>(`/api/generation/music/${operation}/jobs`, { ...input, requestId }, csrf),
  musicRequest: (requestId: string) => request<Execution>(`/api/generation/music/requests/${encodeURIComponent(requestId)}`),
  speechDiscovery: () => request<{ available: boolean }>('/api/generation/speech/discovery'),
  speechSubmit: (input: SpeechInput, requestId: string, csrf: string) => post<Execution>('/api/generation/speech/jobs', { ...input, requestId }, csrf),
  speechRequest: (requestId: string) => request<Execution>(`/api/generation/speech/requests/${encodeURIComponent(requestId)}`),
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
  externalImportAvailability: () => request<{ available: boolean }>('/api/generation/external-import'),
  importExternal: (jobId: string, csrf: string) => post<Execution>('/api/generation/external-import', { jobId }, csrf),
  asset: (id: string) => request<AssetDetail>(`/api/assets/${encodeURIComponent(id)}`),
  deleteAssets: (ids: string[], csrf: string) => post<Deletion>('/api/assets/delete', { ids }, csrf),
}
