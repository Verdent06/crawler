import { useMemo, useState } from 'react'
import type { SiteDetail } from '../api'

type TabId = 'links' | 'contacts' | 'documents'

type Props = {
  site: SiteDetail | null
}

function keywordsText(value: SiteDetail['links'][number]['matched_keywords']): string {
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'string') return value
  return ''
}

export function ResultsTabs({ site }: Props) {
  const [tab, setTab] = useState<TabId>('links')
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')

  const links = useMemo(() => {
    if (!site) return []
    const q = query.trim().toLowerCase()
    return [...site.links]
      .filter((row) => (typeFilter === 'all' ? true : row.link_type === typeFilter))
      .filter((row) => {
        if (!q) return true
        const blob = [
          row.url,
          row.anchor_text ?? '',
          row.reason ?? '',
          keywordsText(row.matched_keywords),
          row.link_type,
        ]
          .join(' ')
          .toLowerCase()
        return blob.includes(q)
      })
      .sort((a, b) => b.result_score - a.result_score)
  }, [site, query, typeFilter])

  if (!site) return null

  const emptyCompleted =
    site.status === 'completed' &&
    site.links.length === 0 &&
    site.contacts.length === 0 &&
    site.documents.length === 0

  if (site.status === 'failed') {
    return (
      <section className="panel">
        <div className="empty">
          <strong>Crawl failed</strong>
          {site.error || 'The job ended without usable results.'}
        </div>
      </section>
    )
  }

  if (emptyCompleted) {
    return (
      <section className="panel">
        <div className="empty">
          <strong>No ranked results</strong>
          The crawl finished, but nothing cleared the score threshold. Try a deeper
          max pages/depth or a more finance-focused seed page.
        </div>
      </section>
    )
  }

  if (site.status === 'running' && site.links.length === 0) {
    return (
      <section className="panel">
        <div className="empty">
          <strong>Crawling…</strong>
          Ranked links will appear here as the frontier expands.
        </div>
      </section>
    )
  }

  return (
    <section className="panel" aria-labelledby="results-heading">
      <h2 id="results-heading">Results</h2>
      <div className="tabs" role="tablist" aria-label="Result categories">
        <button
          type="button"
          className="tab"
          role="tab"
          aria-selected={tab === 'links'}
          onClick={() => setTab('links')}
        >
          Links ({site.links.length})
        </button>
        <button
          type="button"
          className="tab"
          role="tab"
          aria-selected={tab === 'contacts'}
          onClick={() => setTab('contacts')}
        >
          Contacts ({site.contacts.length})
        </button>
        <button
          type="button"
          className="tab"
          role="tab"
          aria-selected={tab === 'documents'}
          onClick={() => setTab('documents')}
        >
          Documents ({site.documents.length})
        </button>
      </div>

      {tab === 'links' ? (
        <>
          <div className="filters">
            <input
              type="search"
              placeholder="Filter by URL, anchor, reason…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter links"
            />
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              aria-label="Filter by link type"
            >
              <option value="all">All types</option>
              <option value="document">document</option>
              <option value="contact">contact</option>
              <option value="navigation">navigation</option>
            </select>
          </div>
          {links.length === 0 ? (
            <div className="empty">No links match this filter.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Score</th>
                    <th>Type</th>
                    <th>Anchor</th>
                    <th>URL</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {links.map((row) => (
                    <tr key={row.id}>
                      <td className="score">{row.result_score.toFixed(0)}</td>
                      <td>
                        <span className="type-chip">{row.link_type}</span>
                      </td>
                      <td>{row.anchor_text || '—'}</td>
                      <td className="url-cell">
                        <a href={row.url} target="_blank" rel="noreferrer">
                          {row.url}
                        </a>
                      </td>
                      <td className="reason">{row.reason || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}

      {tab === 'contacts' ? (
        site.contacts.length === 0 ? (
          <div className="empty">No contacts extracted yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Title</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {site.contacts.map((row) => (
                  <tr key={row.id}>
                    <td>{row.name || '—'}</td>
                    <td>{row.title || '—'}</td>
                    <td>
                      {row.email ? (
                        <a href={`mailto:${row.email}`}>{row.email}</a>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>{row.phone || '—'}</td>
                    <td className="url-cell">
                      <a href={row.source_url} target="_blank" rel="noreferrer">
                        {row.source_url}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}

      {tab === 'documents' ? (
        site.documents.length === 0 ? (
          <div className="empty">No documents checked yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Verdict</th>
                  <th>Type</th>
                  <th>Year</th>
                  <th>URL</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {site.documents.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <span className={`verdict ${row.verdict}`}>{row.verdict}</span>
                    </td>
                    <td>{row.claimed_type || '—'}</td>
                    <td>{row.fiscal_year || '—'}</td>
                    <td className="url-cell">
                      <a href={row.url} target="_blank" rel="noreferrer">
                        {row.title || row.url}
                      </a>
                    </td>
                    <td className="reason">{row.evidence || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </section>
  )
}
