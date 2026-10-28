import { useEffect, useRef, useState } from 'react'
import { api, HTTPFailure, type Execution, type ParameterSpec, type VideoDiscovery, type VideoInput, type VideoWorkflow } from './api'
import { ResultPreview } from './Gallery'
import { legalSeed, ReferencePicker, seedDomain, validValue } from './WorkflowImage'

export type VideoDraft = Partial<VideoInput>
const terminal = ['completed', 'failed', 'cancelled', 'busy']
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/
export function videoDraft(item: VideoWorkflow): VideoDraft {
  return { workflowId: item.id, definitionVersion: item.definitionVersion!, definitionDigest: item.definitionDigest!, imageInputs: {},
    additionalParameters: Object.fromEntries(Object.entries(item.parameters).filter(([key, spec]) => key !== 'seed' && spec.role !== 'managed_input' && spec.default !== undefined).map(([key, spec]) => [key, spec.default])) }
}
export function videoPayload(form: VideoDraft, item?: VideoWorkflow): VideoInput | null {
  if (!item?.selectable || item.recipeSchema !== 11 || form.workflowId !== item.id || form.definitionVersion !== item.definitionVersion || form.definitionDigest !== item.definitionDigest) return null
  const images = Object.entries(item.parameters).filter(([, spec]) => spec.role === 'managed_input').map(([key]) => key)
  if (Object.keys(form.imageInputs ?? {}).some(key => !images.includes(key))) return null
  if (Object.keys(form.additionalParameters ?? {}).some(key => !item.parameters[key] || images.includes(key))) return null
  const imageInputs: Record<string, string> = {}, additionalParameters: Record<string, unknown> = {}
  for (const [key, spec] of Object.entries(item.parameters)) {
    if (key === 'seed' && (form.additionalParameters?.seed === undefined || form.additionalParameters.seed === '')) { if (!seedDomain(spec)) return null; continue }
    const value = spec.role === 'managed_input' ? form.imageInputs?.[key] : form.additionalParameters?.[key] ?? spec.default
    if (value === undefined && !spec.required) continue
    if (!validValue(spec, value)) return null
    if (spec.role === 'managed_input') imageInputs[key] = value as string
    else additionalParameters[key] = value
  }
  const { width, height, frames, fps } = additionalParameters
  if ([width, height, frames, fps].some(value => typeof value !== 'number' || !Number.isSafeInteger(value))) return null
  const [w, h, n, rate] = [width, height, frames, fps] as number[]
  if (w < 64 || w > 1024 || h < 64 || h > 1024 || w % 16 || h % 16 || w * h > 832 * 832 || n < 5 || n > 161 || n % 4 !== 1 || rate < 1 || rate > 32 || n / rate > 10.1) return null
  return { workflowId: item.id, definitionVersion: 1, definitionDigest: item.definitionDigest!, imageInputs, additionalParameters }
}

function Field({ name, spec, value, disabled, update }: { name: string; spec: ParameterSpec; value: unknown; disabled: boolean; update: (value: unknown) => void }) {
  const label = ({ width: 'Width', height: 'Height', frames: 'Frames', fps: 'Frames per second', seed: 'Seed' } as Record<string, string>)[name] ?? spec.label ?? name
  return <label>{label}{spec.role === 'prompt' ? <textarea disabled={disabled} rows={4} minLength={spec.min_length} maxLength={spec.max_length} value={String(value ?? '')} onChange={event => update(event.target.value)} /> : spec.enum ?
    <select disabled={disabled} value={String(value ?? '')} onChange={event => update(spec.enum!.find(choice => String(choice) === event.target.value))}>{spec.enum.map((choice, i) => <option key={i} value={String(choice)}>{String(choice)}</option>)}</select> :
    <input disabled={disabled} type={spec.type === 'boolean' ? 'checkbox' : ['integer', 'number'].includes(spec.type) ? 'number' : 'text'}
      min={spec.minimum} max={spec.maximum} step={name === 'frames' ? 4 : spec.type === 'number' ? 'any' : spec.multiple_of ?? 1}
      minLength={spec.min_length} maxLength={spec.max_length} placeholder={name === 'seed' ? 'Automatic' : undefined}
      checked={spec.type === 'boolean' ? Boolean(value) : undefined} value={spec.type === 'boolean' ? undefined : String(value ?? '')}
      onChange={event => update(spec.type === 'boolean' ? event.target.checked : ['integer', 'number'].includes(spec.type) ? event.target.value === '' ? '' : Number(event.target.value) : event.target.value)} />}</label>
}

export default function Video({ csrf, accountKey, execution, onExecution, authorized = () => true }: {
  csrf: string; accountKey: string; execution: Execution | null; onExecution: (value: Execution | null) => void; authorized?: () => boolean
}) {
  const [discovery, setDiscovery] = useState<VideoDiscovery>({ available: false, workflows: [], managedInputReady: false })
  const [form, setForm] = useState<VideoDraft>({})
  const [checking, setChecking] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [pendingId, setPendingId] = useState<string | null>(null), [uncertain, setUncertain] = useState(false)
  const [references, setReferences] = useState<Record<string, boolean>>({}), [referenceBusy, setReferenceBusy] = useState<Record<string, boolean>>({})
  const epoch = useRef(0), mounted = useRef(false), sending = useRef(false)
  const current = (version: number) => mounted.current && epoch.current === version && authorized()
  const storageKey = `flamoris.video.pending:${accountKey}`
  const clearPending = () => { try { sessionStorage.removeItem(storageKey) } catch {} }
  const active = !!execution && !terminal.includes(execution.state)
  const item = discovery.workflows.find(value => value.id === form.workflowId)
  const payload = videoPayload(form, item)
  const working = Object.values(referenceBusy).some(Boolean)
  const locked = busy || active || uncertain
  const missing = item?.referenceRoles?.some(role => references[role] !== true) ?? true
  const duration = Number(form.additionalParameters?.frames) / Number(form.additionalParameters?.fps)
  const update = (name: string, value: unknown) => setForm(old => ({ ...old, additionalParameters: { ...old.additionalParameters, [name]: value } }))
  async function check() {
    const version = epoch.current
    setChecking(true)
    try {
      const value = await api.videoDiscovery()
      if (!current(version)) return
      setDiscovery(value)
      const first = value.workflows.find(recipe => recipe.selectable)
      if (first) setForm(old => old.workflowId ? old : videoDraft(first))
    } catch { if (current(version)) setDiscovery({ available: false, workflows: [], managedInputReady: false }) }
    finally { if (current(version)) setChecking(false) }
  }
  async function recover(requestId: string) {
    const version = epoch.current
    try {
      const value = await api.videoRequest(requestId)
      if (!current(version)) return
      onExecution(value); window.location.hash = `execution/${value.id}`; setUncertain(false); setError('')
    } catch { if (current(version)) setError('The previous Video submission cannot be confirmed yet. No resubmission was made.') }
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
    let live = true, inflight = false
    const version = epoch.current
    const timer = window.setInterval(() => {
      if (document.hidden || inflight || !current(version)) return
      inflight = true
      api.execution(execution.id).then(async value => {
        if (!live || !current(version)) return
        if (value.state === 'completed') {
          try { const result = await api.result(value.id); if (live && current(version)) onExecution(result) }
          catch { if (live && current(version)) { onExecution(value); setError('Result metadata is temporarily unavailable. Reload the result below.') } }
        } else onExecution(value)
      }).catch(() => { if (live && current(version)) setError('Video status is temporarily unavailable.') }).finally(() => { inflight = false })
    }, 2500)
    return () => { live = false; clearInterval(timer) }
  }, [execution?.id, execution?.state])
  return <section className="panel"><span className="eyebrow">VIDEO</span><h2>Animate an image</h2><p>Choose a start image and describe the motion.</p>
    <div className="row"><label>Video recipe<select disabled={locked || working} value={form.workflowId ?? ''} onChange={event => {
      const selected = discovery.workflows.find(recipe => recipe.id === event.target.value)
      if (selected) { setForm(videoDraft(selected)); setReferences({}); setError('') }
    }}><option value="" disabled>Choose a recipe</option>{form.workflowId && !item && <option value={form.workflowId} disabled>Previous recipe (unavailable)</option>}{discovery.workflows.map(recipe => <option key={recipe.id} value={recipe.id} disabled={!recipe.selectable}>{recipe.name}{recipe.selectable ? '' : ' (unavailable)'}</option>)}</select></label>
      <button type="button" disabled={checking || busy || working} onClick={check}>Check again</button></div>
    {!discovery.available && <p role="status">{checking ? 'Checking Video availability…' : 'Video generation is unavailable. An available video service and recipe are required.'}</p>}
    {item && item.definitionDigest !== form.definitionDigest && <p role="alert">This recipe changed. Select it again before generating. Your current draft has been kept.</p>}
    <form onSubmit={async event => {
      event.preventDefault()
      if (!payload || !discovery.available || !discovery.managedInputReady || checking || locked || working || missing || sending.current) return
      sending.current = true
      const version = epoch.current, requestId = crypto.randomUUID()
      setBusy(true); setError(''); setPendingId(requestId)
      try { sessionStorage.setItem(storageKey, requestId) }
      catch { sending.current = false; setBusy(false); setPendingId(null); setError('Could not record this request in the browser session. No generation was submitted.'); return }
      try {
        const value = await api.videoSubmit(payload, requestId, csrf)
        if (current(version)) { onExecution(value); window.location.hash = `execution/${value.id}` }
      } catch (failure) {
        if (!current(version)) return
        if (failure instanceof HTTPFailure && failure.status < 500 && failure.status !== 401) { clearPending(); setPendingId(null); setError(failure.message) }
        else { setUncertain(true); setError('Video submission could not be confirmed. No automatic retry was made. Check the existing request before submitting again.') }
      } finally { sending.current = false; if (current(version)) setBusy(false) }
    }}>
      {item && <>
        {(item.referenceRoles ?? []).map(role => <ReferencePicker key={`${item.id}-${role}`} enabled={discovery.managedInputReady} label={role === 'start_image' ? 'Start image' : 'End image'}
          help={role === 'start_image' ? 'The image used to begin this clip.' : 'The target image for the end of this clip.'} inputId={form.imageInputs?.[role]} csrf={csrf}
          disabled={locked || working && !referenceBusy[role]} onBusy={value => setReferenceBusy(old => ({ ...old, [role]: value }))}
          onAvailability={value => setReferences(old => ({ ...old, [role]: value }))} onChange={id => {
            setReferences(old => ({ ...old, [role]: false }))
            setForm(old => { const images = { ...old.imageInputs }; if (id) images[role] = id; else delete images[role]; return { ...old, imageInputs: images } })
          }} />)}
        {Object.entries(item.parameters).filter(([, spec]) => spec.role === 'prompt').map(([name, spec]) => <Field key={name} name={name} spec={spec} value={form.additionalParameters?.[name] ?? spec.default} disabled={locked} update={value => update(name, value)} />)}
        <div className="row">{['width', 'height', 'frames', 'fps', 'seed'].filter(name => item.parameters[name]).map(name => <Field key={name} name={name} spec={item.parameters[name]} value={form.additionalParameters?.[name]} disabled={locked} update={value => update(name, value)} />)}
          {item.parameters.seed && <button type="button" disabled={locked} onClick={() => update('seed', legalSeed(item.parameters.seed))}>Random seed</button>}</div>
        <p role="status">{Number.isFinite(duration) && duration > 0 ? `Clip duration: ${duration.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')} seconds` : 'Choose a valid frame count and frame rate.'}</p>
        <small>Dimensions use steps of 16 pixels. Frame count uses 5, 9, 13… up to 161; clips are limited to 10.1 seconds.</small>
        <details><summary>Advanced settings</summary>{Object.entries(item.parameters).filter(([name, spec]) => spec.role !== 'prompt' && spec.role !== 'managed_input' && !['width', 'height', 'frames', 'fps', 'seed'].includes(name)).map(([name, spec]) => <Field key={name} name={name} spec={spec} value={form.additionalParameters?.[name] ?? spec.default} disabled={locked} update={value => update(name, value)} />)}</details>
      </>}
      {error && <p role="alert" className="error">{error}</p>}
      {uncertain && pendingId && <button type="button" disabled={busy} onClick={() => recover(pendingId)}>Check previous request</button>}
      <button className="primary" disabled={!payload || !discovery.available || !discovery.managedInputReady || checking || locked || working || missing}>{busy ? 'Submitting…' : 'Generate video'}</button>
    </form>
    {execution && <div className="results"><h3>Video result</h3><span className="badge">{execution.state.replaceAll('_', ' ')}</span>
      {execution.state === 'busy' && <p>The generation service is busy; no new generation was accepted.</p>}
      {['unknown', 'submission_unknown'].includes(execution.state) && <p>The generation outcome is uncertain. No automatic resubmission was made.</p>}
      {['queued', 'running', 'cancel_requested', 'unknown'].includes(execution.state) && <button onClick={async () => {
        const version = epoch.current
        try { const value = await api.cancel(execution.id, csrf); if (current(version)) onExecution(value) }
        catch { if (current(version)) setError('Cancellation could not be confirmed. Continue checking this execution.') }
      }}>Request cancellation</button>}
      {execution.state === 'completed' && <div className="assets">{execution.assets.map(asset => <article key={asset.id}><ResultPreview item={asset} /><strong>{asset.displayName}</strong><a href={asset.downloadUrl}>Download ↓</a></article>)}</div>}
      <button type="button" onClick={async () => {
        const version = epoch.current
        try { const value = await api.result(execution.id); if (current(version)) { onExecution(value); setError('') } }
        catch { if (current(version)) setError('Video result is temporarily unavailable.') }
      }}>Reload result</button>
    </div>}
  </section>
}
