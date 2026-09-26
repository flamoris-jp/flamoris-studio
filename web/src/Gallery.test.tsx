// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Gallery from './Gallery'
import { api, type Asset } from './api'

const item = (id: string): Asset => ({
  id, executionId: 'execution', displayName: id + '.png',
  createdAt: '2026-09-24T00:00:00Z', mediaKind: 'image', mimeType: 'image/png',
  sizeBytes: 1024, width: 32, height: 24, hasThumbnail: false,
  previewUrl: '/preview/' + id, thumbnailUrl: '/thumbnail/' + id, downloadUrl: '/download/' + id,
})

let root: Root | undefined
let host: HTMLDivElement | undefined
beforeEach(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))

async function show(ids: string[]) {
  vi.spyOn(api, 'assets').mockResolvedValue({ items: ids.map(item), nextOffset: null })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => { root!.render(<Gallery csrf="csrf" />) })
  return host
}

async function click(element: Element) {
  await act(async () => { element.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
}

afterEach(async () => {
  if (root) await act(async () => { root!.unmount() })
  host?.remove()
  root = undefined
  host = undefined
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('selection and confirmation keep failures visible and selected for retry', async () => {
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValue(true)
  vi.stubGlobal('confirm', confirm)
  const remove = vi.spyOn(api, 'deleteAssets').mockResolvedValue({
    results: [{ id: 'a', deleted: true }, { id: 'b', deleted: false, error: 'unavailable' }],
  })
  const view = await show(['a', 'b'])
  for (const box of view.querySelectorAll('article input[type="checkbox"]')) await click(box)
  const button = view.querySelector('button')!
  expect(button.textContent).toContain('(2)')
  await click(button)
  expect(confirm).toHaveBeenCalledOnce()
  expect(remove).not.toHaveBeenCalled()
  await click(button)
  expect(remove).toHaveBeenCalledWith(['a', 'b'], 'csrf')
  expect(view.querySelectorAll('article')).toHaveLength(1)
  expect(view.querySelector('article strong')?.textContent).toBe('b.png')
  expect((view.querySelector('article input') as HTMLInputElement).checked).toBe(true)
  expect(view.querySelector('[role="alert"]')?.textContent).toContain('Could not delete 1')
})

test('single result deletion asks for confirmation and reports network errors', async () => {
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
  vi.spyOn(api, 'asset').mockResolvedValue({ ...item('a'), state: 'completed',
    submittedAt: '2026-09-24T00:00:00Z', settings: {} })
  const remove = vi.spyOn(api, 'deleteAssets').mockRejectedValue(new Error('offline'))
  const view = await show(['a'])
  await click(Array.from(view.querySelectorAll('button')).find(b => b.textContent === 'View details')!)
  await click(Array.from(view.querySelectorAll('button')).find(b => b.textContent === 'Delete this result')!)
  expect(remove).toHaveBeenCalledWith(['a'], 'csrf')
  expect(view.querySelectorAll('article')).toHaveLength(1)
  expect(view.querySelector('[role="alert"]')?.textContent).toContain('Could not delete 1')
})

test('missing per-item response is treated as failure', async () => {
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
  vi.spyOn(api, 'deleteAssets').mockResolvedValue({ results: [] })
  const view = await show(['a'])
  await click(view.querySelector('article input')!)
  await click(view.querySelector('button')!)
  expect(view.querySelectorAll('article')).toHaveLength(1)
  expect(view.querySelector('[role="alert"]')?.textContent).toContain('Could not delete 1')
})

test('thumbnail generation is lazy and failure preserves the catalog entry', async () => {
  const view = await show(['a'])
  const image = view.querySelector('article img')!
  expect(image.getAttribute('src')).toBe('/thumbnail/a')
  expect(image.getAttribute('loading')).toBe('lazy')
  await act(async () => { image.dispatchEvent(new Event('error')) })
  expect(view.querySelector('article')?.textContent).toContain('Preview unavailable')
  expect(view.querySelector('article strong')?.textContent).toBe('a.png')
  expect(view.querySelectorAll('article')).toHaveLength(1)
})
