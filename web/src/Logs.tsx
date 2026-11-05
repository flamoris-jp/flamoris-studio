import { useEffect, useRef, useState } from 'react'
import { api, type DiagnosticEvent, type DiagnosticQuery } from './api'

export default function Logs({ initialRequestId = '' }: { initialRequestId?: string }) {
  const [requestId, setRequestId] = useState(initialRequestId)
  const [level, setLevel] = useState('')
  const [component, setComponent] = useState('')
  const [scope, setScope] = useState('mine')
  const [canViewAll, setCanViewAll] = useState(false)
  const [query, setQuery] = useState<DiagnosticQuery>({ requestId: initialRequestId })
  const [items, setItems] = useState<DiagnosticEvent[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const epoch = useRef(0)
  async function load(filter: DiagnosticQuery, append = false) {
    const version = ++epoch.current
    setBusy(true); setError('')
    try {
      const page = await api.logs(filter)
      if (version !== epoch.current) return
      setItems(old => append ? [...old, ...page.items] : page.items)
      setNext(page.nextCursor); setCanViewAll(page.canViewAll)
    } catch (failure) {
      if (version === epoch.current) setError(failure instanceof Error ? failure.message : 'Logs are unavailable.')
    } finally { if (version === epoch.current) setBusy(false) }
  }
  useEffect(() => {
    setItems([]); setNext(null)
    void load(query)
    return () => { epoch.current += 1 }
  }, [query])
  return <section className="panel diagnostic-logs">
    <h2>Logs</h2>
    <p>Match an error reference to its request and service events. Times use your browser’s time zone. Retention: up to 7 days and the latest 5,000 events.</p>
    <form className="fields" onSubmit={event => {
      event.preventDefault()
      setQuery({ requestId, level, component, scope })
    }}>
      <label>Error reference<input value={requestId} onChange={event => setRequestId(event.target.value)} pattern="[a-f0-9]{32}" placeholder="All references" /></label>
      <label>Level<select value={level} onChange={event => setLevel(event.target.value)}><option value="">All</option><option>ERROR</option><option>WARNING</option></select></label>
      <label>Service<select value={component} onChange={event => setComponent(event.target.value)}><option value="">All</option>{['studio', 'generation', 'agent', 'intelligence', 'browser'].map(name => <option key={name}>{name}</option>)}</select></label>
      {canViewAll && <label>Accounts<select value={scope} onChange={event => setScope(event.target.value)}><option value="mine">My events</option><option value="all">All accounts</option></select></label>}
      <button disabled={busy}>Apply filters</button>
      <button type="button" onClick={() => { setRequestId(''); setLevel(''); setComponent(''); setScope('mine'); setQuery({}) }}>Reset filters</button>
    </form>
    <div className="row"><span>{items.length} events · {query.scope === 'all' ? 'All accounts' : 'My events'}</span><button disabled={busy} onClick={() => { setItems([]); setNext(null); void load(query) }}>Refresh</button></div>
    {error && <p role="alert" className="error">{error}</p>}
    {busy && <p role="status">Loading logs…</p>}
    {!busy && !error && !items.length && <p>No matching events. Only retained events from your account appear unless operator access is enabled.</p>}
    {items.map(item => <article className="diagnostic-event" key={item.id}>
      <div className="row"><strong>{item.level} · {item.component} · {item.operation}</strong><time dateTime={item.time}>{new Date(item.time).toLocaleString()}</time></div>
      <p>{item.method} {item.route} · {item.phase}{item.status !== undefined ? ` · HTTP ${item.status}` : ''}{item.elapsedMs !== undefined ? ` · ${item.elapsedMs} ms` : ''}{item.reason ? ` · ${item.reason}` : ''}</p>
      <div className="row"><code>{item.requestId}</code><button onClick={() => { setRequestId(item.requestId); setQuery({ requestId: item.requestId, scope }) }}>Related events</button></div>
      {!!item.exceptions?.length && <details><summary>Exception locations</summary>{item.exceptions.map((exception, index) => <div key={index}><strong>{exception.type}</strong><ul>{exception.frames.map((frame, i) => <li key={i}><code>{frame.file}:{frame.line} · {frame.function}</code></li>)}</ul></div>)}</details>}
    </article>)}
    {next !== null && <button disabled={busy} onClick={() => { void load({ ...query, before: next }, true) }}>Load older events</button>}
  </section>
}
