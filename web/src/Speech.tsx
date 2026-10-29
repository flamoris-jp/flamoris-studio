import { useEffect, useRef, useState } from 'react'
import { api, HTTPFailure, type Execution, type ManagedInput, type SpeechInput } from './api'
import { ResultPreview } from './Gallery'
import { invalidFields, useActionFeedback, type ReportProblem } from './ActionFeedback'

export type SpeechDraft = { text: string; caption: string; seconds: string; steps: string; seed: string }
export const initialSpeechDraft = (): SpeechDraft => ({ text: '', caption: '', seconds: '', steps: '40', seed: '0' })

const textValid = (value: string, required: boolean) => (!required || !!value.trim()) &&
  [...value].length <= 512 && new TextEncoder().encode(value).length <= 2048 &&
  !/[\u0000-\u0008\u000b-\u001f]/.test(value)

export function speechPayload(form: SpeechDraft, report?: ReportProblem): SpeechInput | null {
  const seconds = form.seconds.trim() ? Number(form.seconds) : null, steps = Number(form.steps), seed = Number(form.seed)
  if (invalidFields([
    [!textValid(form.text, true), 'Speech text: enter 1 to 512 characters within 2048 UTF-8 bytes, without unsupported control characters.'],
    [!textValid(form.caption, false), 'Voice description: use at most 512 characters within 2048 UTF-8 bytes, without unsupported control characters.'],
    [seconds !== null && (!Number.isFinite(seconds) || seconds < 0.5 || seconds > 30), 'Output length: leave blank for Automatic or enter 0.5 to 30 seconds.'],
    [!form.steps.trim() || !Number.isSafeInteger(steps) || steps < 1 || steps > 80, 'Sampling steps: enter a whole number from 1 to 80.'],
    [form.seed.trim() && (!Number.isSafeInteger(seed) || seed < 0 || seed > Number.MAX_SAFE_INTEGER), 'Seed: leave blank for Random or enter a whole number from 0 to 9007199254740991.'],
  ], report)) return null
  return { text: form.text, caption: form.caption, seconds, steps, seed: form.seed.trim() ? seed : null }
}

export default function Speech({ csrf, accountKey, form, setForm, execution, onExecution, statusError = '', authorized = () => true }: {
  csrf: string; accountKey: string; form: SpeechDraft; setForm: (value: SpeechDraft) => void;
  execution: Execution | null; onExecution: (value: Execution | null) => void; statusError?: string; authorized?: () => boolean
}) {
  const [available, setAvailable] = useState(false)
  const [referenceAvailable, setReferenceAvailable] = useState(false)
  const [reference, setReference] = useState<ManagedInput | null>(null)
  const [referenceFile, setReferenceFile] = useState<File | null>(null)
  const [referenceUrl, setReferenceUrl] = useState('')
  const previewUrl = useRef('')
  const clearReference = () => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current)
    previewUrl.current = ''; setReferenceUrl(''); setReference(null); setReferenceFile(null)
  }
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
  const problems: string[] = []
  const payload = speechPayload(form, message => problems.push(message))
  const active = execution && ['queued', 'running', 'submitting', 'cancel_requested', 'unknown', 'submission_unknown'].includes(execution.state)
  if (checking) problems.push('Speech service: availability is still being checked. Wait, then press Generate again.')
  else if (!available) problems.push('Speech service: unavailable. Use Check again to refresh availability.')
  if (referenceFile && (!reference || !referenceAvailable || !reference.available)) problems.push('Reference audio: upload an available WAV supported by the speech service, or remove the reference to generate without it.')
  if (uncertain || execution?.state === 'submission_unknown' || execution?.state === 'unknown') problems.push('Previous Speech request: its outcome is unconfirmed. Check the previous request or result; no new recording will be submitted.')
  else if (active) problems.push('Previous Speech request: still active. Wait for completion or request cancellation and confirm it has finished.')
  const validation = useActionFeedback(problems)
  const change = (key: keyof SpeechDraft, value: string) => setForm({ ...form, [key]: value })
  async function check() {
    const version = epoch.current
    setChecking(true)
    try {
      const status = await api.speechDiscovery()
      if (current(version)) {
        setAvailable(status.available === true)
        setReferenceAvailable(status.referenceAvailable === true)
      }
    } catch { if (current(version)) { setAvailable(false); setReferenceAvailable(false) } }
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
    return () => { mounted.current = false; epoch.current += 1; if (previewUrl.current) URL.revokeObjectURL(previewUrl.current) }
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
    <p>Generate one speech recording from text and an optional voice description. Add a reference recording to guide the voice when reference speech is available.</p>
    {!available && <p role="status">{checking ? 'Checking speech availability…' : 'Speech is unavailable for the configured service.'} <button type="button" disabled={checking || busy} onClick={check}>Check again</button></p>}
    <form noValidate onSubmit={async event => {
      event.preventDefault()
      if (busy || !validation.check()) return
      if (!payload || !available || busy || active || uncertain || referenceFile && (!reference || !referenceAvailable || !reference.available)) return
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
        const created = await api.speechSubmit({ ...payload, ...(reference ? { referenceInputId: reference.id } : {}) }, requestId, csrf)
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
      <fieldset disabled={!referenceAvailable || busy || !!active || uncertain}>
        <legend>Reference audio (optional)</legend>
        <label>Reference WAV<input type="file" accept=".wav,audio/wav,audio/x-wav" onChange={async event => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (!file) return
          const version = epoch.current
          clearReference(); setReferenceFile(file); setBusy(true); setError('')
          try {
            const uploaded = await api.uploadReferenceAudio(file, csrf)
            if (!current(version)) return
            if (!uploaded.available || uploaded.mimeType !== 'audio/wav') throw new Error('Reference audio is unavailable.')
            setReference(uploaded)
            if (typeof URL.createObjectURL === 'function') {
              previewUrl.current = URL.createObjectURL(file); setReferenceUrl(previewUrl.current)
            }
          } catch (failure) {
            if (current(version)) setError(failure instanceof Error ? failure.message : 'Reference upload failed.')
          } finally { if (current(version)) setBusy(false) }
        }} /></label>
        <small>One PCM16 mono/stereo WAV, up to 30 seconds and 8 MiB.</small>
      </fieldset>
      {!referenceAvailable && <p>Reference audio is unavailable for the configured service.
        {available && <button type="button" disabled={checking || busy} onClick={check}>Check again</button>}
      </p>}
      {referenceFile && <div>
        <span>{referenceFile.name}</span>
        {referenceUrl && <audio aria-label="Reference audio preview" controls src={referenceUrl} />}
        <button type="button" disabled={busy || !!active} onClick={clearReference}>Remove reference</button>
      </div>}
      <div className="row"><label>Output length (seconds, optional)<input type="number" min="0.5" max="30" step="0.5" placeholder="Automatic" value={form.seconds} onChange={event => change('seconds', event.target.value)} /></label>
        <label>Sampling steps<input type="number" min="1" max="80" step="1" value={form.steps} onChange={event => change('steps', event.target.value)} /></label>
        <label>Seed<input value={form.seed} inputMode="numeric" onChange={event => change('seed', event.target.value)} placeholder="Random" /></label>
        <button type="button" onClick={() => change('seed', '')}>Random seed</button></div>
      <small>Text and description each allow up to 512 characters. Leave output length blank to let the voice determine its duration.</small>
      {(error || statusError) && <p role="alert" className="error">{error || statusError}</p>}
      {uncertain && pendingId && <button type="button" disabled={busy} onClick={() => recover(pendingId)}>Check previous request</button>}
      {validation.feedback}
      <button className="primary" disabled={busy}>{busy ? 'Submitting…' : 'Generate speech'}</button>
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
