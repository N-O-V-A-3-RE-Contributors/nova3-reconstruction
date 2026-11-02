import test from 'node:test'
import assert from 'node:assert/strict'
import { apply } from '../lib/index.js'

function harness({ agents = new Map(), commands } = {}) {
  const calls = []
  const routes = new Map()
  const ctx = {
    typertGateway: {
      invoke: async (call) => {
        calls.push(call)
        if (call.method === 'create') return { sessionId: 's-1' }
        return {}
      },
    },
    webServer: { register: (route) => { routes.set(route.path, route.handler); return () => {} } },
    get: (name) => (name === 'agents' ? { get: (id) => agents.get(id) } : name === 'commands' ? commands : undefined),
    effect: (fn) => fn(),
  }
  apply(ctx, {})
  return { calls, routes }
}

function request(body, { origin = 'http://127.0.0.1:3080', host = '127.0.0.1:3080', method = 'POST' } = {}) {
  const payload = Buffer.from(JSON.stringify(body))
  return {
    method, headers: { origin, host }, url: '/',
    async *[Symbol.asyncIterator]() { yield payload },
  }
}

function response() {
  return {
    status: 0, body: null,
    writeHead(code) { this.status = code },
    end(text) { this.body = JSON.parse(text) },
  }
}

const LOCAL = { origin: 'http://127.0.0.1:3080', host: '127.0.0.1:3080' }

test('creates a workspace for the path, then a session in it, then prompts', async () => {
  const { calls, routes } = harness({
    commands: { execute: async () => ({ result: { kind: 'success' } }) },
    agents: new Map([['s-1', {}]]),
  })
  const res = response()
  await routes.get('/dsh-runner/api/session')(request({
    workspacePath: '/tmp/checkout-1', prompt: 'do work', permission: 'workspace-write',
    model: 'openrouter/demo', reasoningEffort: 'high', title: 'task 1',
  }), res)
  assert.equal(res.status, 200)
  assert.equal(res.body.sessionId, 's-1')
  assert.equal(res.body.cwd, '/tmp/checkout-1')
  assert.equal(res.body.permissionApplied, true)
  assert.equal(res.body.modelApplied, true)
  assert.deepEqual(calls.map((c) => `${c.namespace}.${c.method}`),
    ['session.create', 'session.rename', 'session.selectModel', 'session.prompt'])
  assert.deepEqual(calls[0].args.request, { cwd: '/tmp/checkout-1' })
  assert.deepEqual(calls[2].args.request,
    { sessionId: 's-1', provider: 'openrouter', model: 'demo', reasoningEffort: 'high' })
  assert.equal(calls[3].args.request.content[0].text, 'do work')
})

test('refuses a non-local origin and a relative path', async () => {
  const { routes } = harness()
  const forbidden = response()
  await routes.get('/dsh-runner/api/session')(
    request({ workspacePath: '/tmp/x', prompt: 'x' }, { origin: 'https://evil.example' }),
    forbidden)
  assert.equal(forbidden.status, 403)
  const relative = response()
  await routes.get('/dsh-runner/api/session')(request({ workspacePath: 'tmp/x', prompt: 'x' }), relative)
  assert.equal(relative.status, 400)
})

test('fails closed when permission services are absent', async () => {
  const { routes } = harness()
  const res = response()
  await routes.get('/dsh-runner/api/session')(request({
    workspacePath: '/tmp/x', prompt: 'x', permission: 'workspace-write' }), res)
  assert.equal(res.status, 502)
  assert.equal(res.body.code, 'PERMISSION_UNAVAILABLE')
})

test('prompts an existing session (this is how a restarted chat resumes)', async () => {
  const { calls, routes } = harness()
  const res = response()
  await routes.get('/dsh-runner/api/prompt')(request({
    sessionId: 'session-chat', text: 'continue' }), res)
  assert.equal(res.status, 200)
  assert.equal(res.body.prompted, true)
  const call = calls.at(-1)
  assert.equal(call.namespace + '.' + call.method, 'session.prompt')
  assert.equal(call.args.request.sessionId, 'session-chat')
  assert.equal(call.args.request.content[0].text, 'continue')
  assert.equal(call.args.request.mode, 'queue')
})

test('status reports whether the Host still holds the session', async () => {
  const { routes } = harness({ agents: new Map([['s-1', {}]]) })
  const live = response()
  await routes.get('/dsh-runner/api/session/status')({
    method: 'GET', headers: LOCAL, url: '/dsh-runner/api/session/status?id=s-1',
  }, live)
  assert.equal(live.status, 200)
  assert.equal(live.body.sessionId, 's-1')
  assert.equal(live.body.live, true)
})
