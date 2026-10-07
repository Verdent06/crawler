import { useState, type FormEvent } from 'react'

export type ScrapeFormValues = {
  url: string
  max_pages: number
  max_depth: number
}

type Props = {
  disabled?: boolean
  error?: string | null
  onSubmit: (values: ScrapeFormValues) => void
}

export function ScrapeForm({ disabled, error, onSubmit }: Props) {
  const [url, setUrl] = useState('https://www.a2gov.org/')
  const [maxPages, setMaxPages] = useState(12)
  const [maxDepth, setMaxDepth] = useState(2)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    onSubmit({
      url: url.trim(),
      max_pages: maxPages,
      max_depth: maxDepth,
    })
  }

  return (
    <section className="panel" aria-labelledby="scrape-heading">
      <h2 id="scrape-heading">Start a scrape</h2>
      <form className="form-grid" onSubmit={handleSubmit}>
        <div className="field field-url">
          <label htmlFor="seed-url">Homepage URL</label>
          <input
            id="seed-url"
            name="url"
            type="url"
            required
            placeholder="https://www.a2gov.org/"
            value={url}
            disabled={disabled}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="max-pages">Max pages</label>
          <input
            id="max-pages"
            name="max_pages"
            type="number"
            min={1}
            max={100}
            value={maxPages}
            disabled={disabled}
            onChange={(e) => setMaxPages(Number(e.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="max-depth">Max depth</label>
          <input
            id="max-depth"
            name="max_depth"
            type="number"
            min={0}
            max={6}
            value={maxDepth}
            disabled={disabled}
            onChange={(e) => setMaxDepth(Number(e.target.value))}
          />
        </div>
        <button className="btn" type="submit" disabled={disabled}>
          {disabled ? 'Scraping…' : 'Start scrape'}
        </button>
      </form>
      {error ? <p className="form-error">{error}</p> : null}
    </section>
  )
}
