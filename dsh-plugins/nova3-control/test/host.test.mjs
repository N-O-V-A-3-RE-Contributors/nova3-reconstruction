import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import { apply } from '../lib/index.js'

const here = dirname(fileURLToPath(import.meta.url))
const repo = join(here, '..', '..', '..')

function harness() {
  const routes = new Map()
  const ctx = {
    effect(fn) { const dispose = fn(); return dispose },
    webServer: {
      register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path) },
    },
  }
  apply(ctx, { repo, github: 'N-O-V-A-3-RE-Contributors/nova3-reconstruction' })
  return routes
}

function response() {
  return {
    status: 0, headers: {}, body: '',
    writeHead(status, headers) { this.status = status; this.headers = headers },
    end(body) { this.body = String(body || '') },
  }
}

test('registers the three Mission routes', () => {
  const routes = harness()
  assert.deepEqual([...routes.keys()].sort(), [
    '/nova3/api/automation', '/nova3/api/mission', '/nova3/api/run',
  ])
})

test('GET /nova3/api/mission returns the documented payload', async () => {
  const routes = harness()
  const res = response()
  await routes.get('/nova3/api/mission')({ method: 'GET', headers: { host: 'localhost' } }, res)
  assert.equal(res.status, 200)
  const payload = JSON.parse(res.body)
  assert.equal(payload.schema, 'nova3-mission/1')
  assert.ok(payload.project.inventory.functions_recorded > 0)
  assert.ok(['on', 'off', 'paused_credits', 'unset'].includes(payload.autopilot.mode))
  assert.equal(typeof payload.source_reference.present, 'boolean')
})

test('mission route refuses a cross-site POST', async () => {
  const routes = harness()
  const res = response()
  await routes.get('/nova3/api/automation')(
    { method: 'POST', headers: { host: 'gui.example', origin: 'https://evil.example' } }, res)
  assert.equal(res.status, 403)
})

test('client bundle registers the Mission conversation view', () => {
  const source = readFileSync(join(here, '..', 'lib', 'client.js'), 'utf8')
  let registration = null
  const slots = {
    inject(name, produce) { assert.equal(name, 'conversation.view'); registration = produce() },
    register(options, component) {
      return { options, component }
    },
  }
  const window = {
    __ModuleLoader__: { load({ id, factory }) {
      assert.equal(id, '@nova3/dsh-control')
      const exports = factory((spec) => {
        if (spec === 'react') {
          return {
            createElement: () => null,
            useState: () => [null, () => {}],
            useEffect: () => {},
            useCallback: (fn) => fn,
            useMemo: (fn) => fn(),
          }
        }
        throw new Error('unexpected module ' + spec)
      })
      globalThis.__nova3Plugin = exports
      return exports
    } },
  }
  // The bundle is a browser script, not an ESM module: evaluate it with a fake loader.
  const run = new Function('window', 'document', source)
  run(window, undefined)
  const plugin = globalThis.__nova3Plugin
  assert.equal(typeof plugin.apply, 'function')
  assert.deepEqual(plugin.inject, ['slots'])
  plugin.apply({ slots })
  assert.ok(registration)
  assert.equal(registration.options.id, 'mission')
  assert.equal(registration.options.label(), 'Mission')
  assert.equal(typeof registration.component, 'function')
})
