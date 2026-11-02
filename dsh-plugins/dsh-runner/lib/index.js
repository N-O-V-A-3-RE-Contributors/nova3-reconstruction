/**
 * dsh-runner (host half): the one Host capability the external supervisor lacks.
 *
 * Why this exists
 * ---------------
 * `session.create` takes a *workspace id*, not a working directory, and the only
 * externally reachable way to launch a native session was a squad run, which is
 * pinned to the squad's registered workspace. That made native (Agents-tab
 * visible) execution incompatible with a per-task git checkout, which is exactly
 * what NOVA3's scope guard and verification require.
 *
 * This row closes the gap with the smallest surface that is still honest:
 *
 *   POST /dsh-runner/api/session
 *     {workspacePath, prompt, agentPreset?, permission?, provider?, model?,
 *      reasoningEffort?, title?}
 *     -> registers or reuses a Workspace for that path, creates a session in it,
 *        names it, applies permission and model, sends the prompt, and returns
 *        {sessionId, workspaceId, permissionApplied, modelApplied}.
 *
 *   GET /dsh-runner/api/session/{id}
 *     -> {sessionId, live} for the Host's own agent registry.
 *
 * It never decides whether work is correct, never writes a project's git, and
 * never exposes a credential. Routes are served to this machine's own browser
 * only, like the rest of the Host's local API. A missing optional service fails
 * closed with a named code instead of silently dropping a restriction.
 */
export const name = 'dsh-runner'
export const inject = ['typertGateway', 'webServer']

const MAX_BODY = 32 * 1024
const MAX_PROMPT = 32 * 1024
const MAX_TEXT = 400

function ownerPeer(signal) {
  // The typert gateway reads the peer's abort signal; an absent one crashed the
  // first live call with "Cannot read properties of undefined (reading
  // 'aborted')". A never-aborting signal is the correct default for these short
  // control calls.
  return {
    id: 'dsh-runner-owner:' + crypto.randomUUID(),
    principal: { kind: 'owner' },
    interactions: 'none',
    signal: signal || new AbortController().signal,
    dispose: async () => {},
  }
}

function refuse(res, status, code, error) {
  const body = JSON.stringify({ ok: false, code, error })
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store', 'content-length': Buffer.byteLength(body) })
  res.end(body)
}

function reply(res, payload) {
  const body = JSON.stringify({ ok: true, ...payload })
  res.writeHead(200, { 'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store', 'content-length': Buffer.byteLength(body) })
  res.end(body)
}

async function readJson(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_BODY) throw Object.assign(new Error('body too large'), { code: 'TOO_LARGE' })
    chunks.push(chunk)
  }
  if (!chunks.length) return {}
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  return value && typeof value === 'object' ? value : {}
}

/** Same-machine browser only, matching the Host's own local route policy. */
function sameMachine(req) {
  const origin = req.headers.origin
  if (!origin) return false
  try {
    return new URL(origin).host === String(req.headers.host || '')
  } catch {
    return false
  }
}

function text(value, field, limit = MAX_TEXT) {
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'string') throw Object.assign(new Error(`${field} must be a string`),
    { code: 'INVALID_REQUEST' })
  const trimmed = value.trim()
  if (!trimmed || trimmed.length > limit) throw Object.assign(
    new Error(`${field} is empty or too long`), { code: 'INVALID_REQUEST' })
  return trimmed
}

function atMost(value) {
  return value === undefined ? undefined : text(value, 'reasoningEffort', 40)
}

export function apply(ctx, config = {}) {
  const gateway = ctx.typertGateway
  if (!gateway) throw new Error('dsh-runner: this composition mounts no typert gateway')

  const call = async (namespace, method, request) => {
    try {
      return await gateway.invoke({
        namespace, method, args: { request }, peer: ownerPeer(ctx.signal),
      })
    } catch (error) {
      // Name the failing verb: a bare "runner_failed" hid a bad peer signal for
      // an entire debugging cycle.
      throw Object.assign(
        new Error(`${namespace}.${method}: ${error?.message || error}`),
        { code: 'GATEWAY_ERROR' })
    }
  }

  const permission = async (sessionId, level) => {
    const commands = ctx.get?.('commands')
    const agents = ctx.get?.('agents')
    const agent = agents?.get?.(sessionId)
    if (!commands || !agent) {
      throw Object.assign(new Error('permission services are unavailable in this composition'),
        { code: 'PERMISSION_UNAVAILABLE' })
    }
    const result = (await commands.execute(agent, '/permission ' + level, [],
      AbortSignal.timeout(30000)))?.result
    if (!result || result.kind !== 'success') {
      throw Object.assign(new Error(result?.text || 'permission was not acknowledged'),
        { code: 'PERMISSION_REFUSED' })
    }
  }

  const createSession = async (req, res) => {
    if (!sameMachine(req)) return refuse(res, 403, 'forbidden', 'local browser only')
    let body
    try {
      body = await readJson(req)
    } catch {
      return refuse(res, 400, 'invalid_json', 'the body is not JSON')
    }
    try {
      const workspacePath = text(body.workspacePath, 'workspacePath', 4096)
      const prompt = text(body.prompt, 'prompt', MAX_PROMPT)
      if (!workspacePath || !workspacePath.startsWith('/')) {
        return refuse(res, 400, 'invalid_request', 'workspacePath must be absolute')
      }
      const created = await call('workspace', 'create', { path: workspacePath })
      const workspaceId = created?.workspace?.workspaceId
      if (typeof workspaceId !== 'string' || !workspaceId) {
        return refuse(res, 502, 'workspace_failed', 'the Host returned no workspace id')
      }
      const session = await call('session', 'create', {
        workspaceId,
        ...(text(body.agentPreset, 'agentPreset', 200) === undefined
          ? {} : { agentPreset: text(body.agentPreset, 'agentPreset', 200) }),
      })
      const sessionId = session?.sessionId
      if (typeof sessionId !== 'string' || !sessionId) {
        return refuse(res, 502, 'session_failed', 'the Host returned no session id')
      }
      const title = text(body.title, 'title', 200)
      if (title) await call('session', 'rename', { sessionId, title })
      let permissionApplied = false
      const level = text(body.permission, 'permission', 40)
      if (level) {
        if (!['read-only', 'workspace-write', 'danger-full-access'].includes(level)) {
          return refuse(res, 400, 'invalid_request', 'unknown permission level')
        }
        try {
          await permission(sessionId, level)
          permissionApplied = true
        } catch (error) {
          return refuse(res, 502, error.code || 'permission_refused', String(error.message))
        }
      }
      let modelApplied = false
      const model = text(body.model, 'model', 200)
      if (model) {
        const slash = model.indexOf('/')
        const provider = text(body.provider, 'provider', 100)
          || (slash > 0 ? model.slice(0, slash) : undefined)
        const id = slash > 0 ? model.slice(slash + 1) : model
        await call('session', 'selectModel', {
          sessionId,
          ...(provider === undefined ? {} : { provider }),
          model: id,
          ...(atMost(body.reasoningEffort) === undefined
            ? {} : { reasoningEffort: atMost(body.reasoningEffort) }),
        })
        modelApplied = true
      }
      await call('session', 'prompt', {
        sessionId,
        requestId: 'dsh-runner-' + crypto.randomUUID(),
        mode: 'queue',
        content: [{ type: 'text', text: prompt }],
      })
      return reply(res, { sessionId, workspaceId, permissionApplied, modelApplied })
    } catch (error) {
      return refuse(res, 502, error.code || 'runner_failed', String(error.message || error))
    }
  }

  const sessionStatus = async (req, res) => {
    if (!sameMachine(req)) return refuse(res, 403, 'forbidden', 'local browser only')
    const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
    const sessionId = url.searchParams.get('id') || ''
    if (!sessionId) return refuse(res, 400, 'invalid_request', 'the id query parameter is required')
    const agents = ctx.get?.('agents')
    const live = Boolean(agents?.get?.(sessionId))
    return reply(res, { sessionId, live })
  }

  const promptSession = async (req, res) => {
    if (!sameMachine(req)) return refuse(res, 403, 'forbidden', 'local browser only')
    let body
    try { body = await readJson(req) } catch {
      return refuse(res, 400, 'invalid_json', 'the body is not JSON')
    }
    try {
      const sessionId = text(body.sessionId, 'sessionId', 200)
      const prompt = text(body.text, 'text', MAX_PROMPT)
      if (!sessionId || !prompt) {
        return refuse(res, 400, 'invalid_request', 'sessionId and text are required')
      }
      await call('session', 'prompt', {
        sessionId,
        requestId: 'dsh-runner-' + crypto.randomUUID(),
        mode: 'queue',
        content: [{ type: 'text', text: prompt }],
      })
      return reply(res, { sessionId, prompted: true })
    } catch (error) {
      return refuse(res, 502, error.code || 'prompt_failed', String(error.message || error))
    }
  }

  ctx.effect(() => {
    // Exact routes only: this Host's webServer route kinds are proven for
    // `exact` by @nova3/dsh-control; a prefix route is what silently kept this
    // plugin from mounting (the route 404'd and nothing was logged).
    const disposers = [
      ctx.webServer.register({ kind: 'exact', path: '/dsh-runner/api/session',
        handler: (req, res) => {
          if (req.method !== 'POST') return refuse(res, 405, 'method_not_allowed', 'POST only')
          return createSession(req, res)
        } }),
      ctx.webServer.register({ kind: 'exact', path: '/dsh-runner/api/prompt',
        handler: (req, res) => {
          if (req.method !== 'POST') return refuse(res, 405, 'method_not_allowed', 'POST only')
          return promptSession(req, res)
        } }),
      ctx.webServer.register({ kind: 'exact', path: '/dsh-runner/api/session/status',
        handler: (req, res) => {
          if (req.method !== 'GET') return refuse(res, 405, 'method_not_allowed', 'GET only')
          return sessionStatus(req, res)
        } }),
    ]
    console.info('[dsh-runner] mounted /dsh-runner/api/session (POST), '
      + '/dsh-runner/api/prompt (POST), /dsh-runner/api/session/status (GET)')
    return () => { for (const dispose of disposers) dispose() }
  }, 'dsh-runner: session routes')
}
