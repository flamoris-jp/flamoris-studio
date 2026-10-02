import { useEffect, useRef, useState } from 'react'
import { api, IntelligenceFailure, type IntelligenceAnswer, type IntelligenceDiscovery } from './api'

export async function textAttachment(file: File): Promise<string> {
  if (file.size > 16384 || !/\.(txt|md|py|js|ts|tsx|jsx|json|csv|cs|cpp|c|h|html|css|xml|yaml|yml|sql|sh)$/i.test(file.name)) throw new Error('Choose a UTF-8 text/code file no larger than 16 KiB.')
  const buffer = await file.arrayBuffer()
  if (buffer.byteLength > 16384) throw new Error('Attachment is too large.')
  const text = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
  if (text.includes('\0')) throw new Error('Attachment must be text.')
  return `\n\n[Attached text: ${file.name.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 128)}]\n${text}`
}

export default function Intelligence({ csrf, active }: { csrf: string; active: boolean }) {
  const [discovery, setDiscovery] = useState<IntelligenceDiscovery>({ available: false, models: [], capabilities: [] })
  const [model, setModel] = useState('')
  const [operation, setOperation] = useState('text.generate')
  const [input, setInput] = useState('')
  const [instruction, setInstruction] = useState('')
  const [attachments, setAttachments] = useState<string[]>([])
  const [reading, setReading] = useState(false)
  const [tokens, setTokens] = useState('1024')
  const [temperature, setTemperature] = useState('0.7')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [notice, setNotice] = useState('')
  const [answer, setAnswer] = useState<IntelligenceAnswer | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [freshUntil, setFreshUntil] = useState(0)
  const [clock, setClock] = useState(Date.now())
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!active || busy) return
    let mounted = true, pending = false
    const check = async () => {
      if (pending || document.hidden) return
      pending = true
      try {
        const next = await api.intelligenceDiscovery()
        if (mounted) { setDiscovery(next); setFreshUntil(Date.now() + 10000); setClock(Date.now()) }
      } catch { if (mounted) { setDiscovery({ available: false, models: [], capabilities: [] }); setFreshUntil(0) } }
      finally { pending = false }
    }
    void check()
    const poll = window.setInterval(() => { setClock(Date.now()); void check() }, 5000)
    return () => { mounted = false; clearInterval(poll) }
  }, [active, busy, refresh, csrf])
  const selected = discovery.models.find(m => m.id === model)
  const assembled = input + attachments.join('')
  const bytes = new TextEncoder().encode(assembled + instruction).length
  const maxTokens = Number(tokens), temp = Number(temperature)
  const valid = !!input.trim() && bytes <= 16384 && selected?.available && clock < freshUntil &&
    discovery.capabilities.includes(operation) && tokens.trim() !== '' && Number.isSafeInteger(maxTokens) &&
    maxTokens >= 1 && maxTokens <= selected.maxOutputTokens && bytes + 512 + maxTokens <= selected.contextTokens &&
    temperature.trim() !== '' && Number.isFinite(temp) && temp >= 0 && temp <= 2
  return <section className="panel editor" aria-label="Raw Intelligence editor">
    <h2>Raw Intelligence</h2><p>One inference request with your explicit instructions. Choose an approved local model.</p>
    <p role="status">{discovery.available ? 'Provider reachable. Model availability is checked on execution.' : 'Intelligence is unavailable. Your draft is preserved.'}</p>
    <button disabled={busy} onClick={() => setRefresh(v => v + 1)}>Check Intelligence</button>
    <div className="fields"><label>Model<select value={model} disabled={busy || uncertain} onChange={e => setModel(e.target.value)}>
      <option value="">Select model</option>{discovery.models.map(m => <option key={m.id} value={m.id} disabled={!m.available}>{m.id}</option>)}
    </select></label><label>Operation<select value={operation} disabled={busy || uncertain} onChange={e => setOperation(e.target.value)}>
      <option value="text.generate">Text</option><option value="reasoning.generate">Reasoning</option><option value="code.generate">Code</option>
    </select></label></div>
    <label>System instruction<textarea rows={3} value={instruction} disabled={busy || uncertain} onChange={e => setInstruction(e.target.value)} /></label>
    <label>Prompt<textarea rows={6} value={input} disabled={busy || uncertain} onChange={e => setInput(e.target.value)} /></label>
    <label>Attach text/code<input type="file" disabled={busy || uncertain || reading || attachments.length >= 4} accept=".txt,.md,.py,.js,.ts,.tsx,.jsx,.json,.csv,.cs,.cpp,.c,.h,.html,.css,.xml,.yaml,.yml,.sql,.sh" onChange={async e => {
      const file = e.target.files?.[0]; e.target.value = ''
      if (!file) return
      setReading(true)
      try {
        const text = await textAttachment(file)
        if (!alive.current) return
        if (new TextEncoder().encode(assembled + text + instruction).length > 16384) throw new Error('Prompt, instructions and attachments together exceed 16 KiB.')
        setAttachments(values => [...values, text]); setNotice('')
      } catch (error) { if (alive.current) setNotice(error instanceof Error ? error.message : 'Could not read attachment.') }
      finally { if (alive.current) setReading(false) }
    }} /></label>
    {attachments.length > 0 && <div><p>{attachments.length} text/code attachment(s) will be included in this inference prompt.</p><button disabled={busy || uncertain || reading} onClick={() => setAttachments([])}>Remove attachments</button></div>}
    <div className="fields"><label>Max output tokens<input inputMode="numeric" value={tokens} disabled={busy || uncertain} onChange={e => setTokens(e.target.value)} /></label>
      <label>Temperature<input inputMode="decimal" value={temperature} disabled={busy || uncertain} onChange={e => setTemperature(e.target.value)} /></label></div>
    <p>{bytes.toLocaleString()} / 16,384 input bytes. Selected text/code is sent only when you run inference. URLs and host file paths are not fetched.</p>
    {notice && <p role="alert">{notice}</p>}
    {uncertain && <p role="alert">This attempt remains unconfirmed. Start a new request explicitly before sending more work.</p>}
    <div className="row actions"><button className="primary" disabled={!valid || busy || uncertain || reading} onClick={async () => {
      if (!valid || busy || uncertain || reading) return
      const payload = { requestId: crypto.randomUUID(), modelId: model, capabilityId: operation,
        input: assembled, instruction, maxOutputTokens: maxTokens, temperature: temp }
      setBusy(true); setNotice(''); setAnswer(null)
      try {
        const result = await api.intelligenceExecute(payload, csrf)
        if (alive.current) setAnswer(result)
      } catch (error) {
        if (!alive.current) return
        setNotice(error instanceof Error ? error.message : 'Inference unavailable.')
        setUncertain(!(error instanceof IntelligenceFailure) || error.uncertain)
      } finally { if (alive.current) setBusy(false) }
    }}>{busy ? 'Running inference…' : 'Run inference'}</button>
      <button disabled={busy || reading} onClick={() => { setUncertain(false); setAnswer(null); setNotice(''); setInput(''); setInstruction(''); setAttachments([]) }}>Start new request</button></div>
    {answer && <div className="intelligence-result"><h3>Result</h3>
      {answer.finishReason === 'length' && <p role="status">Output reached the token limit and may be partial.</p>}
      {answer.text ? <pre>{answer.text}</pre> : <p>No final text was returned within the output budget.</p>}
      <small>{answer.modelId} · {(answer.elapsedMs / 1000).toFixed(1)} s{answer.usage ? ` · ${answer.usage.output_tokens} output tokens` : ''}</small>
    </div>}
  </section>
}
