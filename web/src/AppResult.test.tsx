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
  vi.spyOn(api, 'speechDiscovery').mockResolvedValue({ available: false })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); window.location.hash = ''
  vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear()
})

test('Clear inputs survives late preference loading and preserves an uncertain execution', async () => {
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'submission_unknown', submittedAt: '', assets: [] })
  const submit = vi.spyOn(api, 'submit')
  const cancel = vi.spyOn(api, 'cancel')
  let resolve!: (value: import('./api').ImagePreferences) => void
  vi.mocked(api.imagePreferences).mockImplementation(() => new Promise(value => { resolve = value }))
  await act(async () => root.render(<App />))
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Clear inputs')!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await act(async () => resolve({ width: 768, height: 1024, steps: 30, cfg: 8 }))
  expect([...host.querySelectorAll('label')].find(label => label.textContent === 'width')!.querySelector('input')!.value).toBe('512')
  expect(host.textContent).toContain('Submission could not be confirmed')
  expect(window.location.hash).toBe(`#execution/${asset.executionId}`)
  expect(submit).not.toHaveBeenCalled()
  expect(cancel).not.toHaveBeenCalled()
})

test('late Use settings cannot refill the draft after Clear inputs', async () => {
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'completed', submittedAt: '', assets: [asset] })
  let resolve!: (value: import('./api').AssetDetail) => void
  vi.spyOn(api, 'asset').mockImplementation(() => new Promise(value => { resolve = value }))
  await act(async () => root.render(<App />))
  const click = async (name: string) => { await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === name)!.dispatchEvent(new MouseEvent('click', { bubbles: true }))) }
  await click('Use settings ↗')
  await click('Clear inputs')
  await act(async () => resolve({ ...asset, state: 'completed', submittedAt: '', settings: { positivePrompt: 'old prompt', width: 768 } }))
  expect((host.querySelector('.image-workspace textarea') as HTMLTextAreaElement).value).toBe('')
  expect(window.location.hash).toBe(`#execution/${asset.executionId}`)
  expect(host.textContent).toContain(asset.displayName)
})

test('offline image service still allows reference upload and recheck preserves the attached draft', async () => {
  window.location.hash = ''
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'uploadInput').mockResolvedValue({ id: 'uploaded', available: true, thumbnailUrl: '/private-upload-thumb' })
  vi.spyOn(api, 'input').mockResolvedValue({ id: 'uploaded', available: true, thumbnailUrl: '/private-upload-thumb' })
  vi.spyOn(api, 'submit').mockResolvedValue({ id: 'unused', state: 'queued', assets: [], submittedAt: '' })
  await act(async () => root.render(<App />))
  const fileInput = host.querySelector<HTMLInputElement>('input[aria-label="Reference image file"]')!
  expect(fileInput).not.toBeNull()
  const prompt = [...host.querySelectorAll('textarea')].find(input => input.closest('label')?.textContent?.startsWith('Positive prompt'))!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(prompt, 'keep my reference draft')
    prompt.dispatchEvent(new Event('input', { bubbles: true }))
    Object.defineProperty(fileInput, 'files', { value: [new File(['fixture'], 'character.png', { type: 'image/png' })] })
    fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  })
  const generate = () => [...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('Generate image'))!
  expect(generate().disabled).toBe(true)
  expect(api.uploadInput).toHaveBeenCalledOnce()
  vi.mocked(api.discovery).mockResolvedValue({ available: true, templates: ['text-to-image'], checkpoints: [{ id: 'm', name: 'test' }], loras: [] })
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Check again')!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(prompt.value).toBe('keep my reference draft')
  expect(host.querySelector('img[src="/private-upload-thumb"]')).not.toBeNull()
  expect(host.textContent).toContain('or remove the reference')
  expect(generate().disabled).toBe(true)
  expect(api.submit).not.toHaveBeenCalled()
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

test('logout clears pending Speech UUID and a late owned execution hash response cannot restore private results', async () => {
  let resolve!: (value: import('./api').Execution) => void
  vi.spyOn(api, 'result').mockImplementation(() => new Promise(value => { resolve = value }))
  vi.spyOn(api, 'logout').mockResolvedValue(undefined)
  vi.mocked(api.session).mockResolvedValueOnce({ authenticated: true, userName: 'owner@example.test', accountKey: 'stable-owner',
    csrfToken: 'csrf', allowRegistration: false }).mockResolvedValue({ authenticated: false, userName: null, csrfToken: 'new', allowRegistration: false })
  await act(async () => root.render(<App />))
  sessionStorage.setItem('flamoris.speech.pending:stable-owner', '00000000-0000-4000-8000-000000000002')
  const logout = [...host.querySelectorAll('button')].find(button => button.textContent === 'Sign out')!
  await act(async () => logout.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await act(async () => resolve({ id: asset.executionId, state: 'completed', submittedAt: '', category: 'speech',
    assets: [{ ...asset, displayName: 'private-speech.wav' }] }))
  expect(sessionStorage.getItem('flamoris.speech.pending:stable-owner')).toBeNull()
  expect(window.location.hash).toBe('')
  expect(host.textContent).not.toContain('private-speech.wav')
})

test('late Speech status polling after logout cannot start a result read or restore execution', async () => {
  vi.useFakeTimers()
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  const result = vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'queued',
    submittedAt: '', category: 'speech', assets: [] })
  let resolve!: (value: import('./api').Execution) => void
  vi.spyOn(api, 'execution').mockImplementation(() => new Promise(value => { resolve = value }))
  vi.spyOn(api, 'logout').mockResolvedValue(undefined)
  await act(async () => root.render(<App />))
  await act(async () => vi.advanceTimersByTime(2500))
  expect(api.execution).toHaveBeenCalledOnce()
  vi.mocked(api.session).mockResolvedValue({ authenticated: false, userName: null, csrfToken: 'new', allowRegistration: false })
  const logout = [...host.querySelectorAll('button')].find(button => button.textContent === 'Sign out')!
  await act(async () => logout.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await act(async () => resolve({ id: asset.executionId, state: 'completed', submittedAt: '', category: 'speech', assets: [] }))
  expect(result).toHaveBeenCalledOnce()
  expect(window.location.hash).toBe('')
})

test('uncertain Speech keeps its own handle and UUID when Image generation replaces the Image result', async () => {
  window.location.hash = ''
  const requestId = '00000000-0000-4000-8000-000000000002'
  vi.stubGlobal('crypto', { randomUUID: () => requestId })
  vi.mocked(api.session).mockResolvedValue({ authenticated: true, userName: 'owner@example.test',
    accountKey: 'stable-owner', csrfToken: 'csrf', allowRegistration: false })
  vi.mocked(api.discovery).mockResolvedValue({ available: true, templates: ['text-to-image'],
    checkpoints: [{ id: 'checkpoint:model', name: 'model' }], loras: [] })
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  vi.mocked(api.speechDiscovery).mockResolvedValue({ available: true })
  const speech = vi.spyOn(api, 'speechSubmit').mockResolvedValue({ id: asset.executionId,
    state: 'submission_unknown', submittedAt: '', category: 'speech', assets: [] })
  vi.spyOn(api, 'submit').mockResolvedValue({ id: '00000000-0000-4000-8000-000000000003',
    state: 'completed', submittedAt: '', category: 'image', assets: [] })
  const lookup = vi.spyOn(api, 'speechRequest').mockResolvedValue({ id: asset.executionId,
    state: 'submission_unknown', submittedAt: '', category: 'speech', assets: [] })
  await act(async () => root.render(<App />))
  const nav = async (name: string) => { await act(async () => [...host.querySelectorAll('nav button')]
    .find(button => button.textContent === name)!.dispatchEvent(new MouseEvent('click', { bubbles: true }))) }
  await nav('Speech')
  const speak = [...host.querySelectorAll('label')].find(label => label.textContent === 'Speech text')!.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(speak, 'こんにちは')
    speak.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => speak.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await nav('Image')
  const image = host.querySelector('.image-workspace textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(image, 'an image')
    image.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => image.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await nav('Speech')
  const generate = [...host.querySelectorAll('button')].find(button => button.textContent === 'Generate speech')!
  expect((generate as HTMLButtonElement).disabled).toBe(true)
  expect(sessionStorage.getItem('flamoris.speech.pending:stable-owner')).toBe(requestId)
  await act(async () => root.unmount())
  root = createRoot(host)
  await act(async () => root.render(<App />))
  expect(lookup).toHaveBeenCalledExactlyOnceWith(requestId)
  expect(speech).toHaveBeenCalledOnce()
  expect(sessionStorage.getItem('flamoris.speech.pending:stable-owner')).toBe(requestId)
})

async function switchAccount() {
  vi.spyOn(api, 'logout').mockResolvedValue(undefined)
  vi.mocked(api.session).mockResolvedValue({ authenticated: true, userName: 'next@example.test',
    accountKey: 'next-owner', csrfToken: 'next-csrf', allowRegistration: false })
  const logout = [...host.querySelectorAll('button')].find(button => button.textContent === 'Sign out')!
  await act(async () => logout.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(host.textContent).toContain('next@example.test')
}

test('late Image submit cannot restore the previous account execution or hash', async () => {
  window.location.hash = ''
  vi.mocked(api.discovery).mockResolvedValue({ available: true, templates: ['text-to-image'],
    checkpoints: [{ id: 'checkpoint:model', name: 'model' }], loras: [] })
  vi.spyOn(api, 'styles').mockResolvedValue({ items: [] })
  let resolve!: (value: import('./api').Execution) => void
  vi.spyOn(api, 'submit').mockImplementation(() => new Promise(value => { resolve = value }))
  await act(async () => root.render(<App />))
  const prompt = host.querySelector('.image-workspace textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(prompt, 'previous private prompt')
    prompt.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => prompt.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await switchAccount()
  await act(async () => resolve({ id: asset.executionId, state: 'completed', submittedAt: '', assets: [{ ...asset, displayName: 'previous-private.png' }] }))
  expect(window.location.hash).toBe('')
  expect(host.textContent).not.toContain('previous-private.png')
  expect((host.querySelector('.image-workspace textarea') as HTMLTextAreaElement).value).toBe('')
})

test.each(['View settings', 'Use settings ↗'])('late %s cannot reveal the previous account prompt', async action => {
  vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'completed', submittedAt: '', assets: [asset] })
  let resolve!: (value: import('./api').AssetDetail) => void
  vi.spyOn(api, 'asset').mockImplementation(() => new Promise(value => { resolve = value }))
  await act(async () => root.render(<App />))
  const settings = [...host.querySelectorAll('button')].find(button => button.textContent === action)!
  await act(async () => settings.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await switchAccount()
  await act(async () => resolve({ ...asset, state: 'completed', submittedAt: '', settings: { positivePrompt: 'previous private prompt' } }))
  expect(host.textContent).not.toContain('previous private prompt')
  expect(window.location.hash).toBe('')
})

test('late Image cancellation cannot restore the previous account execution', async () => {
  vi.spyOn(api, 'result').mockResolvedValue({ id: asset.executionId, state: 'queued', submittedAt: '', assets: [] })
  let resolve!: (value: import('./api').Execution) => void
  vi.spyOn(api, 'cancel').mockImplementation(() => new Promise(value => { resolve = value }))
  await act(async () => root.render(<App />))
  const cancel = [...host.querySelectorAll('button')].find(button => button.textContent === 'Request cancellation')!
  await act(async () => cancel.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await switchAccount()
  await act(async () => resolve({ id: asset.executionId, state: 'completed', submittedAt: '', assets: [{ ...asset, displayName: 'previous-private.png' }] }))
  expect(host.textContent).not.toContain('previous-private.png')
  expect(window.location.hash).toBe('')
})
