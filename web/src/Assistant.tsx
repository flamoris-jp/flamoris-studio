import { useEffect, useRef, useState } from 'react'
import { api, AssistantFailure, type AssistantAvailability, type AssistantAnswer } from './api'
import type { ImageDraft } from './App'

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
    if (!open || sending) return
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
      } catch { if (active) setState({ available: false, state: 'unavailable' }) }
      finally { pending = false }
    }
    void check()
    const poll = window.setInterval(() => { setClock(Date.now()); void check() }, 3000)
    const visibility = () => { setClock(Date.now()); void check() }
    document.addEventListener('visibilitychange', visibility)
    return () => { active = false; clearInterval(poll); document.removeEventListener('visibilitychange', visibility) }
  }, [open, sending, refresh, csrf])
  const capturedDraft = draft ? attachedImageDraft(draft) : null
  const ready = state.available && state.state === 'ready' && !!state.sessionKey &&
    !!state.expiresAt && Date.parse(state.expiresAt) > clock
  const validQuestion = question.trim() && new TextEncoder().encode(question).length <= 16384
  return <section className="panel assistant-panel" aria-label="Agent assistant">
    <div className="row"><h2>Agent assistant</h2><button aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? 'Collapse' : 'Ask Agent'}</button></div>
    {open && <><p role="status">{ready ? 'Ready' : state.state === 'ready' ? 'Checking availability' : state.state}</p>
      <button disabled={sending} onClick={() => setRefresh(value => value + 1)}>Check availability</button>
      <p>Text advice. Sending does not change your draft or generate media.</p>
      {draft && <><label className="assistant-attach"><input type="checkbox" checked={attach} disabled={sending || uncertain} onChange={event => setAttach(event.target.checked)} />Attach current Image draft</label>
        {attach && <p>Includes prompts, size, steps, guidance, denoise and an explicit seed. The attached snapshot becomes Agent conversation content.</p>}
        {attach && !capturedDraft && <p role="alert">Complete valid Image settings before attaching the draft.</p>}</>}
      <label>Question<textarea value={question} maxLength={16384} disabled={sending || uncertain} onChange={event => setQuestion(event.target.value)} /></label>
      {notice && <p role="alert">{notice}</p>}
      {uncertain && <p role="alert">The outcome is unconfirmed. No automatic retry was made. Start a new conversation explicitly to send a new question.</p>}
      <button className="primary" disabled={!ready || !validQuestion || sending || uncertain || attach && !capturedDraft}
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
            setUncertain(!(error instanceof AssistantFailure) || error.uncertain)
          } finally { if (alive.current) setSending(false) }
        }}>{sending ? 'Waiting for advice…' : 'Send question'}</button>
      <button disabled={sending} onClick={() => { previous.current = undefined; setAnswer(null); setQuestion(''); setNotice(''); setUncertain(false); setAttach(false); setRefresh(value => value + 1) }}>Start new conversation</button>
      {answer && <div className="assistant-answer"><p>{answer.text}</p><small>{answer.provenance.model} · {answer.provenance.provider}</small></div>}
    </>}
  </section>
}
