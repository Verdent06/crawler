import { useEffect, useRef, useState } from 'react'
import { getHealth, getSite, startScrape, type Health, type SiteDetail } from './api'
import { JobStatus } from './components/JobStatus'
import { ResultsTabs } from './components/ResultsTabs'
import { ScrapeForm, type ScrapeFormValues } from './components/ScrapeForm'
import './styles.css'

const POLL_MS = 2000

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [site, setSite] = useState<SiteDetail | null>(null)
  const [siteId, setSiteId] = useState<number | null>(null)
  const [starting, setStarting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    async function loadHealth() {
      try {
        const data = await getHealth()
        if (!cancelled) {
          setHealth(data)
          setHealthError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setHealth(null)
          setHealthError(err instanceof Error ? err.message : 'API unreachable')
        }
      }
    }
    void loadHealth()
    const id = window.setInterval(loadHealth, 15000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  useEffect(() => {
    if (siteId == null) return

    let cancelled = false

    async function poll() {
      try {
        const detail = await getSite(siteId!)
        if (cancelled) return
        setSite(detail)
        if (detail.status !== 'running' && pollRef.current != null) {
          window.clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch (err) {
        if (cancelled) return
        setFormError(err instanceof Error ? err.message : 'Failed to load site')
        if (pollRef.current != null) {
          window.clearInterval(pollRef.current)
          pollRef.current = null
        }
      }
    }

    void poll()
    pollRef.current = window.setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [siteId])

  const running = starting || site?.status === 'running'

  async function handleScrape(values: ScrapeFormValues) {
    setFormError(null)
    setStarting(true)
    setSite(null)
    try {
      const res = await startScrape({
        url: values.url,
        max_pages: values.max_pages,
        max_depth: values.max_depth,
      })
      setSiteId(res.site_id)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not start scrape')
      setSiteId(null)
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Link Scraper</h1>
          <p>
            Find high-value finance contacts and budget/ACFR documents on public
            institution sites.
          </p>
        </div>
        <div className="health" aria-label="API health">
          {healthError ? (
            <span className="pill bad">API down</span>
          ) : health ? (
            <>
              <span className="pill ok">API up</span>
              <span className={`pill ${health.llm_available ? 'ok' : 'warn'}`}>
                {health.llm_available
                  ? 'Model on'
                  : health.llm_enabled
                    ? 'Model offline'
                    : 'Keywords only'}
              </span>
            </>
          ) : (
            <span className="pill">Checking API…</span>
          )}
        </div>
      </header>

      <ScrapeForm disabled={running} error={formError} onSubmit={handleScrape} />
      <JobStatus site={site} loading={starting} />
      <ResultsTabs site={site} />
    </div>
  )
}
