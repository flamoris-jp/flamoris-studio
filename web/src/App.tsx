import { useEffect, useRef, useState } from 'react'
import { api, type Session, type Discovery, type Execution, type ImageSettings, type ImageStyle, type ImagePreferences, type Workflow } from './api'
import Gallery, { ResultPreview } from './Gallery'
import { WorkflowPicker, ReferencePicker, workflowPayload, roleSpec, legalSeed } from './WorkflowImage'
import Assistant from './Assistant'
import Intelligence from './Intelligence'
import Music from './Music'
import Speech, { initialSpeechDraft, type SpeechDraft } from './Speech'

const sections = ['Image', 'Assistant', 'Intelligence', 'Video', 'Music', 'Speech', 'Assets'] as const
type ImageForm = ImageSettings & { sampler: string; scheduler: string; denoise: number; loras: NonNullable<ImageSettings['loras']> }
type OptionalWorkflowFields = 'steps' | 'cfg' | 'sampler' | 'scheduler' | 'denoise'
export type ImageSubmission = Omit<ImageForm, 'seed' | OptionalWorkflowFields> & Partial<Pick<ImageForm, OptionalWorkflowFields>> & { seed?: number }
type NumericKey = 'width' | 'height' | 'steps' | 'cfg' | 'seed' | 'denoise'
export type ImageDraft = Omit<ImageForm, NumericKey | 'loras'> & Record<NumericKey, string> & {
  loras: { name: string; strengthModel: string; strengthClip: string }[]
}
const numberValue = (value: string) => value.trim() === '' ? NaN : Number(value)
const integer = (value: string, min: number, max: number) =>
  value.trim() !== '' && Number.isSafeInteger(numberValue(value)) && numberValue(value) >= min && numberValue(value) <= max
const decimal = (value: string, min: number, max: number) =>
  value.trim() !== '' && Number.isFinite(numberValue(value)) && numberValue(value) >= min && numberValue(value) <= max
const token = (value: string) => /^[a-zA-Z0-9_]{1,80}$/.test(value)
export const initialImageDraft = (checkpoint = ''): ImageDraft => ({ positivePrompt: '', negativePrompt: '', width: '512', height: '512', steps: '20', cfg: '7', seed: '', denoise: '1', sampler: 'euler', scheduler: 'normal', checkpoint, loras: [] })
export function restoreImageDraft(old: ImageDraft, settings: Partial<ImageSettings>): ImageDraft {
  const numeric = (key: NumericKey) => typeof settings[key] === 'number' ? String(settings[key]) : old[key]
  return { ...old,
    workflowId: settings.workflowId, workflowKind: settings.workflowKind,
    definitionVersion: settings.definitionVersion, definitionDigest: settings.definitionDigest,
    referenceInputId: settings.referenceInputId, additionalParameters: settings.additionalParameters,
    positivePrompt: typeof settings.positivePrompt === 'string' ? settings.positivePrompt : old.positivePrompt,
    negativePrompt: typeof settings.negativePrompt === 'string' ? settings.negativePrompt : old.negativePrompt,
    checkpoint: typeof settings.checkpoint === 'string' ? settings.checkpoint : old.checkpoint,
    width: numeric('width'), height: numeric('height'), steps: numeric('steps'), cfg: numeric('cfg'), seed: numeric('seed'), denoise: numeric('denoise'),
    sampler: typeof settings.sampler === 'string' ? settings.sampler : old.sampler,
    scheduler: typeof settings.scheduler === 'string' ? settings.scheduler : old.scheduler,
    loras: Array.isArray(settings.loras) ? settings.loras.map(lora => ({ name: lora.name, strengthModel: String(lora.strengthModel), strengthClip: String(lora.strengthClip) })) : old.loras,
  }
}
export function withRandomSeed(old: ImageDraft, entropy: Uint32Array): ImageDraft {
  const candidate = entropy[0] * 2097152 + (entropy[1] & 2097151)
  return { ...old, seed: String(String(candidate) === old.seed ? (candidate + 1) % Number.MAX_SAFE_INTEGER : candidate) }
}

export function imagePayload(form: ImageDraft): ImageSubmission | null {
  if (!form.positivePrompt.trim() || !form.checkpoint ||
      !integer(form.width, 64, 4096) || numberValue(form.width) % 8 !== 0 ||
      !integer(form.height, 64, 4096) || numberValue(form.height) % 8 !== 0 ||
      !integer(form.steps, 1, 150) || (form.seed.trim() !== '' && !integer(form.seed, 0, Number.MAX_SAFE_INTEGER)) ||
      !decimal(form.cfg, 0, 100) || !decimal(form.denoise, 0, 1) || !token(form.sampler) || !token(form.scheduler) ||
      form.loras.length > 16 || form.loras.some(lora => !lora.name ||
        !decimal(lora.strengthModel, -20, 20) || !decimal(lora.strengthClip, -20, 20))) return null
  return { ...form, width: numberValue(form.width), height: numberValue(form.height),
    steps: numberValue(form.steps), cfg: numberValue(form.cfg), seed: form.seed.trim() === '' ? undefined : numberValue(form.seed), denoise: numberValue(form.denoise),
    loras: form.loras.map(lora => ({ ...lora, strengthModel: numberValue(lora.strengthModel), strengthClip: numberValue(lora.strengthClip) })) }
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [section, setSection] = useState<string>('Image')
  const [discovery, setDiscovery] = useState<Discovery | null>(null)
  const [execution, setExecution] = useState<Execution | null>(null)
  const [resultSettings, setResultSettings] = useState<Partial<ImageSettings> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<ImageDraft>(() => initialImageDraft())
  const [speechForm, setSpeechForm] = useState<SpeechDraft>(() => initialSpeechDraft())
  const [speechExecution, setSpeechExecution] = useState<Execution | null>(null)
  const [musicExecution, setMusicExecution] = useState<Execution | null>(null)
  const [preferencesReady, setPreferencesReady] = useState(false)
  const [preferencesError, setPreferencesError] = useState('')
  const accountEpoch = useRef(0)
  const signingOut = useRef(false)
  const imageDraftEpoch = useRef(0)
  const currentAccount = (version: number) => accountEpoch.current === version && !signingOut.current
  useEffect(() => {
    signingOut.current = false
    accountEpoch.current += 1
    return () => { accountEpoch.current += 1 }
  }, [session?.authenticated, session?.accountKey ?? session?.userName])
  useEffect(() => { api.session().then(setSession).catch(() => setError('Studio is unavailable.')) }, [])
  useEffect(() => {
    if (!session?.authenticated) return
    let active = true
    const version = accountEpoch.current
    api.discovery().then(value => { if (active && currentAccount(version)) setDiscovery(value) })
      .catch(() => { if (active && currentAccount(version)) setDiscovery({ available: false, checkpoints: [], loras: [], templates: [] }) })
    return () => { active = false }
  }, [session?.authenticated, session?.accountKey ?? session?.userName])
  useEffect(() => { if (discovery?.checkpoints.length) setForm(old => old.checkpoint ? old : { ...old, checkpoint: discovery.checkpoints[0].name }) }, [discovery])
  useEffect(() => {
    if (!session?.authenticated) { setPreferencesReady(false); return }
    let active = true
    setPreferencesReady(false)
    const draftVersion = imageDraftEpoch.current
    api.imagePreferences().then(preferences => {
      if (!active) return
      if (draftVersion === imageDraftEpoch.current) setForm(old => ({ ...old, ...Object.fromEntries(Object.entries(preferences).map(([key, value]) => [key, String(value)])) }))
      setPreferencesReady(true)
    }).catch(() => { if (active) setPreferencesError('Could not load Image defaults.') })
    return () => { active = false }
  }, [session?.authenticated, session?.userName])
  useEffect(() => {
    if (!preferencesReady || !session?.authenticated) return
    const values = { width: form.width, height: form.height, steps: form.steps, cfg: form.cfg }
    if (!integer(values.width, 64, 4096) || Number(values.width) % 8 ||
      !integer(values.height, 64, 4096) || Number(values.height) % 8 ||
      !integer(values.steps, 1, 150) || !decimal(values.cfg, 0, 100)) return
    const timer = window.setTimeout(() => {
      const preferences: ImagePreferences = { width: Number(values.width), height: Number(values.height), steps: Number(values.steps), cfg: Number(values.cfg) }
      api.saveImagePreferences(preferences, session.csrfToken).catch(() => setPreferencesError('Could not save Image defaults.'))
    }, 500)
    return () => window.clearTimeout(timer)
  }, [preferencesReady, session?.authenticated, session?.userName, session?.csrfToken, form.width, form.height, form.steps, form.cfg])
  const useSettings = (settings: Partial<ImageSettings>) => {
    if (!Object.keys(settings).length) { setError('Saved Image settings are unavailable.'); return }
    setForm(old => restoreImageDraft(old, settings)); setSection('Image'); window.location.hash = ''
  }
  useEffect(() => {
    if (!session?.authenticated) return
    let active = true
    const version = accountEpoch.current
    const id = window.location.hash.match(/^#execution\/([0-9a-f-]{36})$/)?.[1]
    if (id) api.result(id).then(value => {
      if (!active || !currentAccount(version)) return
      if (value.category === 'speech') { setSpeechExecution(value); setSection('Speech') }
      else if (value.category === 'music') { setMusicExecution(value); setSection('Music') }
      else setExecution(value)
    }).catch(() => {
      if (active && currentAccount(version)) { window.location.hash = ''; setError('Saved execution is unavailable.') }
    })
    return () => { active = false }
  }, [session?.authenticated, session?.accountKey ?? session?.userName])
  useEffect(() => {
    if (!execution || ['completed', 'failed', 'cancelled', 'busy', 'submission_unknown'].includes(execution.state)) return
    let active = true, inflight = false
    const version = accountEpoch.current
    const timer = window.setInterval(() => {
      if (document.hidden || inflight || !currentAccount(version)) return
      inflight = true
      api.execution(execution.id).then(async value => {
        if (!active || !currentAccount(version)) return
        if (value.state === 'completed') {
          try {
            const result = await api.result(value.id)
            if (active && currentAccount(version)) setExecution(result)
          } catch {
            if (active && currentAccount(version)) { setExecution(value); setError('Result is temporarily unavailable.') }
          }
        } else setExecution(value)
      }).catch(() => { if (active && currentAccount(version)) setError('Status is temporarily unavailable.') })
        .finally(() => { inflight = false })
    }, 2500)
    return () => { active = false; clearInterval(timer) }
  }, [execution?.id, execution?.state, session?.authenticated, session?.accountKey ?? session?.userName])
  if (!session) return <div className="center"><h1>FLAMORIS Studio</h1><p>{error || 'Connecting…'}</p></div>
  if (!session.authenticated) return <Account allowRegistration={session.allowRegistration} onReady={setSession} />
  return <div className="layout"><aside><div className="brand">✦ <strong>FLAMORIS</strong><small>STUDIO</small></div><p className="eyebrow">WORKSPACE</p>
    <nav aria-label="Creative domains">{sections.map(name => <button key={name} aria-current={section === name ? 'page' : undefined} onClick={() => setSection(name)}>{name}</button>)}</nav>
    <div className="account-footer"><span>{session.userName}</span><button onClick={() => setSection('Account')}>Account settings</button><button onClick={async () => { signingOut.current = true; try { await api.logout(session.csrfToken); accountEpoch.current += 1; try { sessionStorage.removeItem(`flamoris.speech.pending:${session.accountKey ?? session.userName ?? ''}`); sessionStorage.removeItem(`flamoris.music.pending:${session.accountKey ?? session.userName ?? ''}`) } catch {} setForm(initialImageDraft()); setSpeechForm(initialSpeechDraft()); setSpeechExecution(null); setMusicExecution(null); setExecution(null); setResultSettings(null); setDiscovery(null); setBusy(false); setError(''); window.location.hash = ''; setSession(await api.session()) } catch { signingOut.current = false; setError('Could not sign out.') } }}>Sign out</button></div></aside>
    <main><header><div><span className="eyebrow">CREATIVE CONTROL PLANE</span><h1>{section}</h1></div><span className="badge">PHASE 1A</span></header>
    <div hidden={section !== 'Intelligence'}><Intelligence key={session.userName} csrf={session.csrfToken} active={section === 'Intelligence'} /></div>
    <div hidden={section !== 'Speech'}><Speech key={session.accountKey ?? session.userName} csrf={session.csrfToken} accountKey={session.accountKey ?? session.userName ?? ''} form={speechForm} setForm={setSpeechForm} execution={speechExecution} onExecution={setSpeechExecution} authorized={() => !signingOut.current} /></div>
    <div hidden={section !== 'Music'}><Music key={session.accountKey ?? session.userName} csrf={session.csrfToken} accountKey={session.accountKey ?? session.userName ?? ''} execution={musicExecution} onExecution={setMusicExecution} authorized={() => !signingOut.current} /></div>
    {section === 'Intelligence' || section === 'Speech' || section === 'Music' ? null : section === 'Account' ? <AccountSettings session={session} onChanged={setSession} /> : section === 'Assets' ? <Gallery csrf={session.csrfToken} onUseSettings={useSettings} /> : section === 'Assistant' ? <Assistant key={session.userName} csrf={session.csrfToken} initiallyOpen /> : section === 'Image' ? <div className="image-workspace"><div><p>Turn a prompt into something you can keep.</p>
      {preferencesError && <p role="alert" className="error">{preferencesError}</p>}
      {!discovery?.available && <section className="panel"><h2>Image generation unavailable</h2><p>You can prepare a reference below. Generation requires an available image service.</p><button onClick={() => { const version = accountEpoch.current; api.discovery().then(value => { if (currentAccount(version)) setDiscovery(value) }).catch(() => { if (currentAccount(version)) setError('Service unavailable.') }) }}>Check again</button></section>}
      <ImageEditor discovery={discovery ?? { available: false, checkpoints: [], loras: [], templates: [] }} busy={busy} form={form} setForm={setForm} csrf={session.csrfToken} onReset={() => { imageDraftEpoch.current += 1; setError('') }} onSubmit={async form => {
        const version = accountEpoch.current
        setBusy(true); setError(''); setExecution(null); setResultSettings(null)
        try { const created = await api.submit(form, session.csrfToken); if (currentAccount(version)) { setExecution(created); window.location.hash = `execution/${created.id}` } }
        catch (e) { if (currentAccount(version)) { setError(e instanceof Error ? e.message : 'Generation failed.'); api.discovery().then(value => { if (currentAccount(version)) setDiscovery(value) }).catch(() => {}) } }
        finally { if (currentAccount(version)) setBusy(false) }
      }} />
      {error && <p role="alert" className="error">{error}</p>}
      {execution && execution.category !== 'speech' && <section className="panel results"><div className="row"><div><span className="eyebrow">CURRENT EXECUTION</span><h2>Result</h2></div><span className="badge">{execution.state.replace('_', ' ')}</span></div>
        {['queued', 'running', 'submitting', 'cancel_requested'].includes(execution.state) && <button onClick={async () => { const version = accountEpoch.current; try { const value = await api.cancel(execution.id, session.csrfToken); if (currentAccount(version)) setExecution(value) } catch (e) { if (currentAccount(version)) setError(e instanceof Error ? e.message : 'Cancellation failed.') } }}>Request cancellation</button>}
        {execution.state === 'submission_unknown' && <p>Submission could not be confirmed. No automatic retry was made.</p>}
        {execution.state === 'completed' && <div className="assets">{execution.assets.length ? execution.assets.map(asset => <article key={asset.id}>
          <div><ResultPreview item={asset} /></div>
          <div><strong>{asset.displayName}</strong><small>{asset.mimeType} · {asset.sizeBytes === null ? 'Size pending' : `${(asset.sizeBytes / 1024 / 1024).toFixed(1)} MB`}</small>
            <a href={asset.downloadUrl}>Download ↓</a>{asset.origin !== 'external' && asset.mediaKind === 'image' && ['image/png','image/jpeg','image/webp'].includes(asset.mimeType) && <><button onClick={async () => { const version = accountEpoch.current; try { const detail = await api.asset(asset.id); if (!currentAccount(version)) return; if (!Object.keys(detail.settings).length) { setError('Saved Image settings are unavailable.'); return } setResultSettings(detail.settings) } catch { if (currentAccount(version)) setError('Could not load settings.') } }}>View settings</button><button onClick={async () => { const version = accountEpoch.current, draftVersion = imageDraftEpoch.current; try { const detail = await api.asset(asset.id); if (currentAccount(version) && draftVersion === imageDraftEpoch.current) useSettings(detail.settings) } catch { if (currentAccount(version)) setError('Could not restore settings.') } }}>Use settings ↗</button></>}</div></article>) : <p>No files were returned.</p>}</div>}
        {resultSettings && <dl className="settings-list">{Object.entries(resultSettings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{Array.isArray(value) ? value.map((lora, index) => `${index + 1}. ${lora.name} (model ${lora.strengthModel}, CLIP ${lora.strengthClip})`).join('\n') || 'None' : String(value)}</dd></div>)}</dl>}
      </section>}
    </div><Assistant key={session.userName} csrf={session.csrfToken} draft={form} /></div> : <section className="panel"><span className="eyebrow">COMING LATER</span><h2>{section} is unavailable</h2><p>This editor will connect when its MCP capability is ready.</p></section>}</main></div>
}

function AccountSettings({ session, onChanged }: { session: Session; onChanged: (session: Session) => void }) {
  const [email, setEmail] = useState(session.userName ?? '')
  const [password, setPassword] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')
  const [busy, setBusy] = useState(false)
  return <section className="panel"><h2>Login email</h2><p>Current email: {session.userName}</p>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setMessage('')
      try {
        await api.changeEmail(email, password, session.csrfToken)
        onChanged(await api.session())
        setPassword('')
        setMessage('Email updated.')
      } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not update email.') }
      finally { setBusy(false) }
    }}>
      <label>New email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} /></label>
      <label>Current password<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
      {message && <p role="status">{message}</p>}
      <button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Update email'}</button>
    </form>
    <h2>Change password</h2><p>Other signed-in sessions will be signed out.</p>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setPasswordMessage('')
      try {
        await api.changePassword(currentPassword, newPassword, confirmPassword, session.csrfToken)
        setCurrentPassword(''); setNewPassword(''); setConfirmPassword('')
        setPasswordMessage('Password updated. Other sessions have been signed out.')
      } catch (e) { setPasswordMessage(e instanceof Error ? e.message : 'Could not update password.') }
      finally { setBusy(false) }
    }}>
      <label>Current password<input type="password" autoComplete="current-password" required value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} /></label>
      <label>New password<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label>
      <label>Confirm new password<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} /></label>
      {passwordMessage && <p role="status">{passwordMessage}</p>}
      <button className="primary" disabled={busy || newPassword !== confirmPassword}>{busy ? 'Saving…' : 'Update password'}</button>
    </form>
  </section>
}

function Account({ allowRegistration, onReady }: { allowRegistration: boolean; onReady: (session: Session) => void }) {
  const [email, setEmail] = useState(''), [password, setPassword] = useState(''), [register, setRegister] = useState(false)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  return <div className="center"><div className="login panel"><div className="brand">✦ <strong>FLAMORIS</strong><small>STUDIO</small></div>
    <h1>{register ? 'Create account' : 'Welcome back'}</h1><p>Sign in to your private Studio workspace.</p>
    <form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try {
      const session = await api.session(); await api.authenticate(register ? 'register' : 'login', email, password, session.csrfToken)
      onReady(await api.session())
    } catch (e) { setError(e instanceof Error ? e.message : 'Sign in failed.') } finally { setBusy(false) } }}>
      <label>Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} /></label>
      <label>Password<input type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={register ? 12 : undefined} value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{register ? 'Create account' : 'Sign in'}</button></form>
    {allowRegistration && <button className="text-button" onClick={() => { setRegister(!register); setError('') }}>{register ? 'Have an account? Sign in' : 'Need an account? Register'}</button>}
  </div></div>
}

export function ImageEditor({ discovery, onSubmit, onReset, busy, form, setForm, csrf }: { discovery: Discovery; onSubmit: (form: ImageSubmission) => void; onReset?: () => void; busy: boolean; form: ImageDraft; setForm: React.Dispatch<React.SetStateAction<ImageDraft>>; csrf: string }) {
  const [styles, setStyles] = useState<ImageStyle[]>([])
  const [selectedStyle, setSelectedStyle] = useState('')
  const [styleName, setStyleName] = useState('')
  const [styleError, setStyleError] = useState('')
  const [styleBusy, setStyleBusy] = useState(false)
  const [referenceBusy, setReferenceBusy] = useState(false)
  const [resetVersion, setResetVersion] = useState(0)
  const positivePrompt = useRef<HTMLTextAreaElement>(null)
  function clearInputs() {
    if (busy || referenceBusy || styleBusy) return
    onReset?.()
    setForm(initialImageDraft(discovery.checkpoints[0]?.name ?? ''))
    setSelectedStyle(''); setStyleName(''); setStyleError('')
    setResetVersion(old => old + 1)
    positivePrompt.current?.focus()
  }
  const selected = discovery.workflows?.find(item => item.id === form.workflowId)
  const exactSelection = !form.workflowId ? (!form.workflowKind && form.definitionVersion == null && form.definitionDigest == null) : Boolean(selected?.selectable && selected.kind === 'builtin' && form.workflowKind === selected.kind && selected.definitionVersion === form.definitionVersion && selected.definitionDigest === form.definitionDigest)
  const payload = selected ? workflowPayload(form, selected) : form.workflowId ? null : imagePayload(form)
  const spec = (role: string) => roleSpec(selected, role)
  const supports = (role: string) => !selected || Boolean(spec(role))
  const checkpoints = discovery.checkpoints.filter(item => !spec('checkpoint')?.enum || spec('checkpoint')!.enum!.includes(item.name))
  useEffect(() => { api.styles().then(page => setStyles(page.items)).catch(() => setStyleError('Could not load Styles.')) }, [])
  const set = <K extends keyof ImageDraft>(key: K, value: ImageDraft[K]) => setForm(old => ({ ...old, [key]: value }))
  const valid = discovery.available && !referenceBusy && !form.referenceInputId && exactSelection && payload !== null && discovery.checkpoints.some(item => item.name === form.checkpoint) &&
    form.loras.every(lora => discovery.loras.some(item => item.name === lora.name))
  const style = styles.find(item => item.id === selectedStyle)
  async function styleAction(action: 'save' | 'update' | 'duplicate' | 'delete') {
    setStyleError(''); setStyleBusy(true)
    try {
      if (action === 'delete') {
        if (!style || !window.confirm(`Delete Style “${style.name}”?`)) return
        await api.deleteStyle(style.id, csrf)
        setStyles(old => old.filter(item => item.id !== style.id)); setSelectedStyle(''); setStyleName('')
      } else {
        const name = styleName.trim()
        if (!name) { setStyleError('Enter a Style name.'); return }
        const values = { name, positivePrompt: form.positivePrompt, negativePrompt: form.negativePrompt }
        if (action !== 'duplicate' && !values.positivePrompt.trim()) { setStyleError('Enter a Positive prompt first.'); return }
        const saved = action === 'update' && style ? await api.updateStyle(style.id, values, csrf) :
          action === 'duplicate' && style ? await api.duplicateStyle(style.id, name, csrf) : await api.createStyle(values, csrf)
        setStyles(old => [...old.filter(item => item.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)))
        setSelectedStyle(saved.id); setStyleName(saved.name)
      }
    } catch (e) { setStyleError(e instanceof Error ? e.message : 'Style operation failed.') }
    finally { setStyleBusy(false) }
  }
  return <form className="panel editor" onSubmit={event => { event.preventDefault(); if (valid && payload) onSubmit(payload) }}><div className="row"><div><span className="eyebrow">IMAGE GENERATOR</span><h2>Compose your image</h2></div><span className="badge">{discovery.available ? 'Ready' : 'Unavailable'}</span></div>
    <WorkflowPicker discovery={discovery} form={form} setForm={setForm} />
    {form.workflowId && !exactSelection && <p role="alert">This image template is unavailable. Your draft is preserved; select a ready template.</p>}
    <label>Model<select required value={form.checkpoint} onChange={e => set('checkpoint', e.target.value)}><option value="">Choose a model</option>{form.checkpoint && !checkpoints.some(x => x.name === form.checkpoint) && <option value={form.checkpoint} disabled>{form.checkpoint} (unavailable for this template)</option>}{checkpoints.map(x => <option key={x.id} value={x.name}>{x.name}</option>)}</select></label>
    <div className="style-box"><div className="fields"><label>Style<select value={selectedStyle} onChange={e => { const found = styles.find(item => item.id === e.target.value); setSelectedStyle(e.target.value); setStyleName(found?.name ?? ''); if (found) setForm(old => ({ ...old, positivePrompt: found.positivePrompt, negativePrompt: found.negativePrompt })) }}><option value="">Choose a saved Style</option>{styles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Style name<input maxLength={100} value={styleName} onChange={e => setStyleName(e.target.value)} placeholder="Name this prompt recipe" /></label></div>
      <div className="row style-actions"><button type="button" disabled={styleBusy} onClick={() => styleAction('save')}>Save as new</button><button type="button" disabled={!style || styleBusy} onClick={() => styleAction('update')}>Update selected</button><button type="button" disabled={!style || styleBusy} onClick={() => styleAction('duplicate')}>Duplicate</button><button type="button" disabled={!style || styleBusy} onClick={() => styleAction('delete')}>Delete</button></div>
      {styleError && <p role="alert" className="error">{styleError}</p>}<small>Applying a Style changes the prompts; edits afterward are your own draft.</small></div>
    <label>Positive prompt<textarea ref={positivePrompt} required maxLength={20000} rows={8} value={form.positivePrompt} onChange={e => set('positivePrompt', e.target.value)} placeholder="A place, a person, a moment…" /></label>
    <label>Negative prompt<textarea maxLength={20000} rows={5} value={form.negativePrompt} onChange={e => set('negativePrompt', e.target.value)} /></label>
    <ReferencePicker key={`reference-${resetVersion}`} enabled={false} inputId={form.referenceInputId} csrf={csrf} onChange={id => set("referenceInputId", id)} onBusy={setReferenceBusy} />
    {form.referenceInputId && <p role="alert">Remove the reference before generating. Your saved image remains available.</p>}
    <div className="fields four">{(['width', 'height', 'steps', 'cfg'] as const).map(key => <label key={key}>{key}<input disabled={!supports(key)} type="number" min={spec(key)?.minimum ?? (key === 'steps' ? 1 : key === 'cfg' ? 0 : 64)} max={spec(key)?.maximum ?? (key === 'steps' ? 150 : key === 'cfg' ? 100 : 4096)} step={spec(key)?.multiple_of ?? (key === 'cfg' ? 'any' : key === 'steps' ? 1 : 8)} value={form[key]} onChange={e => set(key, e.target.value)} /></label>)}</div>
    <div className="size-presets"><span>Size presets</span>{[[512, 512], [768, 1024], [768, 1152], [768, 1344]].map(([width, height]) => <button type="button" disabled={!supports('width') || !supports('height')} key={`${width}-${height}`} onClick={() => setForm(old => ({ ...old, width: String(width), height: String(height) }))}>{width} × {height}</button>)}</div>
    <div className="fields"><label>Seed (blank = Auto)<input disabled={!supports("seed")} type="number" min={spec("seed")?.minimum ?? 0} max={Math.min(Number.MAX_SAFE_INTEGER, spec("seed")?.maximum ?? Number.MAX_SAFE_INTEGER)} step={spec("seed")?.multiple_of ?? 1} value={form.seed} onChange={e => set('seed', e.target.value)} /></label><button type="button" disabled={!supports("seed")} onClick={() => { if (selected) { const seed = legalSeed(spec("seed")); if (seed !== null) set("seed", String(seed)) } else { const bits = new Uint32Array(2); crypto.getRandomValues(bits); setForm(old => withRandomSeed(old, bits)) } }}>Randomize seed</button><button type="button" onClick={() => set('seed', '')}>Auto seed</button></div>
    <details key={`advanced-${resetVersion}`}><summary>Advanced generation settings</summary><div className="fields"><label>Sampler<input disabled={!supports("sampler")} value={form.sampler} maxLength={80} onChange={e => set('sampler', e.target.value)} /></label><label>Scheduler<input disabled={!supports("scheduler")} value={form.scheduler} maxLength={80} onChange={e => set('scheduler', e.target.value)} /></label><label>Denoise<input disabled={!supports("denoise")} type="number" min={spec("denoise")?.minimum ?? 0} max={spec("denoise")?.maximum ?? 1} step="any" value={form.denoise} onChange={e => set('denoise', e.target.value)} /></label></div></details>
    <div className="row"><strong>LoRA layers</strong><button type="button" disabled={form.loras.length >= 16 || !discovery.loras.length || (selected !== undefined && !spec("loras"))} onClick={() => set('loras', [...form.loras, { name: discovery.loras[0].name, strengthModel: '1', strengthClip: '1' }])}>+ Add LoRA</button></div>
    {form.loras.map((lora, index) => <div className="fields lora" key={index}><label>LoRA {index + 1}<select value={lora.name} onChange={e => set('loras', form.loras.map((x, i) => i === index ? { ...x, name: e.target.value } : x))}>{!discovery.loras.some(x => x.name === lora.name) && <option value={lora.name} disabled>{lora.name} (unavailable)</option>}{discovery.loras.map(x => <option key={x.id} value={x.name}>{x.name}</option>)}</select></label>
      {(['strengthModel', 'strengthClip'] as const).map(key => <label key={key}>{key}<input type="number" min="-20" max="20" step="any" value={lora[key]} onChange={e => set('loras', form.loras.map((x, i) => i === index ? { ...x, [key]: e.target.value } : x))} /></label>)}
      <button type="button" onClick={() => set('loras', form.loras.filter((_, i) => i !== index))}>Remove</button>{index > 0 && <button type="button" onClick={() => set('loras', form.loras.map((x, i) => i === index ? form.loras[index - 1] : i === index - 1 ? lora : x))}>Move up</button>}</div>)}
    {selected && form.loras.length > 0 && !spec('loras') && <p role="alert">This template does not support the LoRA draft. Remove the layers or select a compatible template.</p>}
    <small id="image-clear-help">Clear inputs resets prompts, reference, template, LoRAs and generation settings.</small>
    <div className="row actions"><button type="button" aria-describedby="image-clear-help" disabled={busy || referenceBusy || styleBusy} onClick={clearInputs}>Clear inputs</button><button className="primary" disabled={!valid || busy}>{busy ? 'Submitting…' : 'Generate image ↗'}</button></div>
  </form>
}
