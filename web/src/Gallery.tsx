import { useEffect, useState } from 'react'
import { api, type Asset, type AssetDetail, type ImageSettings } from './api'

// Image load events do not expose HTTP status. Retry only twice, then let the
// user retry explicitly; never turn a permanent error into an endless loop.
function RetryingImage({ url, alt, lazy, unavailable }: {
  url: string; alt: string; lazy?: boolean; unavailable: string
}) {
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    if (!failed || attempt >= 2) return
    const timer = window.setTimeout(() => {
      setAttempt(old => old + 1)
      setFailed(false)
    }, 1000 * (attempt + 1))
    return () => window.clearTimeout(timer)
  }, [failed, attempt])
  if (failed && attempt >= 2) return <span>{unavailable} <button onClick={() => {
    setAttempt(0); setReload(old => old + 1); setFailed(false)
  }}>Reload preview</button></span>
  const src = attempt || reload
    ? `${url}${url.includes('?') ? '&' : '?'}previewRetry=${reload}-${attempt}` : url
  return <img key={src} src={src} alt={alt} loading={lazy ? 'lazy' : undefined}
    onLoad={() => setFailed(false)} onError={() => setFailed(true)} />
}

function Thumbnail({ item }: { item: Asset }) {
  if (renderer(item) !== 'image') return <span>{item.mediaKind === 'unknown' ? 'File' : item.mediaKind} · Download available</span>
  if (item.sizeBytes !== null && item.sizeBytes > 64 * 1024 * 1024) return <span>Preview unavailable</span>
  return <RetryingImage key={item.thumbnailUrl} url={item.thumbnailUrl} alt="" lazy unavailable="Preview unavailable" />
}

function renderer(item: Asset): 'image' | 'audio' | 'video' | 'file' {
  if (item.mediaKind === 'image' && ['image/png', 'image/jpeg', 'image/webp'].includes(item.mimeType)) return 'image'
  if (item.mediaKind === 'audio' && ['audio/wav', 'audio/mpeg'].includes(item.mimeType)) return 'audio'
  if (item.mediaKind === 'video' && item.mimeType === 'video/mp4') return 'video'
  return 'file'
}

export function ResultPreview({ item }: { item: Asset }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [item.id, item.previewUrl])
  const kind = renderer(item)
  if (kind === 'image') return <ImagePreview item={item} />
  if (kind === 'file') return <p>{item.mediaKind === 'midi' ? 'MIDI file; playback is unavailable.' : 'File preview is unavailable.'} Download is available below.</p>
  if (failed) return <p>Preview unavailable; download may still work. <button onClick={() => setFailed(false)}>Reload preview</button></p>
  return kind === 'audio'
    ? <audio controls preload="metadata" src={item.previewUrl} onError={() => setFailed(true)} aria-label={item.displayName} />
    : <video controls preload="metadata" src={item.previewUrl} onError={() => setFailed(true)} aria-label={item.displayName} />
}

export function ImagePreview({ item }: { item: Asset }) {
  if (item.sizeBytes !== null && item.sizeBytes > 64 * 1024 * 1024) return <span>Preview unavailable; download may still work.</span>
  const url = item.hasThumbnail ? item.thumbnailUrl : item.previewUrl
  return <RetryingImage key={url} url={url} alt={item.displayName} unavailable="Preview unavailable; download may still work." />
}

export default function Gallery({ csrf, onUseSettings }: { csrf: string; onUseSettings?: (settings: Partial<ImageSettings>) => void }) {
  const [items, setItems] = useState<Asset[]>([])
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [detail, setDetail] = useState<AssetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [failures, setFailures] = useState<string[]>([])
  const [canImport, setCanImport] = useState(false)
  const [externalJob, setExternalJob] = useState('')

  useEffect(() => {
    api.assets().then(page => { setItems(page.items); setNextOffset(page.nextOffset) })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load generated results.'))
      .finally(() => setLoading(false))
    api.externalImportAvailability().then(value => setCanImport(value.available === true))
      .catch(() => setCanImport(false))
  }, [])
  const toggle = (id: string) => setSelected(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id])
  async function remove(ids: string[]) {
    if (!ids.length || !window.confirm(`Delete ${ids.length} generated result${ids.length === 1 ? '' : 's'} and their stored copies? Provider originals may remain.`)) return
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
  return <section className="panel results"><div className="row"><div><span className="eyebrow">YOUR OUTPUTS</span><h2>Assets</h2></div>
    <button disabled={working || !selected.length} onClick={() => remove(selected)}>Delete selected ({selected.length})</button></div>
    <p>Generated files saved to this gallery are private to your account. Deletion removes the stored generated copy; provider originals may remain.</p>
    {canImport && <form className="row" onSubmit={async event => {
      event.preventDefault()
      if (working || !/^[a-f0-9]{32}$/.test(externalJob.trim())) return
      setWorking(true); setError('')
      try {
        await api.importExternal(externalJob.trim(), csrf)
        const page = await api.assets()
        setItems(page.items); setNextOffset(page.nextOffset); setExternalJob(''); setSelected([])
      } catch { setError('Could not import this completed generation. Verify the job and your linked external account, then retry explicitly.') }
      finally { setWorking(false) }
    }}><label>External generation job<input aria-label="External generation job" value={externalJob}
      maxLength={32} disabled={working} onChange={event => setExternalJob(event.target.value)} /></label>
      <button type="submit" disabled={working || !/^[a-f0-9]{32}$/.test(externalJob.trim())}>Import results</button>
      <small>Add completed generations from your linked external client.</small></form>}
    {error && <p role="alert" className="error">{error}</p>}
    {loading ? <p>Loading results…</p> : !items.length ? <p>No generated results yet.</p> : <div className="assets gallery">{items.map(item =>
      <article key={item.id}><div className="gallery-preview"><Thumbnail item={item} /></div>
        <div><label><input type="checkbox" checked={selected.includes(item.id)} disabled={working} onChange={() => toggle(item.id)} /> Select</label>
          <strong>{item.displayName}</strong><small>{new Date(item.createdAt).toLocaleString()} · {item.mediaKind}{item.origin === 'external' ? ' · External' : ''} · {item.sizeBytes === null ? 'Size unknown' : `${(item.sizeBytes / 1024 / 1024).toFixed(1)} MB`}</small>
          <button onClick={() => { setError(''); api.asset(item.id).then(setDetail).catch(() => setError('Could not load result details.')) }}>View details</button>
          {failures.includes(item.id) && <small>Deletion failed</small>}</div></article>)}</div>}
    {nextOffset !== null && <button disabled={working} onClick={async () => {
      try { const page = await api.assets(nextOffset); setItems(old => [...old, ...page.items]); setNextOffset(page.nextOffset) }
      catch { setError('Could not load more results.') }
    }}>Load more</button>}
    {detail && <div className="gallery-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setDetail(null) }}><div className="panel gallery-detail" role="dialog" aria-modal="true" aria-label={`Details for ${detail.displayName}`} onKeyDown={event => { if (event.key === 'Escape') setDetail(null) }}><div className="row"><h3>{detail.displayName}</h3><button autoFocus onClick={() => setDetail(null)}>Close</button></div>
      <ResultPreview key={detail.id} item={detail} />
      {detail.outputRole && <p>{detail.outputRole.role} · {detail.outputRole.port} · {detail.outputRole.index + 1}</p>}
      <p>{new Date(detail.createdAt).toLocaleString()} · {detail.mimeType} · {detail.sizeBytes === null ? 'Size unknown' : `${(detail.sizeBytes / 1024 / 1024).toFixed(1)} MB`}{detail.width && detail.height ? ` · ${detail.width} × ${detail.height}` : ''}</p>
      <dl>{Object.entries(detail.settings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{Array.isArray(value) ? value.map((lora, index) => `${index + 1}. ${lora.name} (model ${lora.strengthModel}, CLIP ${lora.strengthClip})`).join('\n') || 'None' : String(value)}</dd></div>)}</dl>
      <div className="row"><a href={detail.downloadUrl}>Download ↓</a>{onUseSettings && renderer(detail) === 'image' && Object.keys(detail.settings).length > 0 && <button onClick={() => onUseSettings(detail.settings)}>Use settings ↗</button>}<button disabled={working} onClick={() => remove([detail.id])}>Delete this result</button></div>
    </div></div>}
  </section>
}
