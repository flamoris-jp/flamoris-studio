// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ImageEditor, initialImageDraft, type ImageDraft } from './App'
import { api, type Discovery, type Workflow } from './api'

const discovery: Discovery = { available: true, templates: ['text-to-image'],
  checkpoints: [{ id: 'checkpoint:model', name: 'model' }], loras: [] }
let root: Root, host: HTMLDivElement
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const generate = () => [...host.querySelectorAll('button')].find(b => b.textContent?.includes('Generate image') || b.textContent === 'Submitting…')!
const feedback = () => host.querySelector<HTMLElement>('[aria-label="Image generation needs attention"]')
async function click() { await act(async () => generate().click()) }
async function edit(label: string, value: string) {
  const field = [...host.querySelectorAll('label')].find(l => l.textContent?.startsWith(label))!.querySelector('input,textarea')! as HTMLInputElement | HTMLTextAreaElement
  await act(async () => {
    const prototype = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function show(draft: ImageDraft, data = discovery, busy = false) {
  const submit = vi.fn()
  function Editor() {
    const [form, setForm] = useState(draft)
    return <ImageEditor discovery={data} form={form} setForm={setForm} csrf="csrf" busy={busy} onSubmit={submit} />
  }
  await act(async () => root.render(<Editor />))
  return submit
}

test('Ready accepts a click and explains invalid native-required and numeric fields; correction never auto-submits', async () => {
  const submit = await show({ ...initialImageDraft('model'), width: '511', seed: '-1', scheduler: 'bad scheduler' })
  expect(host.textContent).toContain('Service ready')
  expect(generate().disabled).toBe(false)
  expect(host.querySelector('form')!.noValidate).toBe(true)
  expect(host.querySelector('textarea')!.checkValidity()).toBe(false)
  expect(feedback()).toBeNull()
  await click()
  expect(feedback()?.textContent).toContain('Positive prompt:')
  expect(feedback()?.textContent).toContain('Width: enter a whole number from 64 to 4096 in multiples of 8')
  expect(feedback()?.textContent).toContain('Seed:')
  expect(feedback()?.textContent).toContain('Scheduler:')
  expect(document.activeElement).toBe(feedback())
  expect(submit).not.toHaveBeenCalled()
  await edit('Positive prompt', 'city at night')
  await edit('width', '512')
  await edit('Seed', '')
  await edit('Scheduler', 'normal')
  expect(feedback()).toBeNull()
  expect(submit).not.toHaveBeenCalled()
  await click()
  expect(submit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ positivePrompt: 'city at night', width: 512, seed: undefined }))
})

test('unavailable model and LoRA produce actionable explanations without discarding the draft', async () => {
  const submit = await show({ ...initialImageDraft('missing'), positivePrompt: 'keep this', loras: [{ name: 'missing', strengthModel: '1', strengthClip: '1' }] })
  await click()
  expect(feedback()?.textContent).toContain('Model: choose an installed image model')
  expect(feedback()?.textContent).toContain('LoRA layers: a selected model is unavailable')
  expect(host.querySelector('textarea')!.value).toBe('keep this')
  expect(submit).not.toHaveBeenCalled()
})

test('template-specific custom controls name their label and range', async () => {
  const item: Workflow = { id: 'comfy-' + 'a'.repeat(64), name: 'Editor', kind: 'registered-comfy', selectable: true, reason: null,
    recipeSchema: 10, definitionVersion: 1, definitionDigest: 'a'.repeat(64), referenceRoles: [],
    image: { mode: 'declared', dimensions: { mode: 'graph' }, profile: 'declared-comfy-v1' },
    parameters: { intensity: { type: 'number', label: 'Edit intensity', role: 'parameter', minimum: 0, maximum: 1, required: true } } }
  const submit = await show({ ...initialImageDraft(), workflowId: item.id, workflowKind: item.kind, definitionVersion: 1, definitionDigest: item.definitionDigest,
    additionalParameters: { intensity: 2 } }, { ...discovery, checkpoints: [], workflows: [item] })
  await click()
  expect(feedback()?.textContent).toContain('Edit intensity: enter a number from 0 to 1')
  expect(submit).not.toHaveBeenCalled()
  await edit('Edit intensity', '0.5')
  expect(feedback()).toBeNull()
  expect(submit).not.toHaveBeenCalled()
  await click()
  expect(submit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ additionalParameters: { intensity: 0.5 } }))
})

test('an in-flight submission remains disabled and an explicit form event cannot submit again', async () => {
  const submit = await show({ ...initialImageDraft('model'), positivePrompt: 'ready' }, discovery, true)
  expect(generate().disabled).toBe(true)
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(submit).not.toHaveBeenCalled()
  expect(feedback()).toBeNull()
})

test('keyboard form submission explains errors and Clear inputs dismisses them', async () => {
  const submit = await show({ ...initialImageDraft('model'), positivePrompt: 'keep', height: '65' })
  await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(feedback()?.textContent).toContain('Height:')
  expect(submit).not.toHaveBeenCalled()
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Clear inputs')!.click())
  expect(feedback()).toBeNull()
  expect(generate().disabled).toBe(false)
})
