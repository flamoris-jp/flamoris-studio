// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, expect, test, vi } from 'vitest'
import PersonalityEditor from './Personality'
import { api, HTTPFailure, type Personality } from './api'

let host: HTMLDivElement, root: Root
const initial: Personality = { revision: 1, display_name: 'Helper', sections: [{ title: 'Identity', content: '<script>identity</script>' }], can_edit: true, scope: 'shared_agent', updated_at: 'now', sessionKey: 'owner-session' }
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); host = document.createElement('div'); document.body.append(host); root = createRoot(host); vi.spyOn(api, 'personality').mockResolvedValue(initial) })
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const button = (name: string) => [...host.querySelectorAll('button')].find(b => b.textContent === name)!
async function click(name: string) { await act(async () => button(name).dispatchEvent(new MouseEvent('click', { bubbles: true }))) }
async function show(sessionKey = 'owner-session') { await act(async () => root.render(<PersonalityEditor csrf="csrf" sessionKey={sessionKey} />)) }
async function editName(name: string) { const node = host.querySelector('input:not([type=checkbox])')!; await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, name); node.dispatchEvent(new Event('input', { bubbles: true })) }) }
async function confirmShared() { await act(async () => host.querySelector('input[type=checkbox]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))) }

test('shared scope confirmation, escaped ordered sections and revision save', async () => {
  const save = vi.spyOn(api, 'personalitySave').mockResolvedValue({ revision: 2, duplicate: false })
  await show(); await click('Load current personality'); await editName('New Helper')
  expect(host.textContent).toContain('everyone using this Agent')
  expect(host.querySelector('script')).toBeNull()
  expect(button('Save personality').disabled).toBe(true)
  await confirmShared(); await click('Save personality')
  expect(save).toHaveBeenCalledTimes(1)
  expect(save.mock.calls[0][0]).toMatchObject({ expectedRevision: 1, displayName: 'New Helper', sessionKey: 'owner-session' })
  expect(host.textContent).toContain('Saved revision 2')
})

test('conflict preserves draft; uncertainty freezes exact update ID without auto retry', async () => {
  const save = vi.spyOn(api, 'personalitySave').mockRejectedValue(new HTTPFailure('conflict', 409))
  await show(); await click('Load current personality'); await editName('My unsaved changes'); await confirmShared(); await click('Save personality')
  expect((host.querySelector('input:not([type=checkbox])') as HTMLInputElement).value).toBe('My unsaved changes')
  expect(host.textContent).toContain('Conflict')
  save.mockRejectedValue(new TypeError('disconnected')); await click('Save personality')
  expect(save).toHaveBeenCalledTimes(2)
  const payload = save.mock.calls[1][0]
  expect(host.textContent).toContain('unconfirmed')
  await click('Retry same saved update identity')
  expect(save.mock.calls[2][0]).toEqual(payload)
})

test('session change and late results cannot reveal old personality', async () => {
  let resolve!: (p: Personality) => void
  vi.spyOn(api, 'personality').mockImplementation(() => new Promise(r => { resolve = r }))
  await show(); await click('Load current personality'); await show('new-session')
  await act(async () => resolve(initial))
  expect(host.textContent).not.toContain('identity')
  expect(host.querySelector('textarea')).toBeNull()
})

test('read-only personality cannot be saved or reordered', async () => {
  vi.spyOn(api, 'personality').mockResolvedValue({ ...initial, can_edit: false })
  const save = vi.spyOn(api, 'personalitySave')
  await show(); await click('Load current personality')
  expect(host.textContent).toContain('Read only')
  expect(host.querySelector('fieldset')!.disabled).toBe(true)
  expect(save).not.toHaveBeenCalled()
})
