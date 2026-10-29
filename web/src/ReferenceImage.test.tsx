// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ImageEditor, initialImageDraft, restoreImageDraft, type ImageDraft } from './App'
import { api, type Discovery, type Workflow } from './api'

const roles = ['initial_image', 'character_image', 'style_image', 'control_image']
const fields = ['referenceInputId', 'characterInputId', 'styleInputId', 'controlInputId']
const ids = roles.map((_, i) => `${i + 1}0000000-0000-4000-8000-000000000000`)
const reference: Workflow = {
  id: 'comfy-' + 'a'.repeat(64), kind: 'registered-comfy', name: 'Character, style and pose',
  definitionVersion: 1, definitionDigest: 'a'.repeat(64), selectable: true, reason: null,
  referenceRoles: roles, liveProviderVerified: false, recipeSchema: 8,
  image: { mode: 'img2img', profile: 'checkpoint-reference-v1', dimensions: { mode: 'parameters' } },
  parameters: {
    checkpoint: { type: 'string', role: 'checkpoint', default: 'model', enum: ['model'] },
    positive_prompt: { type: 'string', role: 'positive_prompt', required: true },
    width: { type: 'integer', role: 'width', minimum: 64, maximum: 4096, multiple_of: 8 },
    height: { type: 'integer', role: 'height', minimum: 64, maximum: 4096, multiple_of: 8 },
    seed: { type: 'integer', role: 'seed', minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
    denoise: { type: 'number', role: 'denoise', minimum: 0, maximum: 1 },
    character_strength: { type: 'number', minimum: 0, maximum: 5, default: 0.8 },
    style_strength: { type: 'number', minimum: 0, maximum: 5, default: 0.6 },
    control_strength: { type: 'number', minimum: 0, maximum: 2, default: 1 },
    ...Object.fromEntries(roles.map(role => [role, { type: 'string', role, required: true,
      pattern: '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' }])),
  },
}
const builtin: Workflow = { ...reference, id: 'text-to-image', kind: 'builtin', name: 'Text only',
  definitionVersion: null, definitionDigest: null, referenceRoles: [], recipeSchema: 1,
  image: { mode: 'txt2img', profile: 'image-v1', dimensions: { mode: 'parameters' } },
  parameters: Object.fromEntries(Object.entries(reference.parameters).filter(([, s]) => s.role && !roles.includes(s.role))) }
const discovery: Discovery = { available: true, managedInputReady: true, templates: [],
  checkpoints: [{ id: 'checkpoint:model', name: 'model' }], loras: [], workflows: [reference, builtin] }
let root: Root, host: HTMLDivElement
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'input').mockImplementation(async id => ({ id, available: true, thumbnailUrl: '/thumb/' + id }))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const button = (text: string) => [...host.querySelectorAll('button')].find(item => item.textContent?.includes(text))!
async function show(missing = false) {
  const submit = vi.fn()
  function Editor() {
    const [form, setForm] = useState<ImageDraft>(() => restoreImageDraft(initialImageDraft('model'), {
      workflowId: reference.id, workflowKind: reference.kind, definitionVersion: 1, definitionDigest: reference.definitionDigest,
      positivePrompt: 'same character in a new scene', seed: 42,
      additionalParameters: { character_strength: 0.45, style_strength: 0.3, control_strength: 0.9 },
      ...Object.fromEntries(fields.map((field, i) => [field, missing && i === 2 ? undefined : ids[i]])),
    }))
    return <ImageEditor discovery={discovery} form={form} setForm={setForm} csrf="csrf" busy={false} onSubmit={submit} />
  }
  await act(async () => root.render(<Editor />))
  return submit
}

test('four references restore independently and submit opaque handles with their influence', async () => {
  const submit = await show()
  expect(host.textContent).toContain('Character reference')
  expect(host.textContent).toContain('Visual style reference')
  expect(host.textContent).toContain('prepared pose, depth or edge map')
  expect(host.querySelectorAll('img')).toHaveLength(4)
  expect(button('Generate image').disabled).toBe(false)
  await act(async () => button('Generate image').click())
  expect(submit).toHaveBeenCalledTimes(1)
  expect(submit.mock.calls[0][0]).toMatchObject({
    workflowId: reference.id, definitionDigest: reference.definitionDigest,
    referenceInputId: ids[0], characterInputId: ids[1], styleInputId: ids[2], controlInputId: ids[3],
    additionalParameters: { character_strength: 0.45, style_strength: 0.3, control_strength: 0.9 },
  })
})

test('missing or expired style reference blocks generation without removing the other references', async () => {
  const submit = await show(true)
  expect(button('Generate image').disabled).toBe(false)
  await act(async () => button('Generate image').click())
  expect(host.querySelector('[aria-label="Image generation needs attention"]')?.textContent).toContain('Visual style reference')
  expect(submit).not.toHaveBeenCalled()
  expect(host.querySelectorAll('img')).toHaveLength(3)
  expect(host.textContent).toContain('Choose an available image for every reference')
  await act(async () => root.unmount())
  root = createRoot(host)
  vi.mocked(api.input).mockImplementation(async id => ({ id, available: id !== ids[2], thumbnailUrl: '/thumb/' + id }))
  const expiredSubmit = await show()
  expect(button('Generate image').disabled).toBe(false)
  await act(async () => button('Generate image').click())
  expect(host.querySelector('[aria-label="Image generation needs attention"]')?.textContent).toContain('Visual style reference')
  expect(host.querySelectorAll('img')).toHaveLength(4)
  expect(expiredSubmit).not.toHaveBeenCalled()
})

test('switching to text only keeps all attachments visible and prevents silent reference loss', async () => {
  const submit = await show()
  const select = host.querySelector('select')!
  await act(async () => { select.value = builtin.id; select.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(host.querySelectorAll('img')).toHaveLength(4)
  expect(host.textContent).toContain('does not use every attached reference')
  expect(button('Generate image').disabled).toBe(false)
  await act(async () => button('Generate image').click())
  expect(host.querySelector('[aria-label="Image generation needs attention"]')?.textContent).toContain('Reference images')
  expect(submit).not.toHaveBeenCalled()
})

test('Clear inputs removes every role from the draft and preserves stored reference data', async () => {
  await show()
  const deletion = vi.spyOn(api, 'deleteInput')
  await act(async () => button('Clear inputs').click())
  expect(host.querySelectorAll('img')).toHaveLength(0)
  expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('')
  expect(deletion).not.toHaveBeenCalled()
})
