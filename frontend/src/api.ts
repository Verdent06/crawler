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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* keep statusText */
    }
    throw new Error(detail || `Request failed (${res.status})`)
  }
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
