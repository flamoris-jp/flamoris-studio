import { useEffect, useState } from 'react'
import { api, type Session, type Discovery, type Execution } from './api'
import Gallery from './Gallery'

const sections = ['Image', 'Generated', 'Intelligence', 'Video', 'Music', 'Speech'] as const
type ImageForm = { positivePrompt: string; negativePrompt: string; width: number; height: number; steps: number; cfg: number; seed: number; checkpoint: string; loras: { name: string; strengthModel: number; strengthClip: number }[] }
type NumericKey = 'width' | 'height' | 'steps' | 'cfg' | 'seed'
type ImageDraft = Omit<ImageForm, NumericKey | 'loras'> & Record<NumericKey, string> & {
  loras: { name: string; strengthModel: string; strengthClip: string }[]
}
const numberValue = (value: string) => value.trim() === '' ? NaN : Number(value)
const integer = (value: string, min: number, max: number) =>
  value.trim() !== '' && Number.isSafeInteger(numberValue(value)) && numberValue(value) >= min && numberValue(value) <= max
const decimal = (value: string, min: number, max: number) =>
  value.trim() !== '' && Number.isFinite(numberValue(value)) && numberValue(value) >= min && numberValue(value) <= max

export function imagePayload(form: ImageDraft): ImageForm | null {
  if (!form.positivePrompt.trim() || !form.checkpoint ||
      !integer(form.width, 64, 4096) || numberValue(form.width) % 8 !== 0 ||
      !integer(form.height, 64, 4096) || numberValue(form.height) % 8 !== 0 ||
      !integer(form.steps, 1, 150) || !integer(form.seed, 0, Number.MAX_SAFE_INTEGER) ||
      !decimal(form.cfg, 0, 100) || form.loras.some(lora => !lora.name ||
        !decimal(lora.strengthModel, -20, 20) || !decimal(lora.strengthClip, -20, 20))) return null
  return { ...form, width: numberValue(form.width), height: numberValue(form.height),
    steps: numberValue(form.steps), cfg: numberValue(form.cfg), seed: numberValue(form.seed),
    loras: form.loras.map(lora => ({ ...lora, strengthModel: numberValue(lora.strengthModel), strengthClip: numberValue(lora.strengthClip) })) }
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [section, setSection] = useState<string>('Image')
  const [discovery, setDiscovery] = useState<Discovery | null>(null)
  const [execution, setExecution] = useState<Execution | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.session().then(setSession).catch(() => setError('Studio is unavailable.')) }, [])
  useEffect(() => { if (session?.authenticated) api.discovery().then(setDiscovery).catch(() => setDiscovery({ available: false, checkpoints: [], loras: [], templates: [] })) }, [session?.authenticated])
  useEffect(() => {
    if (!session?.authenticated) return
    const id = window.location.hash.match(/^#execution\/([0-9a-f-]{36})$/)?.[1]
    if (id) api.result(id).then(setExecution).catch(() => { window.location.hash = ''; setError('Saved execution is unavailable.') })
  }, [session?.authenticated])
  useEffect(() => {
    if (!execution || ['completed', 'failed', 'cancelled', 'busy', 'submission_unknown'].includes(execution.state)) return
    const timer = window.setInterval(() => {
      if (document.hidden) return
      api.execution(execution.id).then(view => {
        setExecution(view)
        if (view.state === 'completed') api.result(view.id).then(setExecution).catch(() => setError('Result is temporarily unavailable.'))
      }).catch(() => setError('Status is temporarily unavailable.'))
    }, 2500)
    return () => clearInterval(timer)
  }, [execution?.id, execution?.state])
  if (!session) return <div className="center"><h1>FLAMORIS Studio</h1><p>{error || 'Connecting…'}</p></div>
  if (!session.authenticated) return <Account allowRegistration={session.allowRegistration} onReady={setSession} />
  return <div className="layout"><aside><div className="brand">✦ <strong>FLAMORIS</strong><small>STUDIO</small></div><p className="eyebrow">WORKSPACE</p>
    <nav aria-label="Creative domains">{sections.map(name => <button key={name} aria-current={section === name ? 'page' : undefined} onClick={() => setSection(name)}>{name}</button>)}</nav>
    <div className="account-footer"><span>{session.userName}</span><button onClick={() => setSection('Account')}>Account settings</button><button onClick={async () => { try { await api.logout(session.csrfToken); setSession(await api.session()); setExecution(null) } catch { setError('Could not sign out.') } }}>Sign out</button></div></aside>
    <main><header><div><span className="eyebrow">CREATIVE CONTROL PLANE</span><h1>{section}</h1></div><span className="badge">PHASE 1A</span></header>
    {section === 'Account' ? <AccountSettings session={session} onChanged={setSession} /> : section === 'Generated' ? <Gallery csrf={session.csrfToken} /> : section === 'Image' ? <><p>Turn a prompt into something you can keep.</p>
      {discovery?.available ? <ImageEditor discovery={discovery} busy={busy} onSubmit={async form => {
        setBusy(true); setError(''); setExecution(null)
        try { const created = await api.submit(form, session.csrfToken); setExecution(created); window.location.hash = `execution/${created.id}` }
        catch (e) { setError(e instanceof Error ? e.message : 'Generation failed.') }
        finally { setBusy(false) }
      }} /> : <section className="panel"><h2>Image generation unavailable</h2><p>The configured generation service has no available image capability.</p><button onClick={() => api.discovery().then(setDiscovery).catch(() => setError('Service unavailable.'))}>Check again</button></section>}
      {error && <p role="alert" className="error">{error}</p>}
      {execution && <section className="panel results"><div className="row"><div><span className="eyebrow">CURRENT EXECUTION</span><h2>Result</h2></div><span className="badge">{execution.state.replace('_', ' ')}</span></div>
        {['queued', 'running', 'submitting', 'cancel_requested'].includes(execution.state) && <button onClick={async () => { try { setExecution(await api.cancel(execution.id, session.csrfToken)) } catch (e) { setError(e instanceof Error ? e.message : 'Cancellation failed.') } }}>Request cancellation</button>}
        {execution.state === 'submission_unknown' && <p>Submission could not be confirmed. No automatic retry was made.</p>}
        {execution.state === 'completed' && <div className="assets">{execution.assets.length ? execution.assets.map(asset => <article key={asset.id}>
          <a href={asset.previewUrl} target="_blank" rel="noreferrer"><img src={asset.hasThumbnail ? asset.thumbnailUrl : asset.previewUrl} alt={asset.displayName} /></a>
          <div><strong>{asset.displayName}</strong><small>{asset.mimeType} · {asset.sizeBytes === null ? 'Size pending' : `${(asset.sizeBytes / 1024 / 1024).toFixed(1)} MB`}</small>
            <a href={asset.downloadUrl}>Download ↓</a></div></article>) : <p>No images were returned.</p>}</div>}
      </section>}
    </> : <section className="panel"><span className="eyebrow">COMING LATER</span><h2>{section} is unavailable</h2><p>This editor will connect when its MCP capability is ready.</p></section>}</main></div>
}

function AccountSettings({ session, onChanged }: { session: Session; onChanged: (session: Session) => void }) {
  const [email, setEmail] = useState(session.userName ?? '')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
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

function ImageEditor({ discovery, onSubmit, busy }: { discovery: Discovery; onSubmit: (form: ImageForm) => void; busy: boolean }) {
  const [form, setForm] = useState<ImageDraft>({ positivePrompt: '', negativePrompt: '', width: '512', height: '512', steps: '20', cfg: '7', seed: '0', checkpoint: discovery.checkpoints[0]?.name ?? '', loras: [] })
  const set = <K extends keyof ImageDraft>(key: K, value: ImageDraft[K]) => setForm(old => ({ ...old, [key]: value }))
  const valid = imagePayload(form) !== null
  return <form className="panel editor" onSubmit={event => { event.preventDefault(); const payload = imagePayload(form); if (payload) onSubmit(payload) }}><div className="row"><div><span className="eyebrow">IMAGE GENERATOR</span><h2>Compose your image</h2></div><span className="badge">Ready</span></div>
    <label>Positive prompt<textarea required maxLength={20000} rows={4} value={form.positivePrompt} onChange={e => set('positivePrompt', e.target.value)} placeholder="A place, a person, a moment…" /></label>
    <label>Negative prompt<textarea maxLength={20000} rows={2} value={form.negativePrompt} onChange={e => set('negativePrompt', e.target.value)} /></label>
    <div className="fields"><label>Checkpoint<select required value={form.checkpoint} onChange={e => set('checkpoint', e.target.value)}><option value="">Choose a model</option>{discovery.checkpoints.map(x => <option key={x.id} value={x.name}>{x.name}</option>)}</select></label>
      <label>Seed<input type="number" min="0" max={Number.MAX_SAFE_INTEGER} step="1" value={form.seed} onChange={e => set('seed', e.target.value)} /></label></div>
    <div className="fields four">{(['width', 'height', 'steps', 'cfg'] as const).map(key => <label key={key}>{key}<input type="number" min={key === 'steps' ? 1 : key === 'cfg' ? 0 : 64} max={key === 'steps' ? 150 : key === 'cfg' ? 100 : 4096} step={key === 'cfg' ? 0.1 : key === 'steps' ? 1 : 8} value={form[key]} onChange={e => set(key, e.target.value)} /></label>)}</div>
    <div className="row"><strong>LoRA layers</strong><button type="button" disabled={form.loras.length >= 16 || !discovery.loras.length} onClick={() => set('loras', [...form.loras, { name: discovery.loras[0].name, strengthModel: '1', strengthClip: '1' }])}>+ Add LoRA</button></div>
    {form.loras.map((lora, index) => <div className="fields lora" key={index}><label>Model<select value={lora.name} onChange={e => set('loras', form.loras.map((x, i) => i === index ? { ...x, name: e.target.value } : x))}>{discovery.loras.map(x => <option key={x.id} value={x.name}>{x.name}</option>)}</select></label>
      {(['strengthModel', 'strengthClip'] as const).map(key => <label key={key}>{key}<input type="number" min="-20" max="20" step="0.1" value={lora[key]} onChange={e => set('loras', form.loras.map((x, i) => i === index ? { ...x, [key]: e.target.value } : x))} /></label>)}
      <button type="button" onClick={() => set('loras', form.loras.filter((_, i) => i !== index))}>Remove</button></div>)}
    <div className="row actions"><span>Uses the available MCP image capability.</span><button className="primary" disabled={!valid || busy}>{busy ? 'Submitting…' : 'Generate image ↗'}</button></div>
  </form>
}
