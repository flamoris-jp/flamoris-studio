// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Music, { initialMusicDraft, musicPayload } from './Music'
import { api, HTTPFailure, type Asset, type Execution } from './api'

const REQUEST = '00000000-0000-4000-8000-000000000001'
const created: Execution = { id: '00000000-0000-4000-8000-000000000002', category: 'music', operation: 'music.generate', state: 'queued', submittedAt: '', assets: [] }
let host: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', { randomUUID: () => REQUEST })
  sessionStorage.clear(); window.location.hash = ''
  vi.spyOn(api, 'musicDiscovery').mockResolvedValue({ generate: true, transcribe: true })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); sessionStorage.clear(); window.location.hash = ''; vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function show(owner = 'owner-a', observer = vi.fn()) {
  function Editor() {
    const [execution, setExecution] = useState<Execution | null>(null)
    return <Music csrf="csrf" accountKey={owner} execution={execution} onExecution={value => { observer(value); setExecution(value) }} />
  }
  await act(async () => root.render(<Editor key={owner} />))
  return observer
}
async function value(element: HTMLTextAreaElement | HTMLSelectElement, text: string) {
  await act(async () => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, text)
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}
const button = (text: string) => [...host.querySelectorAll('button')].find(item => item.textContent === text)!
async function submit() { await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))) }

test('Music validates bounded text, both seeds, duration and steps', () => {
  const draft = { ...initialMusicDraft(), style: 'カリビアン', seed: String(Number.MAX_SAFE_INTEGER) }
  expect(musicPayload(draft)?.seed).toBe(Number.MAX_SAFE_INTEGER)
  for (const changes of [{ style: ' ' }, { style: '\u0000' }, { style: 'x'.repeat(1025) }, { lyrics: 'x'.repeat(4097) }, { seconds: 'Infinity' }, { seconds: '121' }, { steps: '2.5' }, { seed: String(2 ** 53) }, { lm_seed: '-1' }]) expect(musicPayload({ ...draft, ...changes })).toBeNull()
  expect(musicPayload({ ...draft, style: '😀'.repeat(1024) })).not.toBeNull()
  expect(musicPayload({ ...draft, seed: '', lm_seed: '' })).toMatchObject({ seed: null, lm_seed: null })
})

test('both duration fields allow contract-supported fractional seconds', async () => {
  await show()
  const duration = host.querySelector('input[type="number"]') as HTMLInputElement
  duration.value = '1.5'
  expect(duration.step).toBe('any'); expect(duration.checkValidity()).toBe(true)
  await value(host.querySelector('select')!, 'transcribe')
  const maximum = host.querySelector('input[type="number"]') as HTMLInputElement
  maximum.value = '1.5'
  expect(maximum.step).toBe('any'); expect(maximum.checkValidity()).toBe(true)
})

test('unavailable Music preserves its draft and never submits', async () => {
  vi.mocked(api.musicDiscovery).mockResolvedValue({ generate: false, transcribe: false })
  const send = vi.spyOn(api, 'musicSubmit')
  await show(); await value(host.querySelector('textarea')!, 'calypso')
  expect((button('Generate music') as HTMLButtonElement).disabled).toBe(true)
  await submit(); expect(send).not.toHaveBeenCalled(); expect(host.querySelector('textarea')!.value).toBe('calypso')
})

test('one acknowledged Music request retains only its UUID and blocks duplicate submission', async () => {
  const send = vi.spyOn(api, 'musicSubmit').mockResolvedValue(created)
  const observed = await show(); await value(host.querySelector('textarea')!, 'calypso'); await submit()
  expect(send).toHaveBeenCalledExactlyOnceWith({ style: 'calypso', lyrics: '', seconds: 30, steps: 32, seed: 0, lm_seed: 0 }, 'generate', REQUEST, 'csrf')
  expect(observed).toHaveBeenCalledWith(created); expect(sessionStorage.getItem('flamoris.music.pending:owner-a')).toBe(REQUEST)
  expect(sessionStorage.length).toBe(1); expect(window.location.hash).toBe(`#execution/${created.id}`)
  await submit(); expect(send).toHaveBeenCalledOnce()
})

test('ambiguous request recovers through owner-checked lookup without replay or private draft storage', async () => {
  const send = vi.spyOn(api, 'musicSubmit').mockRejectedValue(new Error('lost receipt'))
  const lookup = vi.spyOn(api, 'musicRequest').mockResolvedValue({ ...created, state: 'submission_unknown' })
  await show(); await value(host.querySelector('textarea')!, 'private lyrics'); await submit()
  expect(sessionStorage.getItem('flamoris.music.pending:owner-a')).toBe(REQUEST)
  expect((button('Generate music') as HTMLButtonElement).disabled).toBe(true)
  await act(async () => root.render(<div />)); const observed = await show()
  expect(lookup).toHaveBeenCalledExactlyOnceWith(REQUEST); expect(observed).toHaveBeenCalledWith({ ...created, state: 'submission_unknown' }); expect(send).toHaveBeenCalledOnce()
})

test('validation allows correction and account-switch discards late Music receipts', async () => {
  const send = vi.spyOn(api, 'musicSubmit').mockRejectedValue(new HTTPFailure('Invalid style', 422))
  await show(); await value(host.querySelector('textarea')!, 'calypso'); await submit()
  expect(sessionStorage.length).toBe(0); expect((button('Generate music') as HTMLButtonElement).disabled).toBe(false)
  let resolve!: (execution: Execution) => void
  send.mockImplementation(() => new Promise(value => { resolve = value }))
  const old = await show('old-owner'); await value(host.querySelector('textarea')!, 'calypso'); await submit()
  const next = await show('new-owner'); await act(async () => resolve(created))
  expect(old).not.toHaveBeenCalled(); expect(next).not.toHaveBeenCalled(); expect(window.location.hash).toBe('')
})

test('transcription accepts only selected WAV Assets and never passes raw asset paths', async () => {
  const wav = { id: 'owned-audio', displayName: 'recording.wav', mediaKind: 'audio', mimeType: 'audio/wav' } as Asset
  vi.spyOn(api, 'assets').mockResolvedValue({ items: [wav, { ...wav, id: 'other', mimeType: 'audio/mpeg' }], nextOffset: null })
  const attach = vi.spyOn(api, 'createInput').mockResolvedValue({ id: REQUEST, available: true, thumbnailUrl: null })
  const send = vi.spyOn(api, 'musicSubmit').mockResolvedValue({ ...created, operation: 'music.transcribe' })
  await show(); await value(host.querySelector('select')!, 'transcribe')
  await act(async () => button('Load audio Assets').click())
  const selection = host.querySelectorAll('select')[1]
  expect(selection.querySelectorAll('option')).toHaveLength(2)
  await value(selection, wav.id); await act(async () => button('Attach selected audio').click())
  expect(attach).toHaveBeenCalledExactlyOnceWith(wav.id, 'csrf')
  await submit()
  expect(send).toHaveBeenCalledExactlyOnceWith({ referenceInputId: REQUEST, max_seconds: 30, melody_only: false }, 'transcribe', REQUEST, 'csrf')
})
