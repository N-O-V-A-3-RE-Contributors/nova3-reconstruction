/**
 * Keyless DuckDuckGo web-search provider for DSH.
 *
 * The profile's default provider (`deepseek-official`) needs a DeepSeek API key
 * the owner does not have on this OpenRouter-routed machine, so `web_search`
 * failed with `WEB_PROVIDER_UNAVAILABLE`. This provider uses DuckDuckGo's public
 * HTML endpoint, which needs no API key and no account.
 *
 * Two requests are required by DuckDuckGo: the first establishes the anti-bot
 * cookie, the second performs the search with that cookie and a real referer.
 * The result markup is stable (`a.result__a` + `a.result__snippet`); parsing is
 * a pure function so it is unit-tested without the network.
 *
 * Configuration: none. Enable with `$DSH_WEB_SEARCH_PROVIDER=duckduckgo` or the
 * web runtime's `searchProvider: duckduckgo`.
 */
import { name as pluginName } from './meta.js'

export const name = pluginName
export const inject = ['web']

const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
const HOME = 'https://duckduckgo.com/'
const SEARCH = 'https://html.duckduckgo.com/html/'

/** Accumulate Set-Cookie headers into a Cookie request header. */
class CookieJar {
  constructor() { this.cookies = new Map() }

  absorb(headers) {
    const values = typeof headers.getSetCookie === 'function'
      ? headers.getSetCookie()
      : (headers.get('set-cookie') ? [headers.get('set-cookie')] : [])
    for (const line of values) {
      const pair = String(line).split(';', 1)[0].trim()
      const at = pair.indexOf('=')
      if (at > 0) this.cookies.set(pair.slice(0, at), pair.slice(at + 1))
    }
  }

  header() {
    return [...this.cookies].map(([key, value]) => `${key}=${value}`).join('; ')
  }
}

/** Decode the HTML entities DuckDuckGo writes into titles and snippets. */
export function decodeEntities(text) {
  return String(text || '')
    .replace(/<[^>]*>/g, '')
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
    .replace(/&quot;/g, '"')
    .replace(/&apos;|&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Turn a `//duckduckgo.com/l/?uddg=...` redirect into the real result URL. */
export function decodeResultUrl(href) {
  const text = String(href || '').replace(/&amp;/g, '&')
  try {
    const url = new URL(text.startsWith('//') ? `https:${text}` : text)
    const target = url.searchParams.get('uddg')
    return target ? decodeURIComponent(target) : url.href
  } catch {
    return ''
  }
}

/**
 * Parse a DuckDuckGo HTML results page.
 * @param {string} html - the response body.
 * @returns {Array<{url: string, title?: string, snippet?: string}>}
 */
export function parseResults(html) {
  const source = String(html || '')
  const pattern = /<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>([\s\S]*?)(?=<a[^>]*class="[^"]*result__a[^"]*"|$)/gi
  const results = []
  const seen = new Set()
  let match
  while ((match = pattern.exec(source)) !== null) {
    const url = decodeResultUrl(match[1])
    if (!url || seen.has(url)) continue
    seen.add(url)
    const title = decodeEntities(match[2])
    const snippetMatch = /class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/a>/i.exec(match[3])
    const snippet = snippetMatch ? decodeEntities(snippetMatch[1]) : undefined
    results.push(snippet ? { url, title, snippet } : { url, title })
  }
  return results
}

async function requestSearch(query) {
  const jar = new CookieJar()
  const common = {
    'user-agent': UA,
    'accept-language': 'en-US,en;q=0.9',
    accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  }
  const warm = await fetch(`${HOME}?q=${encodeURIComponent(query)}&ia=web`, {
    headers: common, redirect: 'follow',
  })
  jar.absorb(warm.headers)
  if (warm.body) await warm.text()

  const search = await fetch(`${SEARCH}?q=${encodeURIComponent(query)}`, {
    headers: { ...common, cookie: jar.header(), referer: HOME },
    redirect: 'follow',
  })
  const html = await search.text()
  if (!search.ok || html.length < 1000) {
    throw Object.assign(new Error(`DuckDuckGo returned HTTP ${search.status}`), {
      code: 'WEB_PROVIDER_ERROR',
    })
  }
  return html
}

class DuckDuckGoProvider {
  id = 'duckduckgo'

  available() { return true }

  async search(request, signal) {
    if (signal?.aborted) throw new Error('search aborted')
    const html = await requestSearch(request.query)
    const results = parseResults(html)
    if (results.length === 0) {
      // A 202 "anomaly" page is a provider failure, not an empty web.
      throw Object.assign(new Error(
        'DuckDuckGo returned no parseable results (anti-bot page?)'), {
        code: 'WEB_PROVIDER_ERROR',
      })
    }
    return { sources: results, truncated: false }
  }
}

export function apply(ctx) {
  ctx.effect(() => ctx.web.registerSearchProvider(new DuckDuckGoProvider()),
    'nova3-websearch: duckduckgo provider')
}
