import { useEffect, useRef, useState } from 'react'
import { api, HTTPFailure, type Asset, type AssetDetail, type AssetKind, type ImageSettings } from './api'

const kinds: [AssetKind, string][] = [['all', 'All'], ['image', 'Image'], ['video', 'Video'], ['music', 'Music'], ['speech', 'Speech'], ['other', 'Other files']]

function attachmentLabel(item: Asset) {
  if (item.mimeType === 'text/vnd.abc') return 'ABC score'
  if (item.mimeType === 'audio/midi') return 'MIDI'
  if (item.mimeType === 'application/json') return 'Generation information'
  return item.displayName
}

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
  const [kind, setKind] = useState<AssetKind>('all')
  const [syncError, setSyncError] = useState('')
  const cursor = useRef(0)
  const revision = useRef(0)
  const detailRequest = useRef(0)

  useEffect(() => {
    let current = true
    revision.current += 1
    setLoading(true); setItems([]); setNextOffset(null); setSelected([]); setDetail(null); setFailures([]); setError('')
    api.assets(0, kind).then(page => { if (current) { setItems(page.items); setNextOffset(page.nextOffset) } })
      .catch(e => { if (current) setError(e instanceof Error ? e.message : 'Could not load generated results.') })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false; revision.current += 1; detailRequest.current += 1 }
  }, [kind])
  useEffect(() => {
    let current = true
    cursor.current = 0
    api.externalImportAvailability().then(value => { if (current) setCanImport(value.available === true) })
      .catch(() => { if (current) setCanImport(false) })
    return () => { current = false }
  }, [csrf])
  useEffect(() => {
    if (!canImport || working) return
    const controller = new AbortController()
    let timer: number | undefined
    async function sync() {
      let delay = 15000
      try {
        if (document.visibilityState === 'visible') {
          const response = await api.syncExternal(cursor.current, csrf, controller.signal)
          if (controller.signal.aborted) return
          cursor.current = response.nextCursor
          if (response.hasMore) delay = 1000
          setSyncError('')
          if (response.imported > 0) {
            const page = await api.assets(0, kind)
            if (controller.signal.aborted) return
            setItems(page.items); setNextOffset(page.nextOffset); setSelected([])
          }
        }
      } catch (e) {
        if (controller.signal.aborted) return
        setSyncError('Could not sync external results. Your saved Assets are still available.')
        if (e instanceof HTTPFailure && [401, 403, 404].includes(e.status)) return
        delay = 30000
      }
      if (!controller.signal.aborted) timer = window.setTimeout(sync, delay)
    }
    void sync()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [canImport, csrf, kind, working])
  const closeDetail = () => { detailRequest.current += 1; setDetail(null) }
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
    <nav className="row" aria-label="Asset types">{kinds.map(([value, label]) => <button key={value}
      aria-pressed={kind === value} disabled={working} onClick={() => setKind(value)}>{label}</button>)}</nav>
    {canImport && <><p>Results from your linked AI connection appear here automatically.</p>
      {syncError && <p role="status">{syncError}</p>}
      <details><summary>Import older results</summary><form className="row" onSubmit={async event => {
      event.preventDefault()
      if (working || !/^[a-f0-9]{32}$/.test(externalJob.trim())) return
      setWorking(true); setError('')
      try {
        await api.importExternal(externalJob.trim(), csrf)
        const page = await api.assets(0, kind)
        setItems(page.items); setNextOffset(page.nextOffset); setExternalJob(''); setSelected([])
      } catch { setError('Could not import this completed generation. Verify the job and your linked external account, then retry explicitly.') }
      finally { setWorking(false) }
    }}><label>External generation job<input aria-label="External generation job" value={externalJob}
      maxLength={32} disabled={working} onChange={event => setExternalJob(event.target.value)} /></label>
      <button type="submit" disabled={working || !/^[a-f0-9]{32}$/.test(externalJob.trim())}>Import results</button>
      <small>Add completed generations from your linked external client.</small></form></details></>}
    {error && <p role="alert" className="error">{error}</p>}
    {loading ? <p>Loading results…</p> : !items.length ? <p>No generated results yet.</p> : <div className="assets gallery">{items.map(item =>
      <article key={item.id}><div className="gallery-preview"><Thumbnail item={item} /></div>
        <div><label><input type="checkbox" checked={selected.includes(item.id)} disabled={working} onChange={() => toggle(item.id)} /> Select</label>
          <strong>{item.displayName}</strong><small>{new Date(item.createdAt).toLocaleString()} · {item.mediaKind}{item.origin === 'external' ? ' · External' : ''} · {item.sizeBytes === null ? 'Size unknown' : `${(item.sizeBytes / 1024 / 1024).toFixed(1)} MB`}</small>
          <button onClick={() => {
            setError('')
            const version = revision.current, request = ++detailRequest.current
            api.asset(item.id).then(value => { if (revision.current === version && detailRequest.current === request) setDetail(value) })
              .catch(() => { if (revision.current === version && detailRequest.current === request) setError('Could not load result details.') })
          }}>View details</button>
          {failures.includes(item.id) && <small>Deletion failed</small>}</div></article>)}</div>}
    {nextOffset !== null && <button disabled={working} onClick={async () => {
      const version = revision.current
      try { const page = await api.assets(nextOffset, kind); if (revision.current !== version) return; setItems(old => [...old, ...page.items.filter(item => !old.some(existing => existing.id === item.id))]); setNextOffset(page.nextOffset) }
      catch { if (revision.current === version) setError('Could not load more results.') }
    }}>Load more</button>}
    {detail && <div className="gallery-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) closeDetail() }}><div className="panel gallery-detail" role="dialog" aria-modal="true" aria-label={`Details for ${detail.displayName}`} onKeyDown={event => { if (event.key === 'Escape') closeDetail() }}><div className="row"><h3>{detail.displayName}</h3><button autoFocus onClick={closeDetail}>Close</button></div>
      <ResultPreview key={detail.id} item={detail} />
      {detail.outputRole && <p>{detail.outputRole.role} · {detail.outputRole.port} · {detail.outputRole.index + 1}</p>}
      <p>{new Date(detail.createdAt).toLocaleString()} · {detail.mimeType} · {detail.sizeBytes === null ? 'Size unknown' : `${(detail.sizeBytes / 1024 / 1024).toFixed(1)} MB`}{detail.width && detail.height ? ` · ${detail.width} × ${detail.height}` : ''}</p>
      <dl>{Object.entries(detail.settings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{Array.isArray(value) ? value.map((lora, index) => `${index + 1}. ${lora.name} (model ${lora.strengthModel}, CLIP ${lora.strengthClip})`).join('\n') || 'None' : String(value)}</dd></div>)}</dl>
      <div className="row"><a href={detail.downloadUrl}>Download ↓</a>{onUseSettings && renderer(detail) === 'image' && Object.keys(detail.settings).length > 0 && <button onClick={() => onUseSettings(detail.settings)}>Use settings ↗</button>}<button disabled={working} onClick={() => remove([detail.id])}>Delete this result</button></div>
      {!!detail.attachments?.length && <div><h4>Related files</h4><ul>{detail.attachments.map(item => <li key={item.id}>
        <a href={item.downloadUrl}>{attachmentLabel(item)} ↓</a> <small>{item.displayName}</small>
      </li>)}</ul></div>}
    </div></div>}
  </section>
}

