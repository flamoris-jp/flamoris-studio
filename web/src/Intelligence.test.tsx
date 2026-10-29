// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Intelligence, { textAttachment } from './Intelligence'
import { api, IntelligenceFailure } from './api'

let host: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const button = (label: string) => [...host.querySelectorAll('button')].find(b => b.textContent === label)!
async function click(label: string) { await act(async () => button(label).dispatchEvent(new MouseEvent('click', { bubbles: true }))) }
async function input(node: HTMLTextAreaElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLTextAreaElement.prototype, 'value')!.set!.call(node, value)
    node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}
const ready = () => ({ available: true, models: [{ id: 'local', contextTokens: 32768, maxOutputTokens: 4096, available: true }], capabilities: ['text.generate', 'code.generate', 'reasoning.generate'] })
async function show(active = true) { await act(async () => root.render(<Intelligence csrf="csrf" active={active} />)) }
async function fill() {
  await input(host.querySelector('select')!, 'local')
  await input(host.querySelectorAll('textarea')[1], 'one private prompt')
}

test('offline draft survives checks; execution uses selected model and plain text result', async () => {
  const discovery = vi.spyOn(api, 'intelligenceDiscovery').mockResolvedValue({ available: false, models: [], capabilities: [] })
  const execute = vi.spyOn(api, 'intelligenceExecute').mockImplementation(async payload => ({ ...payload, executionId: 'correlation', text: '<script>plain answer</script>', finishReason: 'length', usage: null, elapsedMs: 1 }))
  await show(); await input(host.querySelectorAll('textarea')[1], 'one private prompt')
  await explainBlockedAction()
  discovery.mockResolvedValue(ready()); await click('Check Intelligence'); await fill(); await click('Run inference')
  expect(execute).toHaveBeenCalledTimes(1)
  expect(execute.mock.calls[0][0]).toMatchObject({ modelId: 'local', input: 'one private prompt', maxOutputTokens: 1024, temperature: 0.7 })
  expect(host.querySelector('script')).toBe(null)
  expect(host.textContent).toContain('may be partial')
  await show(false)
  expect(host.textContent).toContain('plain answer')
})

test('uncertain inference keeps the snapshot and no refresh/navigation replays it', async () => {
  vi.spyOn(api, 'intelligenceDiscovery').mockResolvedValue(ready())
  const execute = vi.spyOn(api, 'intelligenceExecute').mockRejectedValue(new IntelligenceFailure('No automatic retry was made.', true))
  await show(); await fill(); await click('Run inference'); await click('Check Intelligence'); await show(false); await show(true)
  expect(execute).toHaveBeenCalledTimes(1)
  await explainBlockedAction()
  expect(host.querySelectorAll('textarea')[1].value).toBe('one private prompt')
  await click('Start new request')
  expect(host.querySelectorAll('textarea')[1].value).toBe('')
  expect(execute).toHaveBeenCalledTimes(1)
})

test('text/code attachment is bounded UTF-8 with no executable markup', async () => {
  const file = (name: string, bytes: Uint8Array) => ({ name, size: bytes.length, arrayBuffer: async () => bytes.buffer } as unknown as File)
  expect(await textAttachment(file('example.py', new TextEncoder().encode('<script>code</script>')))).toContain('<script>code</script>')
  await expect(textAttachment(file('image.png', new Uint8Array(1)))).rejects.toThrow()
  await expect(textAttachment(file('huge.txt', new Uint8Array(16385)))).rejects.toThrow()
  await expect(textAttachment(file('invalid.txt', new Uint8Array([255])))).rejects.toThrow()
})

async function explainBlockedAction() {
  const action = button('Run inference') as HTMLButtonElement
  expect(action.disabled).toBe(false)
  const before = vi.isMockFunction(api.intelligenceExecute) ? vi.mocked(api.intelligenceExecute).mock.calls.length : null
  await act(async () => action.click())
  expect(host.querySelector('[aria-label="Action needs attention"]')).not.toBeNull()
  expect(host.textContent).toContain('No request was sent.')
  if (before !== null) expect(api.intelligenceExecute).toHaveBeenCalledTimes(before)
}

test('invalid Intelligence token budget and temperature are named without running inference', async () => {
  vi.spyOn(api, 'intelligenceDiscovery').mockResolvedValue(ready())
  const execute = vi.spyOn(api, 'intelligenceExecute')
  await show(); await fill()
  const fields = host.querySelectorAll<HTMLInputElement>('input:not([type=file])')
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(fields[0], '99999')
    fields[0].dispatchEvent(new Event('input', { bubbles: true }))
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(fields[1], '3')
    fields[1].dispatchEvent(new Event('input', { bubbles: true }))
  })
  await explainBlockedAction()
  const message = host.querySelector('[aria-label="Action needs attention"]')!.textContent
  expect(message).toContain('Max output tokens:'); expect(message).toContain('Context budget:'); expect(message).toContain('Temperature:')
  expect(execute).not.toHaveBeenCalled()
})
