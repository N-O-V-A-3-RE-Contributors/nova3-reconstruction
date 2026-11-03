import test from 'node:test'
import assert from 'node:assert/strict'

import { decodeEntities, decodeResultUrl, parseResults } from '../lib/index.js'

const PAGE = `<!DOCTYPE html><html><body>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">Example &amp; <b>One</b></a>
    </h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">A first <b>snippet</b> text.</a>
  </div>
</div>
<div class="result results_links web-result result--ad">
  <div class="links_main result__body">
    <h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Duplicate</a></h2>
  </div>
</div>
<div class="result results_links web-result">
  <div class="links_main result__body">
    <h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Second</a></h2>
    <a class="result__snippet" href="#">Second snippet &quot;quoted&quot;.</a>
  </div>
</div>
</body></html>`

test('parseResults extracts title, URL and snippet and dedupes', () => {
  const results = parseResults(PAGE)
  assert.equal(results.length, 2)
  assert.deepEqual(results[0], {
    url: 'https://example.com/a',
    title: 'Example & One',
    snippet: 'A first snippet text.',
  })
  assert.deepEqual(results[1], {
    url: 'https://example.com/b',
    title: 'Second',
    snippet: 'Second snippet "quoted".',
  })
})

test('parseResults on a challenge page yields nothing', () => {
  assert.deepEqual(parseResults('<html><body>anomaly</body></html>'), [])
})

test('decodeResultUrl accepts absolute and protocol-relative URLs', () => {
  assert.equal(decodeResultUrl('https://example.com/x'), 'https://example.com/x')
  assert.equal(
    decodeResultUrl('//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fq'),
    'https://example.com/q')
  assert.equal(decodeResultUrl('not a url'), '')
})

test('decodeEntities handles numeric and named entities', () => {
  assert.equal(decodeEntities('a &amp; b &#39;c&#39; &#x2F;d &lt;e&gt;'), "a & b 'c' /d <e>")
})
