// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Video, { videoDraft, videoPayload } from './Video'
import { api, HTTPFailure, type Execution, type VideoWorkflow } from './api'

const REQUEST = '00000000-0000-4000-8000-000000000001'
const HANDLE = '00000000-0000-4000-8000-000000000002'
const item: VideoWorkflow = { id: 'comfy-' + 'd'.repeat(64), name: 'Test video', kind: 'registered-comfy', definitionVersion: 1, definitionDigest: 'd'.repeat(64), recipeSchema: 11,
  video: { profile: 'wan-video-v1', mode: 'i2v' }, selectable: true, reason: null, referenceRoles: ['start_image'], parameters: {
    start_image: { type: 'string', role: 'managed_input', label: 'Start', required: true, pattern: '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' },
    motion: { type: 'string', role: 'prompt', label: 'Motion', required: false, default: 'One step', min_length: 1, max_length: 20000 },
    width: { type: 'integer', role: 'parameter', default: 768, minimum: 64, maximum: 1024, multiple_of: 16 },
    height: { type: 'integer', role: 'parameter', default: 432, minimum: 64, maximum: 1024, multiple_of: 16 },
    frames: { type: 'integer', role: 'parameter', default: 81, minimum: 5, maximum: 161 },
    fps: { type: 'integer', role: 'parameter', default: 16, minimum: 1, maximum: 32 },
    seed: { type: 'integer', role: 'parameter', default: 0, minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
  } }
const queued: Execution = { id: '00000000-0000-4000-8000-000000000003', category: 'video', operation: 'video.generate', state: 'queued', submittedAt: '', assets: [] }
function h3Item(): VideoWorkflow {
  const value = structuredClone(item)
  value.video.profile = 'minimax-h3-video-v1'
  value.parameters.width = { ...value.parameters.width, default: 608, multiple_of: 32 }
  value.parameters.height = { ...value.parameters.height, default: 352, multiple_of: 32 }
  value.parameters.frames = { ...value.parameters.frames, default: 124 }
  value.parameters.fps = { ...value.parameters.fps, default: 24, minimum: 24, maximum: 24 }
  return value
}

test('H3 accepts 124 frames at 24fps and rejects Wan frame counts and geometry', () => {
  const value = h3Item(), form = { ...videoDraft(value), imageInputs: { start_image: HANDLE } }
  expect(videoPayload(form, value)?.additionalParameters.frames).toBe(124)
  for (const changes of [{ frames: 81 }, { width: 624 }, { fps: 16 }]) {
    expect(videoPayload({ ...form, additionalParameters: { ...form.additionalParameters, ...changes } }, value)).toBeNull()
  }
})

test('H3 form shows the 17-frame step and silent output guidance', async () => {
  vi.mocked(api.videoDiscovery).mockResolvedValue({ available: true, workflows: [h3Item()], managedInputReady: true })
  await show()
  expect(host.textContent).toContain('5.1667 seconds')
  expect(host.textContent).toContain('silent video')
  expect(host.querySelector('input[step="17"]')).not.toBeNull()
})
let host: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('crypto', { randomUUID: () => REQUEST, getRandomValues: (words: Uint32Array) => { words[0] = 1; words[1] = 2; return words } })
  sessionStorage.clear(); window.location.hash = ''
  vi.spyOn(api, 'videoDiscovery').mockResolvedValue({ available: true, workflows: [structuredClone(item)], managedInputReady: true })
  vi.spyOn(api, 'uploadInput').mockResolvedValue({ id: HANDLE, available: true, thumbnailUrl: '/private-input' })
  vi.spyOn(api, 'input').mockResolvedValue({ id: HANDLE, available: true, thumbnailUrl: '/private-input' })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); sessionStorage.clear(); window.location.hash = ''; vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function show(owner = 'owner-a', observer = vi.fn(), initial: Execution | null = null) {
  function Editor() {
    const [execution, setExecution] = useState<Execution | null>(initial)
    return <Video csrf="csrf" accountKey={owner} execution={execution} onExecution={value => { observer(value); setExecution(value) }} />
  }
  await act(async () => root.render(<Editor key={owner} />))
  return observer
}
const button = (text: string) => [...host.querySelectorAll('button')].find(value => value.textContent === text) as HTMLButtonElement
async function change(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  await act(async () => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function upload(index = 0) {
  const picker = host.querySelectorAll<HTMLInputElement>('input[type="file"]')[index]
  Object.defineProperty(picker, 'files', { value: [new File(['fixture'], 'start.webp', { type: 'image/webp' })], configurable: true })
  await act(async () => picker.dispatchEvent(new Event('change', { bubbles: true })))
}
async function submit() { await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))) }

test('video payload keeps image roles separate and validates exact clip budgets', () => {
  const form = { ...videoDraft(item), imageInputs: { start_image: HANDLE } }
  expect(videoPayload(form, item)).toMatchObject({ imageInputs: { start_image: HANDLE }, additionalParameters: { width: 768, height: 432, frames: 81, fps: 16 } })
  expect(videoPayload(form, item)?.additionalParameters).not.toHaveProperty('seed')
  for (const changes of [{ width: 769 }, { frames: 80 }, { fps: 8 }, { fps: 0 }, { width: 1024, height: 1024 }, { seed: 2 ** 53 }, { seed: true }, { start_image: HANDLE }]) {
    expect(videoPayload({ ...form, additionalParameters: { ...form.additionalParameters, ...changes } }, item)).toBeNull()
  }
  expect(videoPayload({ ...form, imageInputs: { ...form.imageInputs, end_image: HANDLE } }, item)).toBeNull()
  expect(videoPayload({ ...form, definitionDigest: 'f'.repeat(64) }, item)).toBeNull()
})

test('image upload enables one request with actual duration and no private browser storage', async () => {
  const send = vi.spyOn(api, 'videoSubmit').mockResolvedValue(queued)
  const observer = await show()
  await explainBlockedAction()
  expect(host.textContent).toContain('5.0625 seconds')
  await upload()
  expect(button('Generate video').disabled).toBe(false)
  await submit(); await submit()
  expect(send).toHaveBeenCalledOnce()
  expect(send.mock.calls[0]).toEqual([videoPayload({ ...videoDraft(item), imageInputs: { start_image: HANDLE } }, item), REQUEST, 'csrf'])
  expect(observer).toHaveBeenCalledWith(queued)
  expect(sessionStorage.length).toBe(1); expect(sessionStorage.getItem('flamoris.video.pending:owner-a')).toBe(REQUEST)
  expect(window.location.hash).toBe(`#execution/${queued.id}`)
})

test('unavailable Video keeps the motion draft when checking again', async () => {
  await show(); await change(host.querySelector('textarea')!, 'Keep this motion')
  vi.mocked(api.videoDiscovery).mockResolvedValue({ available: false, workflows: [{ ...item, selectable: false, reason: 'reference_models_unavailable' }], managedInputReady: true })
  const send = vi.spyOn(api, 'videoSubmit')
  await act(async () => button('Check again').click())
  expect(host.querySelector('textarea')!.value).toBe('Keep this motion')
  await explainBlockedAction()
  await submit(); expect(send).not.toHaveBeenCalled()
})

test('FLF2V requires a separately attached end image and never silently discards it', async () => {
  const flf = structuredClone(item); flf.video.mode = 'flf2v'; flf.referenceRoles = ['start_image', 'end_image']; flf.parameters.end_image = { ...flf.parameters.start_image }
  vi.mocked(api.videoDiscovery).mockResolvedValue({ available: true, workflows: [flf], managedInputReady: true })
  const send = vi.spyOn(api, 'videoSubmit').mockResolvedValue(queued)
  await show(); expect(host.textContent).toContain('End image')
  await upload(0); await explainBlockedAction()
  await upload(1); expect(button('Generate video').disabled).toBe(false)
  await submit(); expect(send.mock.calls[0][0].imageInputs).toEqual({ start_image: HANDLE, end_image: HANDLE })
})

test('ambiguous submission recovers only through the existing request and never repeats the POST', async () => {
  const send = vi.spyOn(api, 'videoSubmit').mockRejectedValue(new Error('lost receipt'))
  const recover = vi.spyOn(api, 'videoRequest').mockResolvedValue({ ...queued, state: 'submission_unknown' })
  await show(); await upload(); await submit()
  await explainBlockedAction()
  await act(async () => root.render(<div />)); const observer = await show()
  expect(recover).toHaveBeenCalledExactlyOnceWith(REQUEST)
  expect(observer).toHaveBeenCalledWith({ ...queued, state: 'submission_unknown' })
  expect(send).toHaveBeenCalledOnce()
})

test('validation allows correction while a late receipt cannot cross accounts', async () => {
  const send = vi.spyOn(api, 'videoSubmit').mockRejectedValue(new HTTPFailure('Invalid video', 422))
  await show(); await upload(); await submit()
  expect(sessionStorage.length).toBe(0); expect(button('Generate video').disabled).toBe(false)
  let resolve!: (value: Execution) => void
  send.mockImplementation(() => new Promise(value => { resolve = value }))
  const previous = await show('previous'); await upload(); await submit()
  const next = await show('next'); await act(async () => resolve(queued))
  expect(previous).not.toHaveBeenCalled(); expect(next).not.toHaveBeenCalled(); expect(window.location.hash).toBe('')
})

test('completed result uses the owned video player and download routes', async () => {
  const completed: Execution = { ...queued, state: 'completed', assets: [{ id: 'owned', executionId: queued.id, displayName: 'video.mp4', mimeType: 'video/mp4', mediaKind: 'video', sizeBytes: 100, width: null, height: null, createdAt: '', hasThumbnail: false, previewUrl: '/api/executions/owned/assets/movie/preview', downloadUrl: '/api/executions/owned/assets/movie/download', thumbnailUrl: '' }] }
  await show('owner-a', vi.fn(), completed)
  expect(host.querySelector('video')!.getAttribute('src')).toBe(completed.assets[0].previewUrl)
  expect(host.querySelector('a')!.getAttribute('href')).toBe(completed.assets[0].downloadUrl)
})

test('an expired or failed reference cannot enable generation', async () => {
  vi.mocked(api.input).mockResolvedValue({ id: HANDLE, available: false, thumbnailUrl: null })
  const send = vi.spyOn(api, 'videoSubmit')
  await show(); await upload(); await explainBlockedAction()
  await submit(); expect(send).not.toHaveBeenCalled()
})

test('invalid frame count explains the field on click without an identity; correction requires another click', async () => {
  const send = vi.spyOn(api, 'videoSubmit').mockResolvedValue(queued)
  await show(); await upload()
  const identity = vi.spyOn(crypto, 'randomUUID')
  const frames = [...host.querySelectorAll('label')].find(label => label.textContent?.startsWith('Frames') && !label.textContent.startsWith('Frames per second'))!.querySelector('input')!
  await change(frames, '82')
  expect(frames.checkValidity()).toBe(false)
  await act(async () => button('Generate video').click())
  expect(host.querySelector('[aria-label="Action needs attention"]')?.textContent).toContain('Frames: use 5, 9, 13')
  expect(send).not.toHaveBeenCalled(); expect(identity).not.toHaveBeenCalled(); expect(sessionStorage.length).toBe(0)
  await change(frames, '81')
  expect(host.querySelector('[aria-label="Action needs attention"]')).toBeNull()
  expect(send).not.toHaveBeenCalled()
  await act(async () => button('Generate video').click())
  expect(send).toHaveBeenCalledOnce(); expect(identity).toHaveBeenCalledOnce()
})

async function explainBlockedAction() {
  const action = button('Generate video') as HTMLButtonElement
  expect(action.disabled).toBe(false)
  const before = vi.isMockFunction(api.videoSubmit) ? vi.mocked(api.videoSubmit).mock.calls.length : null
  await act(async () => action.click())
  expect(host.querySelector('[aria-label="Action needs attention"]')).not.toBeNull()
  expect(host.textContent).toContain('No request was sent.')
  if (before !== null) expect(api.videoSubmit).toHaveBeenCalledTimes(before)
}
