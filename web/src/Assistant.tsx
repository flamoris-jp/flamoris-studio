import { useEffect, useRef, useState } from 'react'
import { api, AssistantFailure, HTTPFailure, type AssistantAvailability, type AssistantAnswer } from './api'
import type { ImageDraft } from './App'
import type { AgentModel } from './api'
import PersonalityEditor from './Personality'

export function attachedImageDraft(form: ImageDraft) {
  const draft: Record<string, string | number> = { positive_prompt: form.positivePrompt, negative_prompt: form.negativePrompt }
  for (const key of ['width', 'height', 'steps', 'cfg', 'denoise', 'seed'] as const) {
    if (key === 'seed' && form.seed.trim() === '') continue
    const value = Number(form[key])
    if (!form[key].trim() || !Number.isFinite(value)) return null
    if (['width', 'height'].includes(key) && (!Number.isInteger(value) || value < 64 || value > 4096 || value % 8)) return null
    if (key === 'steps' && (!Number.isInteger(value) || value < 1 || value > 150)) return null
    if (key === 'seed' && (!Number.isSafeInteger(value) || value < 0)) return null
    if (key === 'cfg' && (value < 0 || value > 100)) return null
    if (key === 'denoise' && (value < 0 || value > 1)) return null
    draft[key] = value
  }
  if (new TextEncoder().encode(JSON.stringify(draft)).length > 15500) return null
  return draft
}

export default function Assistant({ csrf, draft, initiallyOpen = false }: { csrf: string; draft?: ImageDraft; initiallyOpen?: boolean }) {
  const [models, setModels] = useState<AgentModel[]>([])
  const [modelId, setModelId] = useState('')
  const [remoteConsent, setRemoteConsent] = useState(false)
  const [switchUncertain, setSwitchUncertain] = useState(false)
  const pendingSwitch = useRef<Parameters<typeof api.assistantSwitch>[0] | null>(null)
  const [selecting, setSelecting] = useState(false)
  const [open, setOpen] = useState(initiallyOpen)
  const [state, setState] = useState<AssistantAvailability>({ available: false, state: 'unknown' })
  const [question, setQuestion] = useState('')
  const [attach, setAttach] = useState(false)
  const [sending, setSending] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null)
  const [notice, setNotice] = useState('')
  const [clock, setClock] = useState(Date.now())
  const [revision, setRevision] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const previous = useRef<string | undefined>(undefined)
  const session = useRef<string | undefined>(undefined)
  const alive = useRef(true)
  const draftJSON = JSON.stringify(draft)
  useEffect(() => { setRevision(value => value + 1) }, [draftJSON])
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!open || sending || selecting) return
    let active = true, pending = false
    const check = async () => {
      if (pending || document.hidden) return
      pending = true
      try {
        const next = await api.assistantAvailability(csrf)
        if (!active) return
        if (session.current && next.sessionKey && session.current !== next.sessionKey) {
          previous.current = undefined; setAnswer(null)
          setNotice('The assistant session changed. Your unsent question is preserved.')
        }
        if (next.sessionKey) session.current = next.sessionKey
        setState(next); setClock(Date.now())
      } catch (error) {
        if (active) {
          setState({ available: false, state: 'unavailable' })
          if (error instanceof HTTPFailure && error.status === 401) {
            previous.current = undefined; setAnswer(null)
          }
        }
      }
      finally { pending = false }
    }
    void check()
    const poll = window.setInterval(() => { setClock(Date.now()); void check() }, 3000)
    const visibility = () => { setClock(Date.now()); void check() }
    document.addEventListener('visibilitychange', visibility)
    return () => { active = false; clearInterval(poll); document.removeEventListener('visibilitychange', visibility) }
  }, [open, sending, selecting, refresh, csrf])
  useEffect(() => {
    if (!open) return
    let active = true
    api.assistantModels(csrf).then(result => { if (active) { setModels(result.models); setModelId(old => old || result.defaultModelId || '') } }).catch(() => { if (active) setModels([]) })
    return () => { active = false }
  }, [open, csrf])
  const selectionMatches = !models.length || state.modelId === modelId && (!remoteConsent || state.remoteConsent === true)
  const remote = models.find(m => m.id === modelId)?.data_flow === 'remote_authorized'
  const capturedDraft = draft ? attachedImageDraft(draft) : null
  const ready = state.available && state.state === 'ready' && !!state.sessionKey &&
    !!state.expiresAt && Date.parse(state.expiresAt) > clock
  const validQuestion = question.trim() && new TextEncoder().encode(question).length <= 16384
  return <section className="panel assistant-panel" aria-label="Agent assistant">
    <div className="row"><h2>Agent assistant</h2><button aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? 'Collapse' : 'Ask Agent'}</button></div>
    {open && <><p role="status">{ready ? 'Ready' : state.state === 'ready' ? 'Checking availability' : state.state}</p>
      <button disabled={sending} onClick={() => setRefresh(value => value + 1)}>Check availability</button>
      {models.length > 0 && <section aria-label="Assistant model selection">
        <label>Conversation model<select value={modelId} disabled={sending || selecting || switchUncertain || uncertain} onChange={e => { setModelId(e.target.value); setRemoteConsent(false) }}><option value="">Select a model</option>{models.map(m => <option key={m.id} value={m.id}>{m.display_name} · {m.data_flow === 'local_only' ? 'Internal LLM' : 'OpenAI API'}</option>)}</select></label>
        {remote && <label><input type="checkbox" checked={remoteConsent} disabled={sending || selecting || switchUncertain || uncertain} onChange={e => setRemoteConsent(e.target.checked)} />Allow this conversation's personality, previous turns, question and explicitly attached draft to be sent to OpenAI</label>}
        <p>You can switch models while keeping this conversation and its personality.</p>
        <button disabled={!modelId || remote && !remoteConsent || sending || selecting || uncertain} onClick={async () => {
          setSelecting(true); setNotice('')
          try {
            const continuing = !!pendingSwitch.current || !!state.sessionKey && !!state.modelId
            if (continuing && !pendingSwitch.current) pendingSwitch.current = { requestId: crypto.randomUUID(), sessionKey: state.sessionKey!, expectedModelId: state.modelId!, modelId, remoteConsent }
            const result = continuing ? await api.assistantSwitch(pendingSwitch.current!, csrf) : await api.assistantStart(modelId, remoteConsent, csrf)
            if (!alive.current) return
            if (!continuing) { previous.current = undefined; setAnswer(null) }
            pendingSwitch.current = null; setSwitchUncertain(false); session.current = result.sessionKey
            setState({ available: false, state: 'unknown', sessionKey: result.sessionKey, modelId: result.modelId, remoteConsent })
            setNotice(continuing ? 'Model switched. This conversation and your unsent question are preserved.' : 'Selected model started. Your unsent question is preserved.'); setRefresh(v => v + 1)
          }
          catch (error) { if (alive.current) { setSwitchUncertain(!!pendingSwitch.current); setNotice(error instanceof Error ? error.message : 'Model selection could not be confirmed.') } }
          finally { if (alive.current) setSelecting(false) }
        }}>{switchUncertain ? 'Check model switch' : state.sessionKey && state.modelId ? 'Switch selected model' : 'Start selected model'}</button>
      </section>}
      {state.modelId && <p>Active conversation model: {models.find(m => m.id === state.modelId)?.display_name ?? state.modelId}</p>}
      <p>Text advice. Sending does not change your draft or generate media.</p>
      {draft && <><label className="assistant-attach"><input type="checkbox" checked={attach} disabled={sending || uncertain} onChange={event => setAttach(event.target.checked)} />Attach current Image draft</label>
        {attach && <p>Includes prompts, size, steps, guidance, denoise and an explicit seed. The attached snapshot becomes Agent conversation content.</p>}
        {attach && !capturedDraft && <p role="alert">Complete valid Image settings before attaching the draft.</p>}</>}
      <label>Question<textarea value={question} maxLength={16384} disabled={sending || uncertain} onChange={event => setQuestion(event.target.value)} /></label>
      {notice && <p role="alert">{notice}</p>}
      {uncertain && <p role="alert">The outcome is unconfirmed. No automatic retry was made. Start a new conversation explicitly to send a new question.</p>}
      <button className="primary" disabled={!ready || !selectionMatches || !validQuestion || sending || selecting || switchUncertain || uncertain || attach && !capturedDraft}
        onClick={async () => {
          if (!state.sessionKey || !ready || sending || uncertain) return
          const payload = JSON.parse(JSON.stringify({ requestId: crypto.randomUUID(), sessionKey: state.sessionKey,
            text: question, previousHandle: previous.current,
            ...(attach && capturedDraft ? { draft: capturedDraft, draftRevision: revision } : {}) }))
          setSending(true); setNotice('')
          try {
            const result = await api.assistantAsk(payload, csrf)
            if (!alive.current) return
            setAnswer(result); previous.current = result.requestHandle; setQuestion('')
          } catch (error) {
            if (!alive.current) return
            setNotice(error instanceof Error ? error.message : 'Assistant unavailable.')
            previous.current = undefined; setAnswer(null)
            setUncertain(!(error instanceof AssistantFailure) || error.uncertain)
          } finally { if (alive.current) setSending(false) }
        }}>{sending ? 'Waiting for advice…' : 'Send question'}</button>
      <button disabled={sending || selecting || models.length > 0 && (!modelId || remote && !remoteConsent)} onClick={async () => {
        setSelecting(true)
        try {
          if (models.length > 0) {
            const result = await api.assistantStart(modelId, remoteConsent, csrf)
            if (!alive.current) return
            session.current = result.sessionKey
            setState({ available: false, state: 'unknown', sessionKey: result.sessionKey, modelId: result.modelId, remoteConsent })
          }
          previous.current = undefined; pendingSwitch.current = null; setAnswer(null); setQuestion(''); setNotice(''); setUncertain(false); setSwitchUncertain(false); setAttach(false); setRefresh(value => value + 1)
        } catch (error) { if (alive.current) setNotice(error instanceof Error ? error.message : 'New conversation could not be confirmed.') }
        finally { if (alive.current) setSelecting(false) }
      }}>Start new conversation</button>
      {models.length > 0 && <PersonalityEditor csrf={csrf} sessionKey={session.current} />}
      {answer && <div className="assistant-answer"><p>{answer.text}</p><small>{answer.provenance.model} · {answer.provenance.provider}</small></div>}
    </>}
  </section>
}
