// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ImageEditor, initialImageDraft, restoreImageDraft } from './App'
import { api, type Asset, type Discovery, type Workflow } from './api'
import { legalSeed, seedDomain, validValue, workflowPayload } from './WorkflowImage'

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
  const submit = await show(); await selectWorkflow()
  expect(button('Generate image').disabled).toBe(true)
  expect(button('Choose from Assets').disabled).toBe(false)
  await click('Choose from Assets'); await click('Source.png')
  expect(api.createInput).toHaveBeenCalledWith('asset-a', 'csrf')
  expect(button('Generate image').disabled).toBe(false)
  expect(submit).not.toHaveBeenCalled()
  await click('Generate image')
  expect(submit).toHaveBeenCalledTimes(1)
  expect(submit.mock.calls[0][0]).toMatchObject({ workflowId: 'reference', definitionVersion: 1, definitionDigest: workflow.definitionDigest, referenceInputId: 'owned', positivePrompt: 'keep this prompt' })
  await click('Remove reference')
  expect(button('Generate image').disabled).toBe(true)
  await click('Choose from Assets'); await click('Source.png')
  expect(button('Generate image').disabled).toBe(false)
  expect(host.querySelector('textarea')?.value).toBe('keep this prompt')
  expect(host.querySelector('input[type="file"]')).toBeNull()
})

test('ready model domain and fixed dimensions remain explicit in the editor', async () => {
  await show({ ...discovery, checkpoints: [...discovery.checkpoints, { id: 'other', name: 'unverified' }], workflows: [{ ...workflow,
    image: { ...workflow.image, dimensions: { mode: 'fixed', width: 768, height: 1152 } },
    parameters: { ...workflow.parameters, model: { ...workflow.parameters.model, enum: ['model'] } } }] })
  await selectWorkflow()
  const model = host.querySelectorAll('select')[1]
  expect([...model.options].map(option => option.value)).not.toContain('unverified')
  const width = [...host.querySelectorAll('label')].find(label => label.textContent === 'width')!.querySelector('input')!
  expect(width.disabled).toBe(true)
  expect(width.value).toBe('768')
  expect(button('512 × 512').disabled).toBe(true)
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

test.each(['sampler', 'scheduler'])('selected builtin %s applies its advertised full string pattern', role => {
  const selected: Workflow = { ...workflow, image: { ...workflow.image, mode: 'txt2img', dimensions: { mode: 'fixed', width: 512, height: 512 } },
    parameters: { model: workflow.parameters.model, prompt: workflow.parameters.prompt,
      token: { type: 'string', role, pattern: '^[a-zA-Z0-9_]+$', max_length: 80 } } }
  const form = { ...initialImageDraft('model'), positivePrompt: 'x' }
  expect(workflowPayload(form, selected)).not.toBeNull()
  for (const value of ['', 'euler invalid', 'euler\n', 'euler/invalid']) {
    expect(workflowPayload({ ...form, [role]: value }, selected)).toBeNull()
  }
})

test('string patterns require complete matches and invalid patterns fail closed', () => {
  expect(validValue({ type: 'string', pattern: '[a-z]+' }, 'valid')).toBe(true)
  for (const value of ['!valid', 'valid!', 'valid\n']) expect(validValue({ type: 'string', pattern: '[a-z]+' }, value)).toBe(false)
  expect(validValue({ type: 'string', pattern: '[' }, 'valid')).toBe(false)
})


test.each(['513', '', 'invalid'])('fixed dimensions override stale draft %s in submitted payload', async stale => {
  const fixed: Workflow = { ...workflow, image: { ...workflow.image, mode: 'txt2img', dimensions: { mode: 'fixed', width: 768, height: 1152 } },
    parameters: { model: workflow.parameters.model, prompt: workflow.parameters.prompt } }
  const form = { ...initialImageDraft('model'), positivePrompt: 'keep this prompt', width: stale, height: stale }
  expect(workflowPayload(form, fixed)).toMatchObject({ width: 768, height: 1152, positivePrompt: 'keep this prompt' })
  expect(form.width).toBe(stale)
  const submit = vi.fn()
  await act(async () => root.render(<ImageEditor discovery={{ ...discovery, workflows: [fixed] }}
    form={{ ...form, workflowId: fixed.id, workflowKind: fixed.kind, definitionVersion: fixed.definitionVersion, definitionDigest: fixed.definitionDigest }}
    setForm={vi.fn()} busy={false} csrf="csrf" onSubmit={submit} />))
  const width = [...host.querySelectorAll('label')].find(label => label.textContent === 'width')!.querySelector('input')!
  expect(width.disabled).toBe(true)
  expect(width.value).toBe('768')
  expect(button('Generate image').disabled).toBe(false)
  await click('Generate image')
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ width: 768, height: 1152 }))
})


test.each(['', 'invalid'])('inactive numeric and sampler drafts are omitted while preserving draft %s', async stale => {
  const fixed: Workflow = { ...workflow, image: { ...workflow.image, mode: 'txt2img', dimensions: { mode: 'fixed', width: 512, height: 512 } },
    parameters: { model: workflow.parameters.model, prompt: workflow.parameters.prompt } }
  const form = { ...initialImageDraft('model'), positivePrompt: 'keep this prompt', steps: stale, cfg: stale, denoise: stale, seed: stale, sampler: '', scheduler: '' }
  const payload = workflowPayload(form, fixed)!
  for (const field of ['steps', 'cfg', 'denoise', 'seed', 'sampler', 'scheduler']) expect(payload).not.toHaveProperty(field)
  expect(form.steps).toBe(stale)
  const submit = vi.fn()
  await act(async () => root.render(<ImageEditor discovery={{ ...discovery, workflows: [fixed] }}
    form={{ ...form, workflowId: fixed.id, workflowKind: fixed.kind, definitionVersion: fixed.definitionVersion, definitionDigest: fixed.definitionDigest }}
    setForm={vi.fn()} busy={false} csrf="csrf" onSubmit={submit} />))
  const steps = [...host.querySelectorAll('label')].find(label => label.textContent === 'steps')!.querySelector('input')!
  expect(steps.disabled).toBe(true)
  expect(button('Generate image').disabled).toBe(false)
  await click('Generate image')
  expect(submit).toHaveBeenCalledWith(expect.objectContaining(payload))
  for (const field of ['steps', 'cfg', 'denoise', 'seed', 'sampler', 'scheduler']) expect(submit.mock.calls[0][0]).not.toHaveProperty(field)
})

test('optional scalar defaults are materialized in payload; required and invalid controls remain blocked', () => {
  const selected: Workflow = { ...workflow, image: { ...workflow.image, mode: 'txt2img', dimensions: { mode: 'fixed', width: 512, height: 512 } },
    parameters: { model: workflow.parameters.model, prompt: workflow.parameters.prompt,
      count: { type: 'integer', role: 'steps', required: false, default: 12, minimum: 1, maximum: 30 },
      guidance: { type: 'number', role: 'cfg', required: false, default: 4, minimum: 0, maximum: 100 } } }
  const form = { ...initialImageDraft('model'), positivePrompt: 'x', steps: '', cfg: '' }
  expect(workflowPayload(form, selected)).toMatchObject({ steps: 12, cfg: 4 })
  expect(workflowPayload({ ...form, steps: 'invalid' }, selected)).toBeNull()
  selected.parameters.count.required = true
  expect(workflowPayload(form, selected)).toBeNull()
  expect(form.steps).toBe('')
})
