import { useEffect, useState } from 'react'
import { api, type Discovery, type Workflow, type ParameterSpec, type ManagedInput, type Asset } from './api'
import type { ImageDraft, ImageSubmission } from './App'

const fields: Record<string, keyof ImageDraft> = { checkpoint: 'checkpoint', positive_prompt: 'positivePrompt', negative_prompt: 'negativePrompt', width: 'width', height: 'height', seed: 'seed', steps: 'steps', cfg: 'cfg', sampler: 'sampler', scheduler: 'scheduler', denoise: 'denoise' }
export const roleSpec = (item: Workflow | undefined, role: string) => Object.values(item?.parameters ?? {}).find(spec => spec.role === role)

export function validValue(spec: ParameterSpec, value: unknown): boolean {
  if (spec.type === 'boolean' && typeof value !== 'boolean') return false
  if (spec.type === 'integer' && (!Number.isSafeInteger(value) || typeof value !== 'number')) return false
  if (spec.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) return false
  if (spec.type === 'string' && (typeof value !== 'string' || value.length < (spec.min_length ?? 0) || value.length > (spec.max_length ?? 20000))) return false
  if (typeof value === 'number' && (value < (spec.minimum ?? -Infinity) || value > (spec.maximum ?? Infinity))) return false
  if (spec.multiple_of !== undefined && (spec.type !== 'integer' || !Number.isSafeInteger(spec.multiple_of) || spec.multiple_of <= 0 || typeof value !== 'number' || value % spec.multiple_of !== 0)) return false
  if (spec.enum && !spec.enum.some(x => typeof x === typeof value && x === value)) return false
  return true
}

function uniform(count: number): number {
  if (!Number.isSafeInteger(count) || count < 1) throw new Error('Unsupported seed domain')
  // Rejection removes modulo bias; the probability of acceptance is >=1/2.
  const range = 1n << 53n, n = BigInt(count), bound = range - range % n
  for (let attempt = 0; attempt < 128; attempt++) {
    const words = crypto.getRandomValues(new Uint32Array(2))
    const value = (BigInt(words[0]) << 21n) | BigInt(words[1] & 2097151)
    if (value < bound) return Number(value % n)
  }
  throw new Error('Random source unavailable')
}

export function seedDomain(spec?: ParameterSpec): { values?: number[]; first?: bigint; step?: bigint; count: bigint } | null {
  if (!spec || spec.type !== 'integer') return null
  const step = spec.multiple_of ?? 1
  if (!Number.isSafeInteger(step) || step <= 0) return null
  const lower = Math.max(0, Math.ceil(spec.minimum ?? 0)), upper = Math.min(Number.MAX_SAFE_INTEGER, Math.floor(spec.maximum ?? Number.MAX_SAFE_INTEGER))
  if (!Number.isSafeInteger(lower) || !Number.isSafeInteger(upper) || lower > upper) return null
  if (spec.enum) {
    const values = spec.enum.filter((v): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= lower && v <= upper && v % step === 0)
    return values.length ? { values, count: BigInt(values.length) } : null
  }
  const stride = BigInt(step), first = (BigInt(lower) + stride - 1n) / stride * stride
  if (first > BigInt(upper)) return null
  return { first, step: stride, count: (BigInt(upper) - first) / stride + 1n }
}

export function legalSeed(spec?: ParameterSpec): number | null {
  const domain = seedDomain(spec)
  if (!domain) return null
  if (domain.values) return domain.values[uniform(domain.values.length)]
  let index: bigint
  if (domain.count === 1n << 53n) {
    const words = crypto.getRandomValues(new Uint32Array(2))
    index = (BigInt(words[0]) << 21n) | BigInt(words[1] & 2097151)
  } else index = BigInt(uniform(Number(domain.count)))
  return Number(domain.first! + domain.step! * index)
}

export function workflowPayload(form: ImageDraft, item: Workflow): ImageSubmission | null {
  if (!item.selectable || !form.positivePrompt || !form.checkpoint) return null
  const values: Record<string, unknown> = { ...form }
  for (const key of ['width', 'height', 'steps', 'cfg', 'denoise', 'seed']) {
    const raw = form[key as 'width']
    values[key] = raw.trim() === '' ? undefined : Number(raw)
  }
  for (const role of ['steps', 'cfg', 'seed', 'sampler', 'scheduler', 'denoise']) {
    if (!roleSpec(item, role)) delete values[fields[role]]
  }
  if (item.image.dimensions.mode === 'fixed') {
    values.width = item.image.dimensions.width
    values.height = item.image.dimensions.height
  }
  const loras = roleSpec(item, 'loras')
  if (form.loras.length && !loras) return null
  if (loras && (form.loras.length < (loras.min_items ?? 0) || form.loras.length > (loras.max_items ?? 16))) return null
  if (form.loras.some(l => !l.name || [l.strengthModel, l.strengthClip].some(v => v.trim() === '' || !Number.isFinite(Number(v)) || Number(v) < -20 || Number(v) > 20))) return null
  values.loras = form.loras.map(l => ({ ...l, strengthModel: Number(l.strengthModel), strengthClip: Number(l.strengthClip) }))
  for (const [key, spec] of Object.entries(item.parameters)) {
    if (spec.role === 'initial_image') { if (!form.referenceInputId) return null; continue }
    if (spec.role === 'loras') continue
    const field = spec.role ? fields[spec.role] : undefined
    let value = field ? values[field] : form.additionalParameters?.[key] ?? spec.default
    if (spec.role === 'seed' && value === undefined) { if (!seedDomain(spec)) return null; continue }
    if (value === undefined && !spec.required) {
      value = spec.default
      if (value === undefined) continue
    }
    if (!validValue(spec, value)) return null
    if (field) values[field] = value
  }
  return values as unknown as ImageSubmission
}

export function WorkflowPicker({ discovery, form, setForm }: { discovery: Discovery; form: ImageDraft; setForm: React.Dispatch<React.SetStateAction<ImageDraft>> }) {
  const selected = discovery.workflows?.find(item => item.id === form.workflowId)
  const stale = selected && (form.definitionVersion !== selected.definitionVersion || form.definitionDigest !== selected.definitionDigest)
  return <><label>Workflow<select value={stale ? '__stale' : form.workflowId ?? ''} onChange={event => {
    const item = discovery.workflows?.find(x => x.id === event.target.value)
    setForm(old => ({ ...old, workflowId: item?.id, workflowKind: item?.kind, definitionVersion: item?.definitionVersion, definitionDigest: item?.definitionDigest,
      additionalParameters: item ? Object.fromEntries(Object.entries(item.parameters).filter(([,v]) => !v.role && v.default !== undefined).map(([k,v]) => [k,v.default])) : undefined,
      referenceInputId: item?.image.mode === 'img2img' ? old.referenceInputId : undefined }))
  }}><option value="">Automatic builtin (LoRA aware)</option>{stale && <option value="__stale" disabled>Previous Workflow version (reselect below)</option>}
    {form.workflowId && !selected && <option value={form.workflowId} disabled>{form.workflowId} (unavailable)</option>}
    {discovery.workflows?.map(item => <option key={item.id} value={item.id} disabled={!item.selectable}>{item.name}{item.selectable ? '' : ' (unavailable)'}</option>)}
  </select></label>
    {selected && Object.entries(selected.parameters).filter(([,spec]) => !spec.role && spec.type !== 'managed_input').map(([key,spec]) => <label key={key}>{key}
      {spec.enum ? <select value={String(form.additionalParameters?.[key] ?? '')} onChange={e => setForm(old => ({ ...old, additionalParameters: { ...old.additionalParameters, [key]: spec.enum?.find(x => String(x) === e.target.value) } }))}>{spec.enum.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select> :
      <input type={spec.type === 'boolean' ? 'checkbox' : ['integer','number'].includes(spec.type) ? 'number' : 'text'} value={spec.type === 'boolean' ? undefined : String(form.additionalParameters?.[key] ?? '')} checked={spec.type === 'boolean' ? Boolean(form.additionalParameters?.[key]) : undefined}
        onChange={e => setForm(old => ({ ...old, additionalParameters: { ...old.additionalParameters, [key]: spec.type === 'boolean' ? e.target.checked : ['integer','number'].includes(spec.type) ? Number(e.target.value) : e.target.value } }))} />}
    </label>)}
  </>
}

export function ReferencePicker({ enabled, needsReference, inputId, csrf, onChange, onValid }: { enabled: boolean; needsReference: boolean; inputId?: string | null; csrf: string; onChange: (id: string | undefined) => void; onValid: (valid: boolean) => void }) {
  const [input, setInput] = useState<ManagedInput | null>(null)
  const [assets, setAssets] = useState<Asset[]>([])
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [missingPreview, setMissingPreview] = useState(false)
  useEffect(() => {
    let active = true
    onValid(false); setInput(null); setMissingPreview(false)
    if (!inputId) return () => { active = false }
    const refresh = () => api.input(inputId).then(value => {
      if (active) { setInput(value); onValid(value.available && (!value.expiresAt || Date.parse(value.expiresAt) > Date.now())) }
    }).catch(() => { if (active) { onValid(false); setError('Reference unavailable. Choose another Asset.') } })
    refresh()
    const timer = window.setInterval(refresh, 30000)
    return () => { active = false; clearInterval(timer) }
  }, [inputId, onValid])
  async function load(offset = 0) {
    setBusy(true); setError('')
    try { const page = await api.assets(offset); setAssets(old => [...(offset ? old : []), ...page.items.filter(a => ['image/png','image/jpeg','image/webp'].includes(a.mimeType))]); setNextOffset(page.nextOffset); setOpen(true) }
    catch { setError('Could not load Assets.') } finally { setBusy(false) }
  }
  async function attach(asset: Asset) {
    setBusy(true); setError('')
    try { const value = await api.createInput(asset.id, csrf); onChange(value.id); setOpen(false) }
    catch { setError('Could not attach this Asset. No automatic retry was made.') } finally { setBusy(false) }
  }
  return <div className="reference-note"><strong>Reference Image</strong>
    {!needsReference ? <p>Select a ready img2img Workflow to use an initial image.</p> : !enabled ? <p>Reference images are unavailable while the Workflow or service is not ready.</p> : <p>The initial image is center-cropped and resized before generation.</p>}
    {input && <div>{input.thumbnailUrl && !missingPreview ? <img width="128" src={input.thumbnailUrl} alt="Reference image" onError={() => setMissingPreview(true)} /> : <span>Preview unavailable</span>}{!input.available && <p>Reference expired or revoked. Choose another Asset.</p>}</div>}
    {inputId && <button type="button" onClick={() => { onChange(undefined); onValid(false); setError('') }}>Remove reference</button>}
    <button type="button" disabled={!enabled || busy} onClick={() => load()}>{inputId ? 'Replace from Assets' : 'Choose from Assets'}</button>
    {open && <div role="dialog" aria-label="Choose reference Asset">{assets.length ? assets.map(asset => <button type="button" key={asset.id} disabled={busy} onClick={() => attach(asset)}>{asset.displayName}</button>) : <p>No eligible images. Generate an image first.</p>}{nextOffset !== null && <button type="button" disabled={busy} onClick={() => load(nextOffset)}>More Assets</button>}<button type="button" onClick={() => setOpen(false)}>Close picker</button></div>}
    {error && <p role="alert">{error}</p>}
  </div>
}
