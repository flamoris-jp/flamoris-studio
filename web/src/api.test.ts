import { afterEach, expect, test, vi } from 'vitest'
import { api, IntelligenceFailure, type IntelligenceInput } from './api'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

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
