// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Speech, { initialSpeechDraft, speechPayload } from './Speech'
import { api, HTTPFailure, type Execution } from './api'

const REQUEST = '00000000-0000-4000-8000-000000000001'
const created: Execution = { id: '00000000-0000-4000-8000-000000000002', category: 'speech',
  state: 'queued', submittedAt: '', assets: [] }
let host: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('crypto', { randomUUID: () => REQUEST })
  sessionStorage.clear(); window.location.hash = ''
  vi.spyOn(api, 'speechDiscovery').mockResolvedValue({ available: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); sessionStorage.clear(); window.location.hash = ''
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})
async function show(owner = 'owner-a', observer = vi.fn()) {
  function Editor() {
    const [form, setForm] = useState({ ...initialSpeechDraft(), text: 'こんにちは' })
    const [execution, setExecution] = useState<Execution | null>(null)
    return <Speech csrf="csrf" accountKey={owner} form={form} setForm={setForm} execution={execution}
      onExecution={value => { observer(value); setExecution(value) }} />
  }
  await act(async () => root.render(<Editor key={owner} />))
  return observer
}
const generate = () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Generate speech')!
async function submit() { await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))) }

test('Speech parameters bound text, numbers and the exact browser-safe seed', () => {
  const form = { ...initialSpeechDraft(), text: 'こんにちは', seed: String(Number.MAX_SAFE_INTEGER) }
  expect(speechPayload(form)?.seed).toBe(Number.MAX_SAFE_INTEGER)
  for (const changes of [{ text: ' ' }, { text: '\u0000' }, { text: 'x'.repeat(513) }, { steps: '2.5' },
    { seconds: 'Infinity' }, { seconds: '31' }, { seed: String(2 ** 53) }]) expect(speechPayload({ ...form, ...changes })).toBeNull()
  expect(speechPayload({ ...form, text: '😀'.repeat(512) })).not.toBeNull()
  expect(speechPayload({ ...form, seed: '' })?.seed).toBeNull()
  expect(speechPayload(form)?.seconds).toBeNull()
  expect(speechPayload({ ...form, seconds: '3.5' })?.seconds).toBe(3.5)
})

test('unavailable Speech keeps the draft and does not submit', async () => {
  vi.mocked(api.speechDiscovery).mockResolvedValue({ available: false })
  const send = vi.spyOn(api, 'speechSubmit')
  await show()
  expect((generate() as HTMLButtonElement).disabled).toBe(true)
  expect(host.querySelector('textarea')?.value).toBe('こんにちは')
  await submit(); expect(send).not.toHaveBeenCalled()
})

test('known active receipt sends one UUID and keeps its pending fence until terminal', async () => {
  const send = vi.spyOn(api, 'speechSubmit').mockResolvedValue(created)
  const observed = await show()
  await submit()
  expect(send).toHaveBeenCalledExactlyOnceWith({ text: 'こんにちは', caption: '', seconds: null, steps: 40, seed: 0 }, REQUEST, 'csrf')
  expect(observed).toHaveBeenCalledWith(created)
  expect(sessionStorage.getItem('flamoris.speech.pending:owner-a')).toBe(REQUEST)
  expect(window.location.hash).toBe(`#execution/${created.id}`)
  expect((generate() as HTMLButtonElement).disabled).toBe(true)
})

test('ambiguous receipt preserves only its UUID and resumes through owner-checked read without replay', async () => {
  const send = vi.spyOn(api, 'speechSubmit').mockRejectedValue(new Error('lost receipt'))
  const lookup = vi.spyOn(api, 'speechRequest').mockResolvedValue({ ...created, state: 'submission_unknown' })
  await show(); await submit()
  expect(sessionStorage.getItem('flamoris.speech.pending:owner-a')).toBe(REQUEST)
  expect(sessionStorage.length).toBe(1)
  expect((generate() as HTMLButtonElement).disabled).toBe(true)
  await act(async () => root.render(<div />))
  const observed = await show()
  expect(lookup).toHaveBeenCalledExactlyOnceWith(REQUEST)
  expect(observed).toHaveBeenCalledWith({ ...created, state: 'submission_unknown' })
  expect(send).toHaveBeenCalledOnce()
  expect((generate() as HTMLButtonElement).disabled).toBe(true)
})

test('validation failure allows correction without treating it as an uncertain submit', async () => {
  vi.spyOn(api, 'speechSubmit').mockRejectedValue(new HTTPFailure('Unsupported speech text', 422))
  await show(); await submit()
  expect(host.textContent).toContain('Unsupported speech text')
  expect((generate() as HTMLButtonElement).disabled).toBe(false)
  expect(sessionStorage.length).toBe(0)
})

test('late submit response from an unmounted account cannot replace another account or execution hash', async () => {
  let resolve!: (value: Execution) => void
  vi.spyOn(api, 'speechSubmit').mockImplementation(() => new Promise(value => { resolve = value }))
  const old = await show('owner-a')
  await submit()
  const next = await show('owner-b')
  await act(async () => resolve(created))
  expect(old).not.toHaveBeenCalled(); expect(next).not.toHaveBeenCalled()
  expect(window.location.hash).toBe('')
})


async function chooseReference() {
  const input = host.querySelector('input[type="file"]')!
  Object.defineProperty(input, 'files', {
    configurable: true, value: [new File(['waveform'], 'reference.wav', { type: 'audio/wav' })],
  })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
}

test('reference upload previews locally and submits the owned handle', async () => {
  vi.mocked(api.speechDiscovery).mockResolvedValue({ available: true, referenceAvailable: true })
  const NativeURL = URL
  vi.stubGlobal('URL', class extends NativeURL {
    static createObjectURL = vi.fn(() => 'blob:reference')
    static revokeObjectURL = vi.fn()
  })
  const upload = vi.spyOn(api, 'uploadReferenceAudio').mockResolvedValue({
    id: 'reference-handle', available: true, mimeType: 'audio/wav', thumbnailUrl: null,
  })
  const send = vi.spyOn(api, 'speechSubmit').mockResolvedValue(created)
  await show(); await chooseReference()
  expect(upload).toHaveBeenCalledOnce()
  expect(host.querySelector('audio[aria-label="Reference audio preview"]')?.getAttribute('src')).toBe('blob:reference')
  await submit()
  expect(send).toHaveBeenCalledExactlyOnceWith({
    text: 'こんにちは', caption: '', seconds: null, steps: 40, seed: 0,
    referenceInputId: 'reference-handle',
  }, REQUEST, 'csrf')
})

test('failed reference upload cannot silently generate without its reference', async () => {
  vi.mocked(api.speechDiscovery).mockResolvedValue({ available: true, referenceAvailable: true })
  vi.spyOn(api, 'uploadReferenceAudio').mockRejectedValue(new Error('Invalid reference WAV'))
  const send = vi.spyOn(api, 'speechSubmit')
  await show(); await chooseReference()
  expect(host.textContent).toContain('Invalid reference WAV')
  expect((generate() as HTMLButtonElement).disabled).toBe(true)
  await submit(); expect(send).not.toHaveBeenCalled()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Remove reference')!.click())
  expect((generate() as HTMLButtonElement).disabled).toBe(false)
})

test('late reference upload cannot cross account boundaries', async () => {
  vi.mocked(api.speechDiscovery).mockResolvedValue({ available: true, referenceAvailable: true })
  let finish!: (value: Awaited<ReturnType<typeof api.uploadReferenceAudio>>) => void
  vi.spyOn(api, 'uploadReferenceAudio').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  await show('owner-a'); await chooseReference(); await show('owner-b')
  await act(async () => finish({ id: 'private-reference', available: true, mimeType: 'audio/wav', thumbnailUrl: null }))
  expect(host.textContent).not.toContain('reference.wav')
  expect(host.querySelector('audio[aria-label="Reference audio preview"]')).toBeNull()
})
