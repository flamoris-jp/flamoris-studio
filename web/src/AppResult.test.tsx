// @vitest-environment jsdom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { api, type Asset } from './api'

let root: Root, host: HTMLDivElement
const asset: Asset = { id: 'asset', executionId: '00000000-0000-4000-8000-000000000001',
  displayName: 'image.png', createdAt: '', mediaKind: 'image', mimeType: 'image/png',
  sizeBytes: 100, width: 32, height: 24, hasThumbnail: false,
  previewUrl: '/preview', thumbnailUrl: '/thumbnail', downloadUrl: '/download' }

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  window.location.hash = `execution/${asset.executionId}`
  vi.spyOn(api, 'session').mockResolvedValue({ authenticated: true, userName: 'owner@example.test',
    csrfToken: 'csrf', allowRegistration: false })
  vi.spyOn(api, 'discovery').mockResolvedValue({ available: false, templates: [], checkpoints: [], loras: [] })
  vi.spyOn(api, 'imagePreferences').mockResolvedValue({ width: 512, height: 512, steps: 20, cfg: 7 })
  vi.spyOn(api, 'saveImagePreferences').mockResolvedValue({ width: 512, height: 512, steps: 20, cfg: 7 })
  vi.spyOn(api, 'assistantAvailability').mockResolvedValue({ available: false, state: 'unavailable' })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); window.location.hash = ''
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})

test.each(['external', 'studio'] as const)('owned %s execution hash preserves correct settings actions', async origin => {
  vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'completed',
    submittedAt: '', assets: [{ ...asset, origin }] })
  await act(async () => root.render(<App />))
  expect(host.querySelector('a[href="/download"]')).not.toBeNull()
  const controls = [...host.querySelectorAll('button')].map(button => button.textContent)
  expect(controls.includes('View settings')).toBe(origin === 'studio')
  expect(controls.includes('Use settings ↗')).toBe(origin === 'studio')
})

test('an empty server snapshot does not restore settings or clear the owned execution hash', async () => {
  vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'completed', submittedAt: '', assets: [asset] })
  vi.spyOn(api, 'asset').mockResolvedValue({ ...asset, state: 'completed', submittedAt: '', settings: {} })
  await act(async () => root.render(<App />))
  const restore = [...host.querySelectorAll('button')].find(button => button.textContent === 'Use settings ↗')!
  await act(async () => restore.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(window.location.hash).toBe(`#execution/${asset.executionId}`)
  expect(host.textContent).toContain('Saved Image settings are unavailable.')
})
