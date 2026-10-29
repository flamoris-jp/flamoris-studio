import { useEffect, useRef, useState } from 'react'
import { api, HTTPFailure, type Asset, type Execution, type MusicDiscovery, type MusicInput, type MusicOperation, type TranscriptionInput } from './api'
import { ResultPreview } from './Gallery'
import { invalidFields, useActionFeedback, type ReportProblem } from './ActionFeedback'

export type MusicDraft = { style: string; lyrics: string; seconds: string; steps: string; seed: string; lm_seed: string }
export const initialMusicDraft = (): MusicDraft => ({ style: '', lyrics: '', seconds: '30', steps: '32', seed: '0', lm_seed: '0' })
const textValid = (value: string, limit: number, required: boolean) => (!required || !!value.trim()) && [...value].length <= limit && new TextEncoder().encode(value).length <= limit * 4 && !/[\u0000-\u0008\u000b-\u001f]/.test(value)
export function musicPayload(form: MusicDraft, report?: ReportProblem): MusicInput | null {
  const seconds = Number(form.seconds), steps = Number(form.steps), seed = Number(form.seed), lmSeed = Number(form.lm_seed)
  if (invalidFields([
    [!textValid(form.style, 1024, true), 'Music style: enter 1 to 1024 characters without unsupported control characters.'],
    [!textValid(form.lyrics, 4096, false), 'Lyrics: use at most 4096 characters without unsupported control characters.'],
    [!form.seconds.trim() || !Number.isFinite(seconds) || seconds < 1 || seconds > 120, 'Duration: enter a number from 1 to 120 seconds.'],
    [!form.steps.trim() || !Number.isSafeInteger(steps) || steps < 1 || steps > 64, 'Sampling steps: enter a whole number from 1 to 64.'],
    ...([['Audio seed', form.seed], ['Composition seed', form.lm_seed]] as const).map(([label, value]): [unknown, string] => [value.trim() && (!Number.isSafeInteger(Number(value)) || Number(value) < 0 || Number(value) > Number.MAX_SAFE_INTEGER), `${label}: leave blank for Random or enter a whole number from 0 to 9007199254740991.`]),
  ], report)) return null
  return { style: form.style, lyrics: form.lyrics, seconds, steps, seed: form.seed.trim() ? seed : null, lm_seed: form.lm_seed.trim() ? lmSeed : null }
}
const terminal = ['completed', 'failed', 'cancelled', 'busy']
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/

export default function Music({ csrf, accountKey, execution, onExecution, authorized = () => true }: {
  csrf: string; accountKey: string; execution: Execution | null; onExecution: (value: Execution | null) => void; authorized?: () => boolean
}) {
  const [operation, setOperation] = useState<MusicOperation>('generate')
  const [form, setForm] = useState<MusicDraft>(initialMusicDraft)
  const [availability, setAvailability] = useState<MusicDiscovery>({ generate: false, transcribe: false })
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [sources, setSources] = useState<Asset[]>([])
  const [offset, setOffset] = useState<number | null>(0)
  const [selectedAsset, setSelectedAsset] = useState('')
  const [reference, setReference] = useState<string | null>(null)
  const [maxSeconds, setMaxSeconds] = useState('30')
  const [melodyOnly, setMelodyOnly] = useState(false)
  const epoch = useRef(0), mounted = useRef(false)
  const current = (version: number) => mounted.current && epoch.current === version && authorized()
  const storageKey = `flamoris.music.pending:${accountKey}`
  const clearPending = () => { try { sessionStorage.removeItem(storageKey) } catch {} }
  const active = !!execution && !terminal.includes(execution.state)
  const seconds = Number(maxSeconds)
  const transcription: TranscriptionInput | null = reference && maxSeconds.trim() && Number.isFinite(seconds) && seconds >= 1 && seconds <= 120 ? { referenceInputId: reference, max_seconds: seconds, melody_only: melodyOnly } : null
  const problems: string[] = []
  const payload = operation === 'generate' ? musicPayload(form, message => problems.push(message)) : transcription
  if (operation === 'transcribe') {
    if (!reference) problems.push('Audio Asset: load your WAV Assets, choose one, then press Attach selected audio.')
    if (!maxSeconds.trim() || !Number.isFinite(seconds) || seconds < 1 || seconds > 120) problems.push('Maximum audio length: enter a number from 1 to 120 seconds.')
  }
  if (checking) problems.push('Music service: availability is still being checked. Wait, then try again.')
  else if (!availability[operation]) problems.push('Music operation: unavailable for the configured service. Use Check again to refresh availability.')
  if (uncertain || execution?.state === 'submission_unknown' || execution?.state === 'unknown') problems.push('Previous Music request: its outcome is unconfirmed. Check the previous request or result; no new work will be submitted.')
  else if (active) problems.push('Previous Music request: still active. Wait for completion or request cancellation and confirm it has finished.')
  const validation = useActionFeedback(problems)
  const change = (key: keyof MusicDraft, value: string) => setForm(old => ({ ...old, [key]: value }))
  async function check() {
    const version = epoch.current
    setChecking(true)
    try { const value = await api.musicDiscovery(); if (current(version)) setAvailability(value) }
    catch { if (current(version)) setAvailability({ generate: false, transcribe: false }) }
    finally { if (current(version)) setChecking(false) }
  }
  async function recover(requestId: string) {
    const version = epoch.current
    try {
      const value = await api.musicRequest(requestId)
      if (!current(version)) return
      onExecution(value); window.location.hash = `execution/${value.id}`
      setUncertain(false); setError('')
      setOperation(value.operation === 'music.transcribe' ? 'transcribe' : 'generate')
    } catch { if (current(version)) setError('The previous Music submission cannot be confirmed yet. No resubmission was made.') }
  }
  async function loadSources(next = 0) {
    const version = epoch.current
    setBusy(true)
    try {
      const page = await api.assets(next)
      if (!current(version)) return
      const values = page.items.filter(item => item.mediaKind === 'audio' && item.mimeType === 'audio/wav')
      setSources(old => next === 0 ? values : [...old, ...values.filter(value => !old.some(item => item.id === value.id))]); setOffset(page.nextOffset)
    } catch { if (current(version)) setError('Could not load your generated audio Assets.') }
    finally { if (current(version)) setBusy(false) }
  }
  useEffect(() => {
    mounted.current = true; epoch.current += 1; check()
    let previous: string | null = null
    try { previous = sessionStorage.getItem(storageKey) } catch {}
    if (previous && uuid.test(previous)) { setPendingId(previous); setUncertain(true); recover(previous) }
    return () => { mounted.current = false; epoch.current += 1 }
  }, [])
  useEffect(() => { if (execution && terminal.includes(execution.state)) { clearPending(); setPendingId(null); setUncertain(false) } }, [execution?.state])
  useEffect(() => {
    if (!execution || terminal.includes(execution.state) || execution.state === 'submission_unknown') return
    let active = true, inflight = false
    const version = epoch.current
    const timer = window.setInterval(() => {
      if (document.hidden || inflight || !current(version)) return
      inflight = true
      api.execution(execution.id).then(async value => {
        if (!active || !current(version)) return
        if (value.state === 'completed') {
          try { const result = await api.result(value.id); if (active && current(version)) onExecution(result) }
          catch { if (active && current(version)) { onExecution(value); setError('Result metadata is temporarily unavailable.') } }
        } else onExecution(value)
      }).catch(() => { if (active && current(version)) setError('Music status is temporarily unavailable.') }).finally(() => { inflight = false })
    }, 2500)
    return () => { active = false; clearInterval(timer) }
  }, [execution?.id, execution?.state])
  return <section className="panel"><span className="eyebrow">MUSIC</span><h2>Create music or transcribe audio</h2>
    <div className="row"><label>Operation<select value={operation} disabled={busy || active || uncertain} onChange={event => { setOperation(event.target.value as MusicOperation); setError('') }}><option value="generate">Generate music</option><option value="transcribe">Transcribe audio</option></select></label></div>
    {!availability[operation] && <p role="status">{checking ? 'Checking Music availability…' : 'This Music operation is unavailable for the configured service.'} <button type="button" disabled={checking || busy} onClick={check}>Check again</button></p>}
    <form noValidate onSubmit={async event => {
      event.preventDefault()
      if (busy || !validation.check()) return
      if (!payload || !availability[operation] || checking || busy || active || uncertain) return
      const version = epoch.current, requestId = crypto.randomUUID()
      setBusy(true); setError(''); setPendingId(requestId)
      try { sessionStorage.setItem(storageKey, requestId) }
      catch { setPendingId(null); setBusy(false); setError('Could not record the request in this browser session. No generation was submitted.'); return }
      try {
        const value = await api.musicSubmit(payload, operation, requestId, csrf)
        if (current(version)) { onExecution(value); window.location.hash = `execution/${value.id}` }
      } catch (failure) {
        if (!current(version)) return
        if (failure instanceof HTTPFailure && failure.status < 500 && failure.status !== 401) { clearPending(); setPendingId(null); setError(failure.message) }
        else { setUncertain(true); setError('Music submission could not be confirmed. No automatic retry was made. Confirm the existing outcome before submitting again.') }
      } finally { if (current(version)) setBusy(false) }
    }}>
      {operation === 'generate' ? <>
        <label>Music style<textarea value={form.style} maxLength={2048} onChange={event => change('style', event.target.value)} /></label>
        <label>Lyrics (optional)<textarea value={form.lyrics} maxLength={8192} onChange={event => change('lyrics', event.target.value)} /></label>
        <div className="row"><label>Duration (seconds)<input type="number" min="1" max="120" step="any" value={form.seconds} onChange={event => change('seconds', event.target.value)} /></label><label>Sampling steps<input type="number" min="1" max="64" value={form.steps} onChange={event => change('steps', event.target.value)} /></label>
          <label>Audio seed<input value={form.seed} inputMode="numeric" placeholder="Random" onChange={event => change('seed', event.target.value)} /></label><label>Composition seed<input value={form.lm_seed} inputMode="numeric" placeholder="Random" onChange={event => change('lm_seed', event.target.value)} /></label><button type="button" onClick={() => setForm(old => ({ ...old, seed: '', lm_seed: '' }))}>Random seeds</button></div>
        <small>One music recording, its ABC score and generation metadata. Style allows 1024 characters; lyrics allow 4096.</small>
      </> : <>
        <p>Choose a WAV recording from your generated Assets. Transcription produces MIDI and annotations, with an ABC score when available.</p>
        <button type="button" disabled={busy || active} onClick={() => loadSources()}>Load audio Assets</button>
        <label>Audio Asset<select value={selectedAsset} disabled={busy || active || !!reference} onChange={event => { setSelectedAsset(event.target.value); setReference(null) }}><option value="">Choose your WAV Asset</option>{sources.map(item => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label>
        {offset !== null && offset > 0 && <button type="button" disabled={busy || active} onClick={() => loadSources(offset)}>Load more Assets</button>}
        <button type="button" disabled={busy} onClick={async () => {
          if (!selectedAsset || reference || active || uncertain || !availability.transcribe) { setError(!selectedAsset ? 'Audio Asset: choose a WAV Asset first.' : reference ? 'Audio Asset: detach the current audio before attaching another.' : active || uncertain ? 'Confirm the previous Music request has finished before changing its audio.' : 'Audio transcription is unavailable. Use Check again.'); return }
          const version = epoch.current
          setBusy(true); setError('')
          try { const value = await api.createInput(selectedAsset, csrf); if (current(version)) { if (!value.available) throw new Error(); setReference(value.id) } }
          catch { if (current(version)) setError('Could not attach this audio Asset. Refresh and choose an available WAV.') }
          finally { if (current(version)) setBusy(false) }
        }}>Attach selected audio</button>
        {reference && <p role="status">Audio attached. <button type="button" disabled={busy || active} onClick={async () => {
          const version = epoch.current
          setBusy(true)
          try { await api.deleteInput(reference, csrf); if (current(version)) setReference(null) }
          catch { if (current(version)) setError('Audio detachment could not be confirmed; check again before replacing it.') }
          finally { if (current(version)) setBusy(false) }
        }}>Detach audio</button></p>}
        <label>Maximum audio length (seconds)<input type="number" min="1" max="120" step="any" value={maxSeconds} onChange={event => setMaxSeconds(event.target.value)} /></label><label><input type="checkbox" checked={melodyOnly} onChange={event => setMelodyOnly(event.target.checked)} />Melody only</label>
      </>}
      {error && <p role="alert" className="error">{error}</p>}
      {uncertain && pendingId && <button type="button" disabled={busy} onClick={() => recover(pendingId)}>Check previous request</button>}
      {validation.feedback}
      <button className="primary" disabled={busy}>{busy ? 'Submitting…' : operation === 'generate' ? 'Generate music' : 'Transcribe audio'}</button>
    </form>
    {execution && <div className="results"><h3>{execution.operation === 'music.transcribe' ? 'Transcription result' : 'Music result'}</h3><span className="badge">{execution.state.replaceAll('_', ' ')}</span>
      {execution.state === 'busy' && <p>The generation service is busy; no new generation was accepted.</p>}
      {['unknown', 'submission_unknown'].includes(execution.state) && <p>The generation outcome is uncertain. No automatic resubmission was made.</p>}
      {['queued', 'running', 'cancel_requested', 'unknown'].includes(execution.state) && <button onClick={async () => {
        const version = epoch.current
        try { const value = await api.cancel(execution.id, csrf); if (current(version)) onExecution(value) }
        catch { if (current(version)) setError('Cancellation could not be confirmed; continue checking this execution.') }
      }}>Request cancellation</button>}
      {execution.warnings?.includes('abc_unavailable') && <p role="status">MIDI transcription succeeded, but an ABC score was unavailable.</p>}
      {execution.state === 'completed' && <><div className="assets">{execution.assets.map(item => <article key={item.id}><ResultPreview item={item} /><strong>{item.displayName}</strong><small>{item.outputRole?.role ?? item.mediaKind}</small><a href={item.downloadUrl}>Download ↓</a></article>)}</div><button onClick={async () => {
        const version = epoch.current
        try { const value = await api.result(execution.id); if (current(version)) { onExecution(value); setError('') } }
        catch { if (current(version)) setError('Result metadata is temporarily unavailable. Try reloading this result.') }
      }}>Reload result</button></>}
    </div>}
  </section>
}
