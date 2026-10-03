import { useEffect, useRef, useState } from 'react'
import { api, HTTPFailure, type Execution, type SpeechInput } from './api'
import { ResultPreview } from './Gallery'

export type SpeechDraft = { text: string; caption: string; seconds: string; steps: string; seed: string }
export const initialSpeechDraft = (): SpeechDraft => ({ text: '', caption: '', seconds: '10', steps: '40', seed: '0' })

const textValid = (value: string, required: boolean) => (!required || !!value.trim()) &&
  [...value].length <= 512 && new TextEncoder().encode(value).length <= 2048 &&
  !/[\u0000-\u0008\u000b-\u001f]/.test(value)

export function speechPayload(form: SpeechDraft): SpeechInput | null {
  const seconds = Number(form.seconds), steps = Number(form.steps), seed = Number(form.seed)
  if (!textValid(form.text, true) || !textValid(form.caption, false) || !form.seconds.trim() ||
    !Number.isFinite(seconds) || seconds < 0.5 || seconds > 30 || !form.steps.trim() ||
    !Number.isSafeInteger(steps) || steps < 1 || steps > 80 || form.seed.trim() &&
    (!Number.isSafeInteger(seed) || seed < 0 || seed > Number.MAX_SAFE_INTEGER)) return null
  return { text: form.text, caption: form.caption, seconds, steps, seed: form.seed.trim() ? seed : null }
}

export default function Speech({ csrf, accountKey, form, setForm, execution, onExecution, statusError = '', authorized = () => true }: {
  csrf: string; accountKey: string; form: SpeechDraft; setForm: (value: SpeechDraft) => void;
  execution: Execution | null; onExecution: (value: Execution | null) => void; statusError?: string; authorized?: () => boolean
}) {
  const [available, setAvailable] = useState(false)
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const epoch = useRef(0)
  const mounted = useRef(false)
  const current = (version: number) => mounted.current && epoch.current === version && authorized()
  const storageKey = `flamoris.speech.pending:${accountKey}`
  const clearPending = () => { try { sessionStorage.removeItem(storageKey) } catch {} }
  const payload = speechPayload(form)
  const active = execution && ['queued', 'running', 'submitting', 'cancel_requested', 'unknown', 'submission_unknown'].includes(execution.state)
  const change = (key: keyof SpeechDraft, value: string) => setForm({ ...form, [key]: value })
  async function check() {
    const version = epoch.current
    setChecking(true)
    try { const status = await api.speechDiscovery(); if (current(version)) setAvailable(status.available === true) }
    catch { if (current(version)) setAvailable(false) }
    finally { if (current(version)) setChecking(false) }
  }
  async function recover(requestId: string) {
    const version = epoch.current
    try {
      const previous = await api.speechRequest(requestId)
      if (!current(version)) return
      onExecution(previous); window.location.hash = `execution/${previous.id}`
      setUncertain(false); setError('')
    } catch { if (current(version)) setError('The previous speech submission cannot be confirmed yet. No resubmission was made.') }
  }
  useEffect(() => {
    mounted.current = true; epoch.current += 1
    check()
    let previous: string | null = null
    try { previous = sessionStorage.getItem(storageKey) } catch {}
    if (previous && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(previous)) {
      setUncertain(true); setPendingId(previous); recover(previous)
    }
    return () => { mounted.current = false; epoch.current += 1 }
  }, [])
  useEffect(() => {
    if (execution && ['completed', 'failed', 'cancelled', 'busy'].includes(execution.state)) {
      clearPending(); setPendingId(null); setUncertain(false)
    }
  }, [execution?.state])
  useEffect(() => {
    if (!execution || ['completed', 'failed', 'cancelled', 'busy', 'submission_unknown'].includes(execution.state)) return
    let active = true, inflight = false
    const version = epoch.current
    const timer = window.setInterval(() => {
      if (document.hidden || inflight || !current(version)) return
      inflight = true
      api.execution(execution.id).then(async value => {
        if (!active || !current(version)) return
        if (value.state === 'completed') {
          try {
            const result = await api.result(value.id)
            if (active && current(version)) onExecution(result)
          } catch { if (active && current(version)) { onExecution(value); setError('Result metadata is temporarily unavailable.') } }
        } else onExecution(value)
      }).catch(() => { if (active && current(version)) setError('Speech status is temporarily unavailable.') })
        .finally(() => { inflight = false })
    }, 2500)
    return () => { active = false; clearInterval(timer) }
  }, [execution?.id, execution?.state])
  return <section className="panel"><span className="eyebrow">SPEECH</span><h2>Japanese speech</h2>
    <p>Generate one speech recording from text and an optional voice description. Reference audio and voice cloning are unavailable.</p>
    {!available && <p role="status">{checking ? 'Checking speech availability…' : 'Speech is unavailable for the configured service.'} <button type="button" disabled={checking || busy} onClick={check}>Check again</button></p>}
    <form onSubmit={async event => {
      event.preventDefault()
      if (!payload || !available || busy || active || uncertain) return
      const version = epoch.current
      setBusy(true); setError('')
      const requestId = crypto.randomUUID()
      setPendingId(requestId)
      try { sessionStorage.setItem(storageKey, requestId) }
      catch {
        setError('Could not record the request in this browser session. No generation was submitted.')
        setPendingId(null); setBusy(false); return
      }
      try {
        const created = await api.speechSubmit(payload, requestId, csrf)
        if (!current(version)) return
        onExecution(created); window.location.hash = `execution/${created.id}`
      } catch (failure) {
        if (!current(version)) return
        // A stalled browser/proxy receipt may follow a committed submission.
        // Keep the draft and fence another submit; discovery is read-only.
        if (failure instanceof HTTPFailure && failure.status < 500 && failure.status !== 401) {
          setError(failure.message)
          setPendingId(null); clearPending()
        } else {
          setUncertain(true)
          setError('Speech submission could not be confirmed. No automatic retry was made. Confirm the existing outcome before submitting again.')
        }
      } finally { if (current(version)) setBusy(false) }
    }}>
      <label>Speech text<textarea value={form.text} maxLength={1024} onChange={event => change('text', event.target.value)} /></label>
      <label>Voice description (optional)<textarea value={form.caption} maxLength={1024} onChange={event => change('caption', event.target.value)} /></label>
      <div className="row"><label>Output length (seconds)<input type="number" min="0.5" max="30" step="0.5" value={form.seconds} onChange={event => change('seconds', event.target.value)} /></label>
        <label>Sampling steps<input type="number" min="1" max="80" step="1" value={form.steps} onChange={event => change('steps', event.target.value)} /></label>
        <label>Seed<input value={form.seed} inputMode="numeric" onChange={event => change('seed', event.target.value)} placeholder="Random" /></label>
        <button type="button" onClick={() => change('seed', '')}>Random seed</button></div>
      <small>Text and description each allow up to 512 characters. Output length limits the recording.</small>
      {(error || statusError) && <p role="alert" className="error">{error || statusError}</p>}
      {uncertain && pendingId && <button type="button" disabled={busy} onClick={() => recover(pendingId)}>Check previous request</button>}
      <button className="primary" disabled={!available || checking || busy || !payload || !!active || uncertain}>{busy ? 'Submitting…' : 'Generate speech'}</button>
    </form>
    {execution && <div className="results"><h3>Speech result</h3><span className="badge">{execution.state.replaceAll('_', ' ')}</span>
      {execution.state === 'busy' && <p>The generation service is busy; no new generation was accepted.</p>}
      {['unknown', 'submission_unknown'].includes(execution.state) && <p>The generation outcome is uncertain. No automatic resubmission was made.</p>}
      {['queued', 'running', 'cancel_requested', 'unknown'].includes(execution.state) && <button onClick={async () => {
        const version = epoch.current
        try { const result = await api.cancel(execution.id, csrf); if (current(version)) onExecution(result) }
        catch { if (current(version)) setError('Cancellation could not be confirmed; continue checking this execution.') }
      }}>Request cancellation</button>}
      {execution.state === 'completed' && <div className="assets">{execution.assets.map(item => <article key={item.id}>
        <ResultPreview item={item} /><strong>{item.displayName}</strong><a href={item.downloadUrl}>Download ↓</a>
      </article>)}</div>}
      {execution.state === 'completed' && <button onClick={async () => {
        const version = epoch.current
        try { const result = await api.result(execution.id); if (current(version)) { onExecution(result); setError('') } }
        catch { if (current(version)) setError('Result metadata is temporarily unavailable. Try reloading this result.') }
      }}>Reload result</button>}
    </div>}
  </section>
}
