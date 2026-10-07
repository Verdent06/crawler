import type { SiteDetail } from '../api'

type Props = {
  site: SiteDetail | null
  loading: boolean
}

function statusClass(status: string): string {
  if (status === 'completed') return 'ok'
  if (status === 'failed') return 'bad'
  if (status === 'running') return 'warn'
  return ''
}

export function JobStatus({ site, loading }: Props) {
  if (!site && !loading) {
    return (
      <section className="panel">
        <div className="empty">
          <strong>No scrape yet</strong>
          Paste a public institution homepage to find finance links and contacts.
        </div>
      </section>
    )
  }

  if (!site && loading) {
    return (
      <section className="panel">
        <div className="empty">
          <strong>Starting crawl…</strong>
          Waiting for the API to accept the job.
        </div>
      </section>
    )
  }

  if (!site) return null

  return (
    <section className="panel" aria-live="polite">
      <h2>Job status</h2>
      <div className="status-row">
        <span className={`pill ${statusClass(site.status)}`}>{site.status}</span>
        <span className="status-meta">
          Domain <strong>{site.domain}</strong>
        </span>
        <span className="status-meta">
          Pages fetched <strong>{site.pages_fetched}</strong>
        </span>
        <span className="status-meta">
          Links <strong>{site.links.length}</strong> · Contacts{' '}
          <strong>{site.contacts.length}</strong> · Documents{' '}
          <strong>{site.documents.length}</strong>
        </span>
      </div>
      {site.status === 'running' ? (
        <p className="status-meta" style={{ marginTop: '0.85rem' }}>
          Crawling… results update as pages are scored.
        </p>
      ) : null}
      {site.error ? <p className="status-error">{site.error}</p> : null}
    </section>
  )
}
