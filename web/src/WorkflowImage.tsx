import { useEffect, useRef, useState } from 'react'
import { api, imageUploadLimit, imageUploadTypes, type Discovery, type Workflow, type ParameterSpec, type ManagedInput, type Asset } from './api'
import type { ImageDraft, ImageSubmission } from './App'

export const referenceFields: Record<string, keyof ImageDraft> = { initial_image: "referenceInputId", character_image: "characterInputId", style_image: "styleInputId", control_image: "controlInputId" }
const fields: Record<string, keyof ImageDraft> = { ...referenceFields, checkpoint: 'checkpoint', positive_prompt: 'positivePrompt', negative_prompt: 'negativePrompt', width: 'width', height: 'height', seed: 'seed', steps: 'steps', cfg: 'cfg', sampler: 'sampler', scheduler: 'scheduler', denoise: 'denoise' }
export const roleSpec = (item: Workflow | undefined, role: string) => Object.values(item?.parameters ?? {}).find(spec => spec.role === role)

export function validValue(spec: ParameterSpec, value: unknown): boolean {
  if (!['boolean', 'integer', 'number', 'string'].includes(spec.type)) return false
  if (spec.type === 'boolean' && typeof value !== 'boolean') return false
  if (spec.type === 'integer' && (!Number.isSafeInteger(value) || typeof value !== 'number')) return false
  if (spec.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) return false
  if (spec.type === 'string' && (typeof value !== 'string' || value.length < (spec.min_length ?? 0) || value.length > (spec.max_length ?? 20000))) return false
  if (spec.type === 'string' && spec.pattern !== undefined) {
    try {
      const match = new RegExp(`^(?:${spec.pattern})$`).exec(value as string)
      if (!match || match[0] !== value) return false
    } catch { return false }
  }
  if (typeof value === 'number' && (value < (spec.minimum ?? -Infinity) || value > (spec.maximum ?? Infinity))) return false
  if (spec.multiple_of !== undefined && (spec.type !== 'integer' || !Number.isSafeInteger(spec.multiple_of) || spec.multiple_of <= 0 || typeof value !== 'number' || value % spec.multiple_of !== 0)) return false
  if (spec.enum && !spec.enum.some(x => typeof x === typeof value && x === value)) return false
  if (spec.max_bytes !== undefined && (!Number.isSafeInteger(spec.max_bytes) || spec.max_bytes <= 0 || new TextEncoder().encode(JSON.stringify(value)).length > spec.max_bytes)) return false
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
  if (item.recipeSchema === 10) {
    if (!item.selectable || item.kind !== 'registered-comfy' || item.definitionVersion !== 1 || item.definitionDigest !== item.id.slice(6) || form.definitionDigest !== item.definitionDigest || form.definitionVersion !== 1) return null
    const additionalParameters: Record<string, unknown> = {}, imageInputs: Record<string, string> = {}
    const images = Object.entries(item.parameters).filter(([,spec]) => spec.role === 'managed_input').map(([key]) => key)
    if (Object.keys(form.imageInputs ?? {}).some(key => !images.includes(key))) return null
    for (const [key, spec] of Object.entries(item.parameters)) {
      const value = spec.role === 'managed_input' ? form.imageInputs?.[key] : form.additionalParameters?.[key] ?? spec.default
      if (value === undefined && !spec.required) continue
      if (!validValue(spec, value)) return null
      if (spec.role === 'managed_input') imageInputs[key] = value as string
      else additionalParameters[key] = value
    }
    return { workflowId: item.id, workflowKind: item.kind, definitionVersion: 1, definitionDigest: item.definitionDigest!, additionalParameters, imageInputs }
  }
  const registered = item.kind === 'registered-comfy' && /^comfy-[a-f0-9]{64}$/.test(item.id)
  if ((!registered && (item.kind !== 'builtin' || !['text-to-image', 'text-to-image-lora'].includes(item.id))) ||
      item.image.dimensions.mode !== 'parameters' ||
      (registered ? item.definitionVersion !== 1 || item.definitionDigest !== item.id.slice(6) :
        item.image.mode !== 'txt2img' || item.definitionVersion !== null || item.definitionDigest !== null) ||
      (form.definitionVersion ?? null) !== item.definitionVersion || (form.definitionDigest ?? null) !== item.definitionDigest) return null
  if (!registered && Object.values(item.parameters).some(s => s.type === "managed_input" || Boolean(s.role && referenceFields[s.role]))) return null
  const supported = new Set(Object.values(item.parameters).filter(s => s.role && referenceFields[s.role]).map(s => referenceFields[s.role!]))
  if (Object.values(referenceFields).some(field => form[field] && !supported.has(field))) return null
  if (!item.selectable || !form.positivePrompt || !form.checkpoint) return null
  const values: Record<string, unknown> = { ...form, workflowId: item.id, workflowKind: item.kind, definitionVersion: item.definitionVersion, definitionDigest: item.definitionDigest }
  for (const key of ['width', 'height', 'steps', 'cfg', 'denoise', 'seed']) {
    const raw = form[key as 'width']
    values[key] = raw.trim() === '' ? undefined : Number(raw)
  }
  for (const role of ['steps', 'cfg', 'seed', 'sampler', 'scheduler', 'denoise']) {
    if (!roleSpec(item, role)) delete values[fields[role]]
  }
  const loras = roleSpec(item, 'loras')
  if (form.loras.length && !loras) return null
  if (loras && (form.loras.length < (loras.min_items ?? 0) || form.loras.length > (loras.max_items ?? 16))) return null
  if (form.loras.some(l => !l.name || [l.strengthModel, l.strengthClip].some(v => v.trim() === '' || !Number.isFinite(Number(v)) || Number(v) < -20 || Number(v) > 20))) return null
  values.loras = form.loras.map(l => ({ ...l, strengthModel: Number(l.strengthModel), strengthClip: Number(l.strengthClip) }))
  for (const [key, spec] of Object.entries(item.parameters)) {
    if (spec.role && !fields[spec.role] && spec.role !== 'loras') return null
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

export function WorkflowPicker({ discovery, form, setForm, disabled = false }: { discovery: Discovery; form: ImageDraft; setForm: React.Dispatch<React.SetStateAction<ImageDraft>>; disabled?: boolean }) {
  const selected = discovery.workflows?.find(item => item.id === form.workflowId)
  const stale = selected && (form.definitionVersion !== selected.definitionVersion || form.definitionDigest !== selected.definitionDigest)
  return <><label>Image template<select disabled={disabled} value={stale ? '__stale' : form.workflowId ?? ''} onChange={event => {
    const item = discovery.workflows?.find(x => x.id === event.target.value)
    const defaults: Record<string, string> = {}
    if (item?.kind === 'registered-comfy') for (const role of ['width', 'height', 'steps', 'cfg', 'sampler', 'scheduler', 'denoise']) {
      const value = roleSpec(item, role)?.default
      if (typeof value === 'number' || typeof value === 'string') defaults[fields[role]] = String(value)
    }
    setForm(old => ({ ...old, ...defaults, workflowId: item?.id, workflowKind: item?.kind, definitionVersion: item?.definitionVersion, definitionDigest: item?.definitionDigest,
      additionalParameters: item ? Object.fromEntries(Object.entries(item.parameters).filter(([,v]) => (item.recipeSchema === 10 ? v.role !== "managed_input" : !v.role) && v.default !== undefined).map(([k,v]) => [k,v.default])) : undefined,
      checkpoint: item?.kind === "registered-comfy" && typeof roleSpec(item, "checkpoint")?.default === "string" ? roleSpec(item, "checkpoint")!.default as string : old.checkpoint,
      imageInputs: {},
      referenceInputId: old.referenceInputId }))
  }}><option value="">Automatic builtin (LoRA aware)</option>{stale && <option value="__stale" disabled>Previous selection (reselect below)</option>}
    {form.workflowId && !selected && <option value={form.workflowId} disabled>{form.workflowId} (unavailable)</option>}
    {discovery.workflows?.map(item => <option key={item.id} value={item.id} disabled={!item.selectable}>{item.name}{item.selectable ? '' : ' (unavailable)'}</option>)}
  </select></label>
    {selected?.reason === "reference_models_unavailable" && <p role="alert">Required reference models are missing from the generation service. Check the model setup below.</p>}
    {selected?.reason === "reference_storage_unavailable" && <p role="alert">Shared reference image storage is unavailable on the generation service.</p>}
    {selected?.kind === "registered-comfy" && <details><summary>Reference model setup</summary><ul>{Object.entries(selected.parameters).filter(([,spec]) => ['ipadapter', 'clip_vision', 'controlnet'].includes(spec.model_kind ?? '')).map(([key,spec]) => <li key={key}>{parameterLabel(key)}: {String(spec.default ?? '')}</li>)}</ul><a href="https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/REFERENCE_IMAGES.md" target="_blank" rel="noreferrer">Model and workflow setup</a></details>}
    {selected?.kind === "registered-comfy" && <small>Choose reference images below. This workflow has not yet been verified on the generation service.</small>}
    {selected && selected.recipeSchema !== 10 && Object.entries(selected.parameters).filter(([,spec]) => !spec.role && !(spec.enum?.length === 1 && typeof spec.default === "string")).map(([key,spec]) => <label key={key}>{parameterLabel(key)}
      {spec.enum ? <select value={String(form.additionalParameters?.[key] ?? '')} onChange={e => setForm(old => ({ ...old, additionalParameters: { ...old.additionalParameters, [key]: spec.enum?.find(x => String(x) === e.target.value) } }))}>{spec.enum.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select> :
      <input min={spec.minimum} max={spec.maximum} step={spec.type === "number" ? "any" : spec.multiple_of ?? 1} type={spec.type === 'boolean' ? 'checkbox' : ['integer','number'].includes(spec.type) ? 'number' : 'text'} value={spec.type === 'boolean' ? undefined : String(form.additionalParameters?.[key] ?? '')} checked={spec.type === 'boolean' ? Boolean(form.additionalParameters?.[key]) : undefined}
        onChange={e => setForm(old => ({ ...old, additionalParameters: { ...old.additionalParameters, [key]: spec.type === 'boolean' ? e.target.checked : ['integer','number'].includes(spec.type) ? Number(e.target.value) : e.target.value } }))} />}
    </label>)}
  </>
}

const parameterLabel = (key: string) => (({ character_strength: "Character influence", style_strength: "Style influence", control_strength: "Pose / structure influence", character_start: "Character start", character_end: "Character end", style_start: "Style start", style_end: "Style end", control_start: "Structure start", control_end: "Structure end", character_adapter: "Character reference model", style_adapter: "Style reference model", clip_vision: "Reference image encoder", controlnet: "Pose / structure model" } as Record<string, string>)[key] ?? key)

export function ReferencePicker({ enabled, inputId, csrf, onChange, onBusy, label = "Reference Image", help, disabled = false, onAvailability }: { enabled: boolean; inputId?: string | null; csrf: string; onChange: (id: string | undefined) => void; onBusy: (busy: boolean) => void; label?: string; help?: string; disabled?: boolean; onAvailability?: (available: boolean) => void }) {
  const [input, setInput] = useState<ManagedInput | null>(null)
  const [assets, setAssets] = useState<Asset[]>([])
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [missingPreview, setMissingPreview] = useState(false)
  const picker = useRef<HTMLInputElement>(null), active = useRef(true), working = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  function start() {
    if (working.current || disabled) return false
    working.current = true; setBusy(true); onBusy(true); setError('')
    return true
  }
  function finish() { working.current = false; if (active.current) { setBusy(false); onBusy(false) } }
  useEffect(() => {
    let active = true
    setInput(null); setMissingPreview(false); onAvailability?.(!inputId)
    if (!inputId) return () => { active = false }
    const refresh = () => api.input(inputId).then(value => {
      if (active) { setInput(value); onAvailability?.(value.available) }
    }).catch(() => { if (active) { setError('Reference unavailable. Upload or choose another image.'); onAvailability?.(false) } })
    refresh()
    const timer = window.setInterval(refresh, 30000)
    return () => { active = false; clearInterval(timer) }
  }, [inputId])
  async function load(offset = 0) {
    if (!start()) return
    try { const page = await api.assets(offset); if (active.current) { setAssets(old => [...(offset ? old : []), ...page.items.filter(a => a.mediaKind === 'image' && imageUploadTypes.includes(a.mimeType) && (a.sizeBytes === null || a.sizeBytes <= imageUploadLimit))]); setNextOffset(page.nextOffset); setOpen(true) } }
    catch { if (active.current) setError('Could not load Assets.') } finally { finish() }
  }
  async function attach(asset: Asset) {
    if (!start()) return
    try { const value = await api.createInput(asset.id, csrf); if (active.current) { onChange(value.id); setOpen(false) } }
    catch { if (active.current) setError('Could not attach this Asset. No automatic retry was made.') } finally { finish() }
  }
  async function upload(files: File[]) {
    if (working.current || disabled) return
    if (files.length !== 1 || !imageUploadTypes.includes(files[0].type) || files[0].size < 1 || files[0].size > imageUploadLimit) {
      setError('Choose one PNG, JPEG or WebP image up to 8 MiB.'); return
    }
    if (!start()) return
    try { const value = await api.uploadInput(files[0], csrf); if (active.current) { onChange(value.id); setOpen(false) } }
    catch { if (active.current) setError('Could not upload this image. Your draft is preserved. No automatic retry was made.') }
    finally { finish() }
  }
  return <div className="reference-note"><strong>{label}</strong>
    <p>{help ?? "These image templates do not use a reference image. Saved images remain available."}</p>
    <div className="reference-upload" role="group" aria-label="Upload reference image" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); void upload(Array.from(event.dataTransfer.files)) }}>
      <input ref={picker} type="file" aria-label="Reference image file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" hidden disabled={busy || disabled} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; if (files.length) void upload(files) }} />
      <button type="button" disabled={busy || disabled} onClick={() => picker.current?.click()}>{inputId ? 'Replace with upload' : 'Upload image'}</button>
      <small>Or drop one PNG, JPEG or WebP here (up to 8 MiB). Reference images are private and expire after 24 hours.</small>
    </div>
    {busy && <p role="status">Preparing reference…</p>}
    {input && <div>{input.thumbnailUrl && !missingPreview ? <img width="128" src={input.thumbnailUrl} alt="Reference image" onError={() => setMissingPreview(true)} /> : <span>Preview unavailable</span>}{!input.available && <p>Reference expired or revoked. Upload or choose another image.</p>}</div>}
    {inputId && <button type="button" disabled={busy || disabled} onClick={() => { onChange(undefined); setError('') }}>Remove reference</button>}
    <button type="button" disabled={!enabled || busy || disabled} onClick={() => load()}>{inputId ? 'Replace from Assets' : 'Choose from Assets'}</button>
    {open && <div role="dialog" aria-label="Choose reference Asset">{assets.length ? assets.map(asset => <button type="button" key={asset.id} disabled={busy || disabled} onClick={() => attach(asset)}>{asset.displayName}</button>) : <p>No eligible images. Generate an image first.</p>}{nextOffset !== null && <button type="button" disabled={busy || disabled} onClick={() => load(nextOffset)}>More Assets</button>}<button type="button" onClick={() => setOpen(false)}>Close picker</button></div>}
    {error && <p role="alert">{error}</p>}
  </div>
}



export function DeclaredFields({ item, form, setForm, csrf, busy, referenceBusy, busyReferences, onAvailability, onBusy }: { item: Workflow; form: ImageDraft; setForm: React.Dispatch<React.SetStateAction<ImageDraft>>; csrf: string; busy: boolean; referenceBusy: boolean; busyReferences: Record<string, boolean>; onAvailability: (key: string, available: boolean) => void; onBusy: (key: string, busy: boolean) => void }) {
  const update = (key: string, value: unknown) => setForm(old => ({ ...old, additionalParameters: { ...old.additionalParameters, [key]: value } }))
  return <div className="declared-image-fields">{Object.entries(item.parameters).map(([key, spec]) => spec.role === 'managed_input' ?
    <ReferencePicker key={`${item.id}-${key}`} label={spec.label ?? key} enabled inputId={form.imageInputs?.[key]} csrf={csrf}
      help="Choose the image to use for this edit." disabled={busy || referenceBusy && !busyReferences[key]}
      onChange={id => { setForm(old => { const images = { ...old.imageInputs }; if (id) images[key] = id; else delete images[key]; return { ...old, imageInputs: images } }); onAvailability(key, !id) }}
      onAvailability={available => onAvailability(key, available)} onBusy={working => onBusy(key, working)} /> :
    <label key={key}>{spec.label ?? key}{spec.role === 'prompt' ?
      <textarea rows={5} required={spec.required} minLength={spec.min_length} maxLength={spec.max_length} value={String(form.additionalParameters?.[key] ?? spec.default ?? '')} onChange={e => update(key, e.target.value)} /> : spec.enum ?
      <select disabled={busy} value={String(form.additionalParameters?.[key] ?? spec.default ?? '')} onChange={e => update(key, spec.enum!.find(value => String(value) === e.target.value))}><option value="" disabled>Choose a value</option>{spec.enum.map((value, i) => <option key={i} value={String(value)}>{String(value)}</option>)}</select> :
      <input disabled={busy} required={spec.required} min={spec.minimum} max={spec.maximum} minLength={spec.min_length} maxLength={spec.max_length} step={spec.type === 'number' ? 'any' : 1}
        type={spec.type === 'boolean' ? 'checkbox' : ['integer', 'number'].includes(spec.type) ? 'number' : 'text'}
        checked={spec.type === 'boolean' ? Boolean(form.additionalParameters?.[key] ?? spec.default) : undefined}
        value={spec.type === 'boolean' ? undefined : String(form.additionalParameters?.[key] ?? spec.default ?? '')}
        onChange={e => update(key, spec.type === 'boolean' ? e.target.checked : ['integer', 'number'].includes(spec.type) ? e.target.value === '' ? '' : Number(e.target.value) : e.target.value)} />}</label>)}</div>
}
