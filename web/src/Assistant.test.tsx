// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Assistant, { attachedImageDraft } from './Assistant'
import { api, AssistantFailure, type AssistantAvailability } from './api'
import { initialImageDraft } from './App'

let host: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const button = (label: string) => [...host.querySelectorAll('button')].find(b => b.textContent === label)!
async function click(label: string) { await act(async () => button(label).dispatchEvent(new MouseEvent('click', { bubbles: true }))) }
async function type(value: string) {
  const node = host.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(node, value)
    node.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const ready = (session = 'studio-session'): AssistantAvailability => ({ available: true, state: 'ready', sessionKey: session, expiresAt: new Date(Date.now() + 5000).toISOString() })
const draft = () => ({ ...initialImageDraft('private-model'), positivePrompt: 'current draft', negativePrompt: 'noise' })
async function show(initiallyOpen = true) {
  await act(async () => root.render(<Assistant csrf="csrf" draft={draft()} initiallyOpen={initiallyOpen} />))
}

test('collapsed panel makes no session call; offline preserves unsent text', async () => {
  const availability = vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ available: false, state: 'offline' })
  await show(false); expect(availability).not.toHaveBeenCalled()
  await click('Ask Agent'); await type('keep my question')
  expect(button('Send question').disabled).toBe(true)
  await click('Check availability')
  expect(host.querySelector('textarea')!.value).toBe('keep my question')
  expect(availability).toHaveBeenCalledTimes(2)
})

test('explicit context is captured, advice is escaped, no generation or apply', async () => {
  vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ready())
  const ask = vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'owned', sessionKey: 'studio-session', text: '<script>alert(1)</script>', provenance: { model: 'local', provider: 'approved' } })
  const generate = vi.spyOn(api, 'submit')
  await show(); await type('advise me')
  expect(host.querySelector('input[type=checkbox]')!.getAttribute('checked')).toBe(null)
  await act(async () => host.querySelector('input[type=checkbox]')!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await click('Send question')
  expect(ask).toHaveBeenCalledTimes(1)
  const payload = ask.mock.calls[0][0] as { draft: Record<string, unknown>; text: string; requestId: string }
  expect(payload.text).toBe('advise me')
  expect(payload.draft.positive_prompt).toBe('current draft')
  expect(payload.draft).not.toHaveProperty('checkpoint')
  expect(payload.draft).not.toHaveProperty('seed')
  expect(payload.requestId).toMatch(/^[0-9a-f-]{36}$/)
  expect(host.textContent).toContain('<script>alert(1)</script>')
  expect(host.querySelector('script')).toBe(null)
  expect(host.querySelector('iframe')).toBe(null)
  expect(generate).not.toHaveBeenCalled()
})

test('uncertain dispatch never retries or regenerates identity on availability refresh', async () => {
  vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ready())
  const ask = vi.spyOn(api, 'assistantAsk').mockRejectedValue(new AssistantFailure('Outcome unknown', true))
  await show(); await type('one question'); await click('Send question')
  expect(button('Send question').disabled).toBe(true)
  expect(host.querySelector('textarea')!.value).toBe('one question')
  await click('Check availability')
  expect(ask).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('No automatic retry')
  await click('Start new conversation')
  expect(host.querySelector('textarea')!.value).toBe('')
  expect(ask).toHaveBeenCalledTimes(1)
})

test('session changes clear answer/parent while preserving unsent question', async () => {
  const availability = vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ready())
  const ask = vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'old-parent', sessionKey: 'studio-session', text: 'old private advice', provenance: { model: 'local', provider: 'approved' } })
  await show(); await type('first'); await click('Send question'); await type('unsent')
  availability.mockImplementation(async () => ready('new-session'))
  await click('Check availability')
  expect(host.textContent).not.toContain('old private advice')
  expect(host.querySelector('textarea')!.value).toBe('unsent')
  await click('Send question')
  expect(ask.mock.calls[1][0]).not.toHaveProperty('previousHandle')
})

test('stale availability does not enable send and invalid context stays disabled', async () => {
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ ...ready(), expiresAt: new Date(Date.now()-1).toISOString() })
  const ask = vi.spyOn(api, 'assistantAsk')
  await show(); await type('waiting')
  expect(button('Send question').disabled).toBe(true)
  expect(ask).not.toHaveBeenCalled()
  expect(attachedImageDraft({ ...draft(), width: '' })).toBe(null)
  expect(attachedImageDraft({ ...draft(), width: '65' })).toBe(null)
  expect(attachedImageDraft({ ...draft(), positivePrompt: '🐱'.repeat(5000) })).toBe(null)
})

test('account remount clears private answers and unsent questions', async () => {
  vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ready())
  vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'owned', sessionKey: 'studio-session', text: 'private first account', provenance: { model: 'local', provider: 'approved' } })
  await show(); await type('first'); await click('Send question'); await type('private unsent')
  await act(async () => root.render(<Assistant key="different-account" csrf="new" initiallyOpen />))
  expect(host.textContent).not.toContain('private first account')
  expect(host.querySelector('textarea')!.value).toBe('')
})

test('expired login clears private advice and parent reference on probe', async () => {
  const { HTTPFailure } = await import('./api')
  const availability = vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ready())
  vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'owned', sessionKey: 'studio-session', text: 'private advice', provenance: { model: 'local', provider: 'approved' } })
  await show(); await type('first'); await click('Send question'); await type('keep unsent')
  availability.mockRejectedValue(new HTTPFailure('Session expired.', 401))
  await click('Check availability')
  expect(host.textContent).not.toContain('private advice')
  expect(host.querySelector('textarea')!.value).toBe('keep unsent')
  expect(button('Send question').disabled).toBe(true)
})
