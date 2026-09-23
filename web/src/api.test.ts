import { afterEach, expect, test, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

test('image submission includes CSRF token and same-origin credentials', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'studio-id', state: 'queued', assets: [] }) })
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
