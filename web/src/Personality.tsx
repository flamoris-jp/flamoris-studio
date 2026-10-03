import { useEffect, useRef, useState } from 'react'
import { api, HTTPFailure, type Personality } from './api'

type SavePayload = { sessionKey: string; requestId: string; expectedRevision: number; displayName: string; sections: { title: string; content: string }[] }

export default function PersonalityEditor({ csrf, sessionKey }: { csrf: string; sessionKey?: string }) {
  const [value, setValue] = useState<Personality | null>(null)
  const [baseline, setBaseline] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [shared, setShared] = useState(false)
  const [history, setHistory] = useState<Personality[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [pending, setPending] = useState<SavePayload | null>(null)
  const epoch = useRef(0)
  useEffect(() => { epoch.current++; setValue(null); setHistory([]); setNotice(''); setPending(null); setBusy(false); return () => { epoch.current++ } }, [csrf, sessionKey])
  const serialized = value ? JSON.stringify({ display_name: value.display_name, sections: value.sections }) : ''
  const dirty = !!value && baseline !== serialized
  const valid = !!value && !!value.display_name.trim() && value.sections.length >= 1 && value.sections.length <= 16 &&
    value.sections.every(s => s.title.trim() && s.content.trim()) && new Set(value.sections.map(s => s.title)).size === value.sections.length &&
    value.sections.reduce((n, s) => n + new TextEncoder().encode(s.content).length, 0) <= 32768
  async function load() {
    const version = epoch.current; setBusy(true)
    try {
      const next = await api.personality(csrf)
      if (version !== epoch.current) return
      if (next.sessionKey !== sessionKey) { setNotice('The session changed. Refresh before editing.'); return }
      setValue(next); setBaseline(JSON.stringify({ display_name: next.display_name, sections: next.sections })); setPending(null); setShared(false); setNotice('')
    } catch { if (version === epoch.current) setNotice('Personality is unavailable or this account has no read permission.') }
    finally { if (version === epoch.current) setBusy(false) }
  }
  async function save(payload: SavePayload) {
    const version = epoch.current; setBusy(true); setPending(payload)
    try {
      const result = await api.personalitySave(payload, csrf)
      if (version !== epoch.current) return
      // Duplicate reconciliation reports the original revision; it never overwrites a newer head.
      if (result.duplicate) { setNotice(`Update ${result.revision} was already saved. Reload to inspect the current revision.`); setPending(null); return }
      setValue(old => old ? { ...old, revision: result.revision } : old)
      setBaseline(JSON.stringify({ display_name: payload.displayName, sections: payload.sections })); setPending(null)
      setNotice(`Saved revision ${result.revision}. New conversations use it; existing conversations keep their snapshot.`)
    } catch (error) {
      if (version !== epoch.current) return
      if (error instanceof HTTPFailure && error.status === 409) { setPending(null); setNotice('Conflict: someone saved a newer revision. Your draft is preserved. Reload explicitly to reconcile.') }
      else if (error instanceof HTTPFailure && [401, 403, 422].includes(error.status)) { setPending(null); setNotice(error.message) }
      else setNotice('Save outcome unconfirmed. Your update identity and draft are frozen. Check or explicitly retry the same update.')
    } finally { if (version === epoch.current) setBusy(false) }
  }
  return <section className="personality-editor" aria-label="Agent personality settings">
    <h3>Agent personality</h3>
    <button disabled={!sessionKey || busy} onClick={() => { if (!dirty || window.confirm('Discard unsaved personality changes and load the current revision?')) void load() }}>Load current personality</button>
    {notice && <p role="alert">{notice}</p>}
    {value && <>
      <p>Revision {value.revision} · {dirty ? 'Unsaved changes' : 'Saved'} · {value.can_edit ? 'Editing permitted' : 'Read only'}</p>
      <p>This personality belongs to a shared Agent. Saving affects new conversations for everyone using this Agent. Model, tool permissions and credentials are managed separately.</p>
      <fieldset disabled={!value.can_edit || busy || !!pending}>
        <label>Display name<input maxLength={128} value={value.display_name} onChange={e => setValue({ ...value, display_name: e.target.value })} /></label>
        {value.sections.map((section, index) => <div className="personality-section" key={index}>
          <label>Section {index + 1} title<input maxLength={80} value={section.title} onChange={e => setValue({ ...value, sections: value.sections.map((s, i) => i === index ? { ...s, title: e.target.value } : s) })} /></label>
          <label>Section {index + 1} body<textarea rows={8} maxLength={32768} value={section.content} onChange={e => setValue({ ...value, sections: value.sections.map((s, i) => i === index ? { ...s, content: e.target.value } : s) })} /></label>
          <div className="row"><button disabled={index === 0} onClick={() => { const sections = [...value.sections]; [sections[index - 1], sections[index]] = [sections[index], sections[index - 1]]; setValue({ ...value, sections }) }}>Move up</button><button disabled={index === value.sections.length - 1} onClick={() => { const sections = [...value.sections]; [sections[index + 1], sections[index]] = [sections[index], sections[index + 1]]; setValue({ ...value, sections }) }}>Move down</button><button disabled={value.sections.length === 1} onClick={() => setValue({ ...value, sections: value.sections.filter((_, i) => i !== index) })}>Remove section</button></div>
        </div>)}
        <button disabled={value.sections.length >= 16} onClick={() => setValue({ ...value, sections: [...value.sections, { title: '', content: '' }] })}>Add section</button>
        <label><input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} />I understand this changes the shared Agent personality</label>
        <button className="primary" disabled={!dirty || !valid || !shared} onClick={() => void save({ sessionKey: value.sessionKey, requestId: crypto.randomUUID(), expectedRevision: value.revision, displayName: value.display_name, sections: JSON.parse(JSON.stringify(value.sections)) })}>Save personality</button>
      </fieldset>
      {pending && <button disabled={busy} onClick={() => void save(pending)}>Retry same saved update identity</button>}
      <button disabled={busy || !!pending} onClick={async () => { const version = epoch.current; setBusy(true); try { const result = await api.personalityHistory(csrf); if (version === epoch.current) { setHistory(result.versions); setBefore(result.beforeRevision) } } catch { if (version === epoch.current) setNotice('History unavailable.') } finally { if (version === epoch.current) setBusy(false) } }}>View revision history</button>
      {history.map(item => <article key={item.revision}><p>Revision {item.revision} · {item.updated_at} · {item.display_name}</p><button disabled={!value.can_edit || busy || !!pending} onClick={() => { if (!dirty || window.confirm('Replace your unsaved draft with this historical revision?')) { setValue({ ...value, display_name: item.display_name, sections: JSON.parse(JSON.stringify(item.sections)) }); setShared(false); setNotice('Historical revision copied into your draft. Saving creates a new revision.') } }}>Restore into draft</button></article>)}
      {before && <button disabled={busy || !!pending} onClick={async () => { const version = epoch.current; setBusy(true); try { const result = await api.personalityHistory(csrf, before); if (version === epoch.current) { setHistory(old => [...old, ...result.versions]); setBefore(result.beforeRevision) } } catch { if (version === epoch.current) setNotice('History unavailable.') } finally { if (version === epoch.current) setBusy(false) } }}>Older revisions</button>}
    </>}
  </section>
}
