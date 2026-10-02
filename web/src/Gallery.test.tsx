// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Gallery, { ImagePreview, ResultPreview } from './Gallery'
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
  vi.useRealTimers()
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
  vi.useFakeTimers()
  const view = await show(['a'])
  const image = view.querySelector('article img')!
  expect(image.getAttribute('src')).toBe('/thumbnail/a')
  expect(image.getAttribute('loading')).toBe('lazy')
  await act(async () => { image.dispatchEvent(new Event('error')) })
  expect(view.querySelector('article')?.textContent).not.toContain('Preview unavailable')
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(view.querySelector('article img')?.getAttribute('src')).toBe('/thumbnail/a?previewRetry=0-1')
  await act(async () => { view.querySelector('article img')!.dispatchEvent(new Event('error')) })
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  await act(async () => { view.querySelector('article img')!.dispatchEvent(new Event('error')) })
  expect(view.querySelector('article')?.textContent).toContain('Preview unavailable')
  expect(vi.getTimerCount()).toBe(0)
  await click(Array.from(view.querySelectorAll('button')).find(b => b.textContent === 'Reload preview')!)
  expect(view.querySelector('article img')?.getAttribute('src')).toBe('/thumbnail/a?previewRetry=1-0')
  expect(view.querySelector('article strong')?.textContent).toBe('a.png')
  expect(view.querySelectorAll('article')).toHaveLength(1)
})

test('View details opens an immediately visible dialog with saved settings', async () => {
  const detail = vi.spyOn(api, 'asset').mockResolvedValue({ ...item('a'), state: 'completed',
    submittedAt: '2026-09-24T00:00:00Z', settings: { seed: 42, width: 768 } })
  const view = await show(['a'])
  await click(Array.from(view.querySelectorAll('button')).find(b => b.textContent === 'View details')!)
  expect(detail).toHaveBeenCalledWith('a')
  expect(view.querySelector('[role="dialog"]')?.textContent).toContain('42')
  expect(view.querySelector('[role="dialog"]')?.textContent).toContain('768')
  await click(Array.from(view.querySelectorAll('[role="dialog"] button')).find(b => b.textContent === 'Close')!)
  expect(view.querySelector('[role="dialog"]')).toBeNull()
})


test('24 gallery images recover from load errors without losing entries', async () => {
  vi.useFakeTimers()
  const view = await show(Array.from({ length: 24 }, (_, i) => String(i)))
  await act(async () => {
    for (const image of view.querySelectorAll('img')) image.dispatchEvent(new Event('error'))
  })
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  await act(async () => {
    for (const image of view.querySelectorAll('img')) image.dispatchEvent(new Event('load'))
  })
  expect(view.querySelectorAll('article img')).toHaveLength(24)
  expect(view.textContent).not.toContain('Preview unavailable')
  expect(vi.getTimerCount()).toBe(0)
})

test('preview source changes and unmount cancel pending image retries', async () => {
  vi.useFakeTimers()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => { root!.render(<ImagePreview item={item('a')} />) })
  await act(async () => { host!.querySelector('img')!.dispatchEvent(new Event('error')) })
  expect(vi.getTimerCount()).toBe(1)
  await act(async () => { root!.render(<ImagePreview item={item('b')} />) })
  expect(host.querySelector('img')?.getAttribute('src')).toBe('/preview/b')
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => { host!.querySelector('img')!.dispatchEvent(new Event('error')) })
  await act(async () => { root!.unmount() })
  root = undefined
  expect(vi.getTimerCount()).toBe(0)
})

test.each([
  ['audio', 'audio/wav', 'audio'], ['video', 'video/mp4', 'video'],
])('validated %s preview is controlled, never autoplays, and offers explicit reload', async (mediaKind, mimeType, tag) => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  const asset = { ...item('media'), mediaKind, mimeType }
  await act(async () => root!.render(<ResultPreview item={asset} />))
  const player = host.querySelector(tag)!
  expect(player.getAttribute('src')).toBe('/preview/media')
  expect(player.hasAttribute('controls')).toBe(true)
  expect(player.getAttribute('preload')).toBe('metadata')
  expect(player.hasAttribute('autoplay')).toBe(false)
  await act(async () => player.dispatchEvent(new Event('error')))
  expect(host.textContent).toContain('download may still work')
  expect(host.querySelector(tag)).toBeNull()
  await click(host.querySelector('button')!)
  expect(host.querySelector(tag)).not.toBeNull()
})

test.each([
  ['midi', 'audio/midi'], ['metadata', 'application/json'],
  ['image', 'image/vnd.adobe.photoshop'], ['image', 'image/svg+xml'],
  ['audio', 'text/html'],
])('unsupported %s/%s stays a file without executable or guessed preview', async (mediaKind, mimeType) => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => root!.render(<ResultPreview item={{ ...item('file'), mediaKind, mimeType }} />))
  expect(host.querySelector('img,audio,video,iframe,script')).toBeNull()
  expect(host.textContent).toContain('Download')
})

test('non-image gallery entry remains after preview failure and does not restore Image settings', async () => {
  const media = { ...item('audio'), mediaKind: 'audio', mimeType: 'audio/wav',
    outputRole: { port: 'audio', role: 'primary_audio', index: 0 } }
  vi.spyOn(api, 'assets').mockResolvedValue({ items: [media], nextOffset: null })
  vi.spyOn(api, 'asset').mockResolvedValue({ ...media, state: 'completed', submittedAt: media.createdAt, settings: {} })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => root!.render(<Gallery csrf="csrf" onUseSettings={vi.fn()} />))
  expect(host.querySelector('article img,audio,video')).toBeNull()
  await click(Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'View details')!)
  expect(host.querySelector('[role="dialog"]')?.textContent).toContain('primary_audio')
  expect(host.textContent).not.toContain('Use settings')
  await act(async () => host!.querySelector('audio')!.dispatchEvent(new Event('error')))
  expect(host.querySelectorAll('article')).toHaveLength(1)
  expect(host.querySelector('[role="dialog"] a')?.getAttribute('href')).toBe('/download/audio')
})
