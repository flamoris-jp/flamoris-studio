import { afterEach, expect, test, vi } from 'vitest'
import { api, imageUploadError, IntelligenceFailure, type IntelligenceInput } from './api'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

test.each([
  [409, { detail: 'Reference storage quota reached' }, 'Reference-image storage is full'],
  [409, { detail: 'Reference storage is busy' }, 'Reference storage is busy'],
  [409, { detail: 'private server text' }, 'The image upload was refused'],
  [409, { detail: '__proto__' }, 'The image upload was refused'],
  [422, null, 'The image upload failed validation'],
  [422, { detail: 'Reference upload was incomplete' }, 'The image upload was incomplete'],
  [422, { detail: 'Image upload metadata could not be verified' }, 'Studio could not verify the uploaded image'],
  [422, { detail: 'Image upload was rejected by the generation service' }, 'The generation service rejected the image upload'],
  [422, { detail: [{ input: 'private-payload' }] }, 'The image upload failed validation'],
  [429, {}, 'Another reference file is uploading'],
  [413, {}, 'The server rejected the upload size'],
  [503, {}, 'The upload service could not confirm'],
])('reference upload HTTP %s gives a safe actionable error without retry', async (status, body, expected) => {
  const fetch = vi.fn().mockResolvedValue({ ok: false, status, json: async () => body })
  vi.stubGlobal('fetch', fetch)
  const failure = await api.uploadInput(new File(['x'], 'x.png', { type: 'image/png' }), 'csrf').catch(error => error)
  const message = imageUploadError(failure)
  expect(message).toContain(expected)
  expect(message).toContain(`HTTP ${status}`)
  expect(message).toContain('No automatic retry')
  expect(message).not.toMatch(/private|\[object Object\]/)
  expect(fetch).toHaveBeenCalledTimes(1)
})

test('proxy HTML and private network errors never enter upload presentation', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 413, json: async () => { throw new Error('private HTML') } }))
  const failure = await api.uploadInput(new File(['x'], 'x.png', { type: 'image/png' }), 'csrf').catch(error => error)
  expect(imageUploadError(failure)).toContain('HTTP 413')
  expect(imageUploadError(new Error('private network diagnostic'))).not.toContain('private')
})

test('assistant start and switch use explicit model selection without browser consent fields', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({ sessionKey: 'owned-session', modelId: 'api' }) })
  vi.stubGlobal('fetch', fetch)
  await api.assistantStart('api', 'csrf')
  const payload = { requestId: 'request', sessionKey: 'owned-session', expectedModelId: 'local', modelId: 'api' }
  await api.assistantSwitch(payload, 'csrf')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ modelId: 'api' })
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(payload)
  expect(fetch.mock.calls.every(([, options]) => options.credentials === 'same-origin' && options.headers['X-CSRF-TOKEN'] === 'csrf')).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(2)
})

test('image submission includes CSRF token and same-origin credentials', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({ id: 'studio-id', state: 'queued', assets: [] }) })
  vi.stubGlobal('fetch', fetch)
  await api.submit({ positivePrompt: 'a quiet stage' }, 'token-123')
  expect(fetch).toHaveBeenCalledWith('/api/generation/image/jobs', expect.objectContaining({
    credentials: 'same-origin', method: 'POST',
    headers: expect.objectContaining({ 'X-CSRF-TOKEN': 'token-123' }),
  }))
})

test('busy response is surfaced without guessing another execution', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false, status: 409, json: async () => ({ error: 'busy', message: 'Generation service is busy.' }),
  }))
  await expect(api.submit({}, 'token')).rejects.toThrow('Generation service is busy.')
})


test('bulk delete uses CSRF and only opaque asset handles', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({ results: [] }) })
  vi.stubGlobal('fetch', fetch)
  await api.deleteAssets(['asset-handle'], 'token-123')
  expect(fetch).toHaveBeenCalledWith('/api/assets/delete', expect.objectContaining({
    credentials: 'same-origin', method: 'POST', body: JSON.stringify({ ids: ['asset-handle'] }),
    headers: expect.objectContaining({ 'X-CSRF-TOKEN': 'token-123' }),
  }))
})

test('external sync supplies only a cursor with CSRF and the selected asset kind is explicit', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({ imported: 0, nextCursor: 4, hasMore: false }) })
  vi.stubGlobal('fetch', fetch)
  await api.syncExternal(3, 'csrf')
  expect(fetch).toHaveBeenCalledWith('/api/generation/external-import/sync', expect.objectContaining({
    method: 'POST', credentials: 'same-origin', body: JSON.stringify({ cursor: 3 }),
    headers: expect.objectContaining({ 'X-CSRF-TOKEN': 'csrf' }), signal: expect.any(AbortSignal),
  }))
  await api.assets(24, 'music')
  expect(fetch).toHaveBeenLastCalledWith('/api/assets?offset=24&kind=music', expect.any(Object))
})

test('stalled external sync is bounded and does not replay or leave a timer', async () => {
  vi.useFakeTimers()
  const fetch = vi.fn().mockImplementation((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  }))
  vi.stubGlobal('fetch', fetch)
  const result = api.syncExternal(0, 'csrf').catch(error => error)
  await vi.advanceTimersByTimeAsync(110000)
  expect(await result).toBeInstanceOf(Error)
  expect(fetch).toHaveBeenCalledOnce()
  expect(vi.getTimerCount()).toBe(0)
})

const inference: IntelligenceInput = { requestId: 'request', modelId: 'local', capabilityId: 'text.generate', input: 'draft', instruction: '', maxOutputTokens: 1024, temperature: 0.7 }

test('stalled inference receipt is bounded and remains uncertain without replay', async () => {
  vi.useFakeTimers()
  const fetch = vi.fn().mockImplementation((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  }))
  vi.stubGlobal('fetch', fetch)
  const outcome = api.intelligenceExecute(inference, 'csrf').catch(error => error)
  await vi.advanceTimersByTimeAsync(170000)
  const error = await outcome
  expect(error).toBeInstanceOf(IntelligenceFailure)
  expect(error.uncertain).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

test('inference receipt deadline also bounds stalled response body and is cleaned up', async () => {
  vi.useFakeTimers()
  const fetch = vi.fn().mockImplementation(async (_url: string, options: RequestInit) => ({
    ok: true, json: () => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }),
  }))
  vi.stubGlobal('fetch', fetch)
  const outcome = api.intelligenceExecute(inference, 'csrf').catch(error => error)
  await vi.advanceTimersByTimeAsync(170000)
  expect((await outcome).uncertain).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

test('completed inference clears its browser deadline', async () => {
  vi.useFakeTimers()
  const answer = { requestId: inference.requestId, modelId: inference.modelId, capabilityId: inference.capabilityId, text: 'answer', finishReason: 'stop' }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => answer }))
  expect(await api.intelligenceExecute(inference, 'csrf')).toEqual(answer)
  expect(vi.getTimerCount()).toBe(0)
})

test('reference upload sends the original bounded file with CSRF and no filename or base64 wrapper', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({ id: 'private-handle', available: true }) })
  vi.stubGlobal('fetch', fetch)
  const file = new File(['fixture'], 'private-name.png', { type: 'image/png' })
  await api.uploadInput(file, 'upload-csrf')
  expect(fetch).toHaveBeenCalledWith('/api/generation/inputs/upload', expect.objectContaining({
    method: 'POST', credentials: 'same-origin', body: file,
    headers: { 'Content-Type': 'image/png', 'X-CSRF-TOKEN': 'upload-csrf' }, signal: expect.any(AbortSignal),
  }))
  expect(fetch).toHaveBeenCalledTimes(1)
})

test('invalid local references do not send an upload request', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await expect(api.uploadInput(new File([], 'x.png', { type: 'image/png' }), 'csrf')).rejects.toThrow('8 MiB')
  await expect(api.uploadInput(new File(['x'], 'x.svg', { type: 'image/svg+xml' }), 'csrf')).rejects.toThrow('PNG')
  expect(fetch).not.toHaveBeenCalled()
})

test('upload deadline bounds a stalled response body and never replays the file', async () => {
  vi.useFakeTimers()
  const fetch = vi.fn().mockImplementation(async (_url: string, options: RequestInit) => ({ ok: true,
    text: () => new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))),
  }))
  vi.stubGlobal('fetch', fetch)
  const result = api.uploadInput(new File(['x'], 'x.png', { type: 'image/png' }), 'csrf').catch(error => error)
  await vi.advanceTimersByTimeAsync(100000)
  expect(await result).toBeInstanceOf(Error)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

