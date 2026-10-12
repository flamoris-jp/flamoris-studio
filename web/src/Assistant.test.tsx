// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Assistant, { attachedImageDraft } from './Assistant'
import { api, AssistantFailure, HTTPFailure, type AssistantAvailability } from './api'
import { initialImageDraft } from './App'
import { readAssistantModel, saveAssistantModel } from './assistantModelPreference'

let host: HTMLDivElement, root: Root
let accountKey: string, accountCount = 0
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  localStorage.clear()
  accountKey = `test-account-${++accountCount}`
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

async function selectModel(id: string) {
  const selector = host.querySelector('select')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(selector, id)
    selector.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function expectNoConsent() {
  expect(host.querySelector('[aria-label="Assistant model selection"] input[type=checkbox]')).toBe(null)
  expect(host.textContent).not.toContain('Allow this conversation')
}

const modelCatalog = { models: [
  { id: 'local', display_name: 'Internal', data_flow: 'local_only' as const },
  { id: 'api', display_name: 'Remote', data_flow: 'remote_authorized' as const },
], defaultModelId: 'local' }

async function reopenInImage() {
  await act(async () => root.render(<div />))
  await show(false)
  await click('Ask Agent')
}

test('selection survives Assistant to Image navigation and a fresh browser-storage read', async () => {
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ ...ready(), modelId: 'api' })
  const start = vi.spyOn(api, 'assistantStart')
  const handoff = vi.spyOn(api, 'assistantSwitch')
  await show(); await selectModel('api'); await reopenInImage()
  expect(host.querySelector('select')!.value).toBe('api')
  expect(localStorage.getItem(`flamoris.assistant.model:${accountKey}`)).toBe('api')
  // A new module has no in-memory fallback, as after a full page reload.
  vi.resetModules()
  const fresh = await import('./assistantModelPreference')
  expect(fresh.readAssistantModel(accountKey)).toBe('api')
  await type('continue')
  expect(button('Send question').disabled).toBe(false)
  expect(start).not.toHaveBeenCalled(); expect(handoff).not.toHaveBeenCalled()
})

test('saved choices are isolated by stable account key', async () => {
  saveAssistantModel(accountKey, 'api')
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ available: false, state: 'unavailable' })
  await show()
  expect(host.querySelector('select')!.value).toBe('api')
  await act(async () => root.render(<Assistant key="other" accountKey="other-account" csrf="other" initiallyOpen />))
  expect(host.querySelector('select')!.value).toBe('local')
  expect(readAssistantModel('other-account')).toBe(null)
})

test('revoked saved model stays unselected instead of falling back or starting', async () => {
  saveAssistantModel(accountKey, 'removed')
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ ...ready(), modelId: 'local' })
  const start = vi.spyOn(api, 'assistantStart')
  await show(); await type('wait')
  expect(host.querySelector('select')!.value).toBe('')
  expect(host.textContent).toContain('Your saved model is unavailable')
  expect(button('Send question').disabled).toBe(true)
  expect(button('Switch selected model').disabled).toBe(true)
  expect(start).not.toHaveBeenCalled()
  expect(readAssistantModel(accountKey)).toBe('removed')
})

test('catalog outage does not erase the preference and reopening restores it', async () => {
  saveAssistantModel(accountKey, 'api')
  const catalog = vi.spyOn(api, 'assistantModels').mockRejectedValue(new Error('offline'))
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ available: false, state: 'unavailable' })
  await show()
  expect(readAssistantModel(accountKey)).toBe('api')
  catalog.mockResolvedValue(modelCatalog)
  await reopenInImage()
  expect(host.querySelector('select')!.value).toBe('api')
})

test('availability response cannot overwrite a saved proposed model or switch it automatically', async () => {
  saveAssistantModel(accountKey, 'api')
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  const availability = vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ ...ready(), modelId: 'local' })
  const handoff = vi.spyOn(api, 'assistantSwitch')
  await show(); await type('wait'); await click('Check availability')
  expect(host.querySelector('select')!.value).toBe('api')
  expect(host.textContent).toContain('Active conversation model: Internal')
  expect(button('Send question').disabled).toBe(true)
  expect(availability).toHaveBeenCalledTimes(2)
  expect(handoff).not.toHaveBeenCalled()
})

test('without a saved preference the authorized active model wins over the default', async () => {
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ ...ready(), modelId: 'api' })
  await show()
  expect(host.querySelector('select')!.value).toBe('api')
})

test('blocked storage retains navigation choice and explains reload limitation', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ available: false, state: 'unavailable' })
  await show(); await selectModel('api')
  expect(host.textContent).toContain('could not save it for a reload')
  await reopenInImage()
  expect(host.querySelector('select')!.value).toBe('api')
})

test('explicitly clearing selection survives navigation without defaulting', async () => {
  vi.spyOn(api, 'assistantModels').mockResolvedValue(modelCatalog)
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ ...ready(), modelId: 'local' })
  await show(); await selectModel(''); await reopenInImage()
  expect(host.querySelector('select')!.value).toBe('')
})

test('remote conversation starts and restarts with normal model controls', async () => {
  let started = false
  vi.spyOn(api, 'assistantModels').mockResolvedValue({ models: [
    { id: 'api', display_name: 'Remote', data_flow: 'remote_authorized' },
  ], defaultModelId: 'api' })
  vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => started ? { ...ready(), modelId: 'api' } : { available: false, state: 'unavailable' })
  const start = vi.spyOn(api, 'assistantStart').mockImplementation(async () => {
    started = true
    return { sessionKey: 'studio-session', modelId: 'api' }
  })
  const confirm = vi.spyOn(window, 'confirm')
  await show(); await type('keep my draft')
  expectNoConsent()
  expect(button('Start selected model').disabled).toBe(false)
  await click('Start selected model')
  expect(start).toHaveBeenCalledWith('api', 'csrf')
  expect(readAssistantModel(accountKey)).toBe('api')
  expect(host.querySelector('textarea')!.value).toBe('keep my draft')
  expect(button('Send question').disabled).toBe(false)
  await click('Start new conversation')
  expect(start).toHaveBeenCalledTimes(2)
  expectNoConsent()
  expect(confirm).not.toHaveBeenCalled()
})

test.each(['local', 'api'])('switching from %s to remote needs no consent and preserves context', async source => {
  let currentModel = source
  vi.spyOn(api, 'assistantModels').mockResolvedValue({ models: [
    { id: 'local', display_name: 'Internal', data_flow: 'local_only' },
    { id: 'api', display_name: 'Remote', data_flow: 'remote_authorized' },
    { id: 'api2', display_name: 'Other remote', data_flow: 'remote_authorized' },
  ], defaultModelId: source })
  vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ({ ...ready(), modelId: currentModel, remoteConsent: source === 'api' }))
  const ask = vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'previous-owned', sessionKey: 'studio-session', text: 'previous answer', provenance: { model: source, provider: 'approved' } })
  const handoff = vi.spyOn(api, 'assistantSwitch').mockImplementation(async payload => {
    currentModel = payload.modelId
    return { sessionKey: 'studio-session', modelId: currentModel, remoteConsent: true }
  })
  const confirm = vi.spyOn(window, 'confirm')
  await show(); await type('first'); await click('Send question'); await type('unsent')
  expectNoConsent()
  await selectModel('api2')
  expect(button('Send question').disabled).toBe(true)
  expect(button('Switch selected model').disabled).toBe(false)
  await click('Switch selected model')
  expect(handoff.mock.calls[0][0]).not.toHaveProperty('remoteConsent')
  expect(host.querySelector('textarea')!.value).toBe('unsent')
  expect(host.textContent).toContain('previous answer')
  expectNoConsent()
  await click('Send question')
  expect(ask.mock.calls[1][0]).toHaveProperty('previousHandle', 'previous-owned')
  expect(confirm).not.toHaveBeenCalled()
})

test('switching after a turn preserves the conversation handle and unsent question', async () => {
  let currentModel = 'local'
  vi.spyOn(api, 'assistantModels').mockResolvedValue({ models: [
    { id: 'local', display_name: 'Local', data_flow: 'local_only' },
    { id: 'second', display_name: 'Second', data_flow: 'local_only' },
  ], defaultModelId: 'local' })
  vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ({ ...ready(), modelId: currentModel }))
  const ask = vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'previous-owned', sessionKey: 'studio-session', text: 'previous answer', provenance: { model: 'local', provider: 'approved' } })
  const handoff = vi.spyOn(api, 'assistantSwitch').mockImplementation(async () => {
    currentModel = 'second'
    return { sessionKey: 'studio-session', modelId: 'second', remoteConsent: false }
  })
  await show(); await type('first'); await click('Send question'); await type('unsent')
  const selector = host.querySelector('select')!
  expect(selector.disabled).toBe(false)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(selector, 'second')
    selector.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await click('Switch selected model')
  expect(handoff).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('previous answer')
  expect(host.querySelector('textarea')!.value).toBe('unsent')
  await click('Send question')
  expect(ask.mock.calls[1][0]).toHaveProperty('previousHandle', 'previous-owned')
})

test('an unconfirmed switch can only be checked with the same request identity', async () => {
  vi.spyOn(api, 'assistantModels').mockResolvedValue({ models: [{ id: 'local', display_name: 'Local', data_flow: 'local_only' }], defaultModelId: 'local' })
  const availability = vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ({ ...ready(), modelId: 'local' }))
  const start = vi.spyOn(api, 'assistantStart')
  const handoff = vi.spyOn(api, 'assistantSwitch').mockRejectedValue(new Error('Acknowledgement lost'))
  await show(); await type('keep this')
  await click('Switch selected model')
  expect(host.querySelector('select')!.disabled).toBe(true)
  expect(button('Send question').disabled).toBe(true)
  availability.mockRejectedValue(new Error('Old upstream session is unavailable'))
  await click('Check availability')
  expect(handoff).toHaveBeenCalledTimes(1)
  await click('Check model switch')
  expect(handoff.mock.calls[0][0]).toEqual(handoff.mock.calls[1][0])
  expect(start).not.toHaveBeenCalled()
  expect(host.querySelector('textarea')!.value).toBe('keep this')
})
async function show(initiallyOpen = true) {
  await act(async () => root.render(<Assistant accountKey={accountKey} csrf="csrf" draft={draft()} initiallyOpen={initiallyOpen} />))
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
  await act(async () => root.render(<Assistant accountKey="different-account" key="different-account" csrf="new" initiallyOpen />))
  expect(host.textContent).not.toContain('private first account')
  expect(host.querySelector('textarea')!.value).toBe('')
})

test('expired login clears private advice and parent reference on probe', async () => {
  const availability = vi.spyOn(api, 'assistantAvailability').mockImplementation(async () => ready())
  vi.spyOn(api, 'assistantAsk').mockResolvedValue({ requestHandle: 'owned', sessionKey: 'studio-session', text: 'private advice', provenance: { model: 'local', provider: 'approved' } })
  await show(); await type('first'); await click('Send question'); await type('keep unsent')
  availability.mockRejectedValue(new HTTPFailure('Session expired.', 401))
  await click('Check availability')
  expect(host.textContent).not.toContain('private advice')
  expect(host.querySelector('textarea')!.value).toBe('keep unsent')
  expect(button('Send question').disabled).toBe(true)
})
