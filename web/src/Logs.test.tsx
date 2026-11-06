// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Logs from './Logs'
import { api, type DiagnosticPage } from './api'

let root: Root | undefined
let host: HTMLDivElement
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); host = document.createElement('div'); document.body.append(host) })
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const reference = 'a'.repeat(32)
const page: DiagnosticPage = { items: [{ id: 7, time: '2026-11-06T00:00:00Z', requestId: reference, level: 'ERROR', component: 'generation', operation: 'jobs.submit', phase: 'response', route: '/api/generation/image/jobs', method: 'POST', status: 411, elapsedMs: 15, reason: 'upstream_failure', exceptions: [{ type: 'GatewayError', frames: [{ file: 'gateway.py', line: 170, function: '_exchange' }] }] }], nextCursor: 7, canViewAll: false, scope: 'mine' }
async function show() { root = createRoot(host); await act(async () => root!.render(<Logs initialRequestId={reference} />)) }
async function click(text: string) { const button = Array.from(host.querySelectorAll('button')).find(node => node.textContent === text)!; await act(async () => button.click()) }

test('filters correlated logs, displays original status and loads older events without replay', async () => {
  const logs = vi.spyOn(api, 'logs').mockResolvedValue(page)
  const submit = vi.spyOn(api, 'submit')
  await show()
  expect(logs).toHaveBeenCalledWith({ requestId: reference })
  expect(host.textContent).toContain('HTTP 411')
  expect(host.textContent).toContain('gateway.py:170')
  expect(host.querySelector('label select')?.textContent).not.toContain('All accounts')
  logs.mockResolvedValue({ ...page, items: [{ ...page.items[0], id: 6 }], nextCursor: null })
  await click('Load older events')
  expect(logs).toHaveBeenLastCalledWith({ requestId: reference, before: 7 })
  expect(host.querySelectorAll('article')).toHaveLength(2)
  expect(submit).not.toHaveBeenCalled()
})

test('late responses cannot restore an earlier filter or an unmounted account', async () => {
  let resolve!: (value: DiagnosticPage) => void
  const logs = vi.spyOn(api, 'logs').mockImplementation(() => new Promise(done => { resolve = done }))
  await show()
  const old = resolve
  logs.mockResolvedValue({ ...page, items: [], nextCursor: null })
  await click('Reset filters')
  await act(async () => old(page))
  expect(host.querySelectorAll('article')).toHaveLength(0)
  expect(host.textContent).toContain('No matching events')
})

test('operator account selection is explicit and viewer failure has a retry action', async () => {
  const logs = vi.spyOn(api, 'logs').mockResolvedValue({ ...page, canViewAll: true })
  await show()
  const select = Array.from(host.querySelectorAll('select')).find(node => node.textContent?.includes('All accounts'))!
  await act(async () => { select.value = 'all'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await click('Apply filters')
  expect(logs).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'all' }))
  logs.mockRejectedValue(new Error('Logs are unavailable.'))
  await click('Refresh')
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('Logs are unavailable.')
})
