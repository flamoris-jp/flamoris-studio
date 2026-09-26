import { useEffect, useState } from 'react'
import { api, type Asset, type AssetDetail } from './api'

function Thumbnail({ item }: { item: Asset }) {
  const [failed, setFailed] = useState(false)
  if (failed || (item.sizeBytes !== null && item.sizeBytes > 64 * 1024 * 1024)) return <span>Preview unavailable</span>
  return <img src={item.thumbnailUrl} alt="" loading="lazy" onError={() => setFailed(true)} />
}

export default function Gallery({ csrf }: { csrf: string }) {
  const [items, setItems] = useState<Asset[]>([])
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [detail, setDetail] = useState<AssetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [failures, setFailures] = useState<string[]>([])

  useEffect(() => {
    api.assets().then(page => { setItems(page.items); setNextOffset(page.nextOffset) })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load generated results.'))
      .finally(() => setLoading(false))
  }, [])
  const toggle = (id: string) => setSelected(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id])
  async function remove(ids: string[]) {
    if (!ids.length || !window.confirm(`Delete ${ids.length} generated result${ids.length === 1 ? '' : 's'} from Generation MCP storage and this Studio gallery? Provider originals may remain.`)) return
    setWorking(true); setError(''); setFailures([])
    const removed: string[] = [], failed: string[] = []
    try {
      for (let start = 0; start < ids.length; start += 32) {
        const batch = ids.slice(start, start + 32)
        try {
          const response = await api.deleteAssets(batch, csrf)
          const outcomes = new Map(response.results.map(item => [item.id, item.deleted]))
          for (const id of batch) (outcomes.get(id) === true ? removed : failed).push(id)
        } catch { failed.push(...batch) }
      }
      setItems(old => old.filter(x => !removed.includes(x.id)))
      setNextOffset(old => old === null ? null : Math.max(0, old - removed.length))
      setSelected(failed)
      if (detail && removed.includes(detail.id)) setDetail(null)
      if (failed.length) { setFailures(failed); setError(`Could not delete ${failed.length} result(s). They are still available here; try again.`) }
    } finally { setWorking(false) }
  }
  return <section className="panel results"><div className="row"><div><span className="eyebrow">YOUR OUTPUTS</span><h2>Generated results</h2></div>
    <button disabled={working || !selected.length} onClick={() => remove(selected)}>Delete selected ({selected.length})</button></div>
    <p>Generated files saved to this gallery are private to your account. Deletion removes the Generation MCP managed copy; provider originals may remain.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {loading ? <p>Loading results…</p> : !items.length ? <p>No generated results yet.</p> : <div className="assets gallery">{items.map(item =>
      <article key={item.id}><div className="gallery-preview"><Thumbnail item={item} /></div>
        <div><label><input type="checkbox" checked={selected.includes(item.id)} disabled={working} onChange={() => toggle(item.id)} /> Select</label>
          <strong>{item.displayName}</strong><small>{new Date(item.createdAt).toLocaleString()} · {item.mediaKind} · {item.sizeBytes === null ? 'Size unknown' : `${(item.sizeBytes / 1024 / 1024).toFixed(1)} MB`}</small>
          <button onClick={() => { setError(''); api.asset(item.id).then(setDetail).catch(() => setError('Could not load result details.')) }}>View details</button>
          {failures.includes(item.id) && <small>Deletion failed</small>}</div></article>)}</div>}
    {nextOffset !== null && <button disabled={working} onClick={async () => {
      try { const page = await api.assets(nextOffset); setItems(old => [...old, ...page.items]); setNextOffset(page.nextOffset) }
      catch { setError('Could not load more results.') }
    }}>Load more</button>}
    {detail && <div className="panel gallery-detail"><div className="row"><h3>{detail.displayName}</h3><button onClick={() => setDetail(null)}>Close</button></div>
      {detail.mediaKind === 'image' && <img src={detail.previewUrl} alt={detail.displayName} />}
      <p>{new Date(detail.createdAt).toLocaleString()} · {detail.mimeType} · {detail.sizeBytes === null ? 'Size unknown' : `${(detail.sizeBytes / 1024 / 1024).toFixed(1)} MB`}{detail.width && detail.height ? ` · ${detail.width} × ${detail.height}` : ''}</p>
      <dl>{Object.entries(detail.settings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>
      <div className="row"><a href={detail.downloadUrl}>Download ↓</a><button disabled={working} onClick={() => remove([detail.id])}>Delete this result</button></div>
    </div>}
  </section>
}
