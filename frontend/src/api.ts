export type Health = {
  status: string
  version: string
  llm_enabled: boolean
  llm_available: boolean
  db_path: string
}

export type ScrapeResponse = {
  site_id: number
  status: string
  domain: string
}

export type LinkRow = {
  id: number
  url: string
  source_page: string | null
  anchor_text: string | null
  link_type: string
  follow_score: number
  result_score: number
  matched_keywords: string[] | string | null
  reason: string | null
}

export type ContactRow = {
  id: number
  source_url: string
  name: string | null
  title: string | null
  email: string | null
  phone: string | null
}

export type DocumentRow = {
  id: number
  url: string
  claimed_type: string | null
  fiscal_year: string | null
  title: string | null
  verdict: string
  evidence: string | null
}

export type SiteDetail = {
  id: number
  seed_url: string
  domain: string
  status: string
  error: string | null
  pages_fetched: number
  links: LinkRow[]
  contacts: ContactRow[]
  documents: DocumentRow[]
}

export type ScrapeRequest = {
  url: string
  max_pages?: number
  max_depth?: number
  max_documents?: number
}

const STATUS_MESSAGES: Record<number, string> = {
  401: 'The API rejected the request: missing or invalid API key. For local dev, start the Vite proxy with SCRAPER_API_KEY set. The browser never sends the key, so leave SCRAPER_API_KEY unset on the public demo.',
  429: 'Too many scrape requests. Wait a moment and try again.',
  503: 'The server is busy with other crawls. Try again shortly.',
}

function retryHint(res: Response): string {
  const seconds = Number(res.headers.get('Retry-After'))
  return Number.isFinite(seconds) && seconds > 0 ? ` (retry in ${seconds}s)` : ''
}

async function errorMessage(res: Response): Promise<string> {
  const known = STATUS_MESSAGES[res.status]
  if (known) return known + retryHint(res)
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail) return body.detail
  } catch {
    /* keep statusText */
  }
  return res.statusText || `Request failed (${res.status})`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!res.ok) throw new Error(await errorMessage(res))
  return res.json() as Promise<T>
}

export function getHealth(): Promise<Health> {
  return request<Health>('/health')
}

export function startScrape(body: ScrapeRequest): Promise<ScrapeResponse> {
  return request<ScrapeResponse>('/scrape', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getSite(siteId: number): Promise<SiteDetail> {
  return request<SiteDetail>(`/sites/${siteId}`)
}
