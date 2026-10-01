// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ImageEditor, initialImageDraft, restoreImageDraft } from './App'
import { api, type Asset, type Discovery, type Workflow } from './api'
import { legalSeed, seedDomain } from './WorkflowImage'

const workflow: Workflow = { id: 'reference', kind: 'definition', name: 'Initial image', selectable: true, reason: null, definitionVersion: 1, definitionDigest: 'sha256:' + 'a'.repeat(64),
  image: { mode: 'img2img', profile: 'image-v1', dimensions: { mode: 'parameters' } }, parameters: {
    model: { type: 'string', role: 'checkpoint', required: true }, prompt: { type: 'string', role: 'positive_prompt', required: true },
    w: { type: 'integer', role: 'width', minimum: 64, maximum: 4096, multiple_of: 8 }, h: { type: 'integer', role: 'height', minimum: 64, maximum: 4096, multiple_of: 8 },
    random: { type: 'integer', role: 'seed', minimum: 8, maximum: 8, multiple_of: 4 }, strength: { type: 'number', role: 'denoise', minimum: 0, maximum: 1 }, source: { type: 'managed_input', role: 'initial_image' },
  } }
const asset: Asset = { id: 'asset-a', executionId: 'execution', displayName: 'Source.png', mimeType: 'image/png', mediaKind: 'image', sizeBytes: 100, width: 512, height: 512, createdAt: '', hasThumbnail: true, thumbnailUrl: '/thumb', previewUrl: '/preview', downloadUrl: '/download' }
const discovery: Discovery = { available: true, templates: ['text-to-image'], checkpoints: [{ id: 'checkpoint:model', name: 'model' }], loras: [], managedInputReady: true, workflows: [workflow] }
let host: HTMLDivElement, root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'assets').mockResolvedValue({ items: [asset], nextOffset: null })
  vi.spyOn(api, 'createInput').mockResolvedValue({ id: 'owned', available: true, thumbnailUrl: '/input-thumb', expiresAt: '2099-01-01T00:00:00Z' })
  vi.spyOn(api, 'input').mockResolvedValue({ id: 'owned', available: true, thumbnailUrl: '/input-thumb', expiresAt: '2099-01-01T00:00:00Z' })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const button = (text: string) => [...host.querySelectorAll('button')].find(b => b.textContent?.includes(text))!
async function click(text: string) { await act(async () => button(text).dispatchEvent(new MouseEvent('click', { bubbles: true }))) }
async function show(data = discovery, restore = false) {
  const submit = vi.fn()
  function Editor() {
    const [form, setForm] = useState(() => restoreImageDraft(initialImageDraft('model'), { positivePrompt: 'keep this prompt', seed: 8,
      ...(restore ? { workflowId: workflow.id, workflowKind: workflow.kind, definitionVersion: 1, definitionDigest: workflow.definitionDigest, referenceInputId: 'owned' } : {}) }))
    return <ImageEditor discovery={data} form={form} setForm={setForm} busy={false} csrf="csrf" onSubmit={submit} />
  }
  await act(async () => root.render(<Editor />))
  return submit
}
async function selectWorkflow() {
  const select = host.querySelector('select')!
  await act(async () => { select.value = 'reference'; select.dispatchEvent(new Event('change', { bubbles: true })) })
}

test('initial selection, attach, remove and replace obey both readiness gates', async () => {
  await show(); await selectWorkflow()
  expect(button('Generate image').disabled).toBe(true)
  expect(button('Choose from Assets').disabled).toBe(false)
  await click('Choose from Assets'); await click('Source.png')
  expect(api.createInput).toHaveBeenCalledWith('asset-a', 'csrf')
  expect(button('Generate image').disabled).toBe(false)
  await click('Remove reference')
  expect(button('Generate image').disabled).toBe(true)
  await click('Choose from Assets'); await click('Source.png')
  expect(button('Generate image').disabled).toBe(false)
  expect(host.querySelector('textarea')?.value).toBe('keep this prompt')
  expect(host.querySelector('input[type="file"]')).toBeNull()
})

test('missing infrastructure disables picker; empty Assets do not enable generate', async () => {
  await show({ ...discovery, managedInputReady: false }); await selectWorkflow()
  expect(button('Choose from Assets').disabled).toBe(true)
  expect(button('Generate image').disabled).toBe(true)
})

test('expired restored reference permits reselection and missing preview does not revoke it', async () => {
  vi.mocked(api.input).mockResolvedValueOnce({ id: 'owned', available: false, thumbnailUrl: null })
  await show(discovery, true)
  expect(button('Generate image').disabled).toBe(true)
  expect(button('Replace from Assets').disabled).toBe(false)
  vi.mocked(api.createInput).mockResolvedValue({ id: 'replacement', available: true, thumbnailUrl: '/input-thumb' })
  await click('Replace from Assets'); await click('Source.png')
  expect(button('Generate image').disabled).toBe(false)
  await act(async () => host.querySelector('img')!.dispatchEvent(new Event('error')))
  expect(host.textContent).toContain('Preview unavailable')
  expect(button('Generate image').disabled).toBe(false)
})

test('stale version stays blocked until explicit current selection', async () => {
  await show({ ...discovery, workflows: [{ ...workflow, definitionVersion: 2 }] }, true)
  expect(button('Generate image').disabled).toBe(true)
  expect(host.textContent).toContain('select a current ready version')
  await selectWorkflow()
  expect(button('Generate image').disabled).toBe(false)
})

test('seed domain sampling respects singleton, typed enum and empty domains', () => {
  expect(legalSeed({ type: 'integer', minimum: 8, maximum: 8, multiple_of: 4 })).toBe(8)
  expect(legalSeed({ type: 'integer', enum: [true, '8', 16], multiple_of: 4 })).toBe(16)
  expect(seedDomain({ type: 'integer', minimum: 1, maximum: 3, multiple_of: 8 })).toBeNull()
  expect(seedDomain({ type: 'integer', multiple_of: 0.5 })).toBeNull()
  expect(seedDomain({ type: 'integer' })?.count).toBe(1n << 53n)
})
