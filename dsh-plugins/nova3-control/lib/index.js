/**
 * NOVA3 Mission control plane (host half).
 *
 * The old `/nova` slash command is replaced by the **Mission** tab in the Web
 * UI. This module serves the tab's JSON and the only two actions the tab has:
 * turning the persistent automation switch on/off and changing whether reviewed
 * green pull requests may be merged. Everything else is a read-only projection
 * produced by `python -m nova3_team.mission`.
 *
 * The plugin never reads credentials, never talks to GitHub itself and never
 * writes supervisor state; all mutations go through the audited
 * `nova3_team.autopilot` CLI, which re-verifies the owner's live GitHub role
 * before enabling.
 *
 * Configuration: `repo` (absolute checkout), `github` (owner/name), injected by
 * the profile patch.
 */
import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

export const name = 'nova3-control'
export const inject = ['webServer', 'commands']

const MISSION_TTL_MS = 15000
const MAX_BODY = 64 * 1024

function repoDir(config) {
  return (config && config.repo) ||
    process.env.NOVA3_PROJECT_ROOT ||
    '/home/jason/Projects/nova3_re'
}

function githubRepo(config) {
  return (config && config.github) ||
    'N-O-V-A-3-RE-Contributors/nova3-reconstruction'
}

function pythonBin(repo) {
  const venv = join(repo, '.venv', 'bin', 'python')
  return existsSync(venv) ? venv : 'python3'
}

function runPython(repo, args, timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    execFile(pythonBin(repo), args, {
      cwd: repo,
      timeout: timeoutMs,
      maxBuffer: 8 * 1024 * 1024,
      env: { ...process.env, PYTHONPATH: repo, PYTHONUNBUFFERED: '1' },
    }, (error, stdout, stderr) => {
      if (error) {
        reject(Object.assign(new Error((stderr || error.message || 'python failed').slice(-2000)),
          { code: error.killed ? 'TIMEOUT' : 'PYTHON_ERROR' }))
        return
      }
      resolve(String(stdout || ''))
    })
  })
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(body),
  })
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
  if (chunks.length === 0) return {}
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    return value && typeof value === 'object' ? value : {}
  } catch {
    throw Object.assign(new Error('invalid JSON body'), { code: 'BAD_JSON' })
  }
}

/** Reject cross-site POSTs; the GUI is served by this same host. */
function sameOrigin(req) {
  const origin = req.headers.origin
  if (!origin) return true
  try {
    return new URL(origin).host === String(req.headers.host || '')
  } catch {
    return false
  }
}


/**
 * `/nova` — manage the worker from chat.
 *
 * Registered here, not in a separate plugin: this row is proven to mount (its
 * routes answer). A separate command-only row composed but never registered, and
 * a command that never registers is simply typed into the chat as text.
 */
export function novaCommand({ repo, github, executor }) {
  const run = executor || ((args, timeoutMs) => new Promise((resolve) => {
    execFile(join(repo, '.venv/bin/python'), args,
      { cwd: repo, timeout: timeoutMs, maxBuffer: 1 << 20 },
      (error, stdout, stderr) => resolve({
        ok: !error, out: String(stdout || '').trim(), err: String(stderr || '').trim(),
      }))
  }))
  return async (invocation) => {
    const text = String(invocation?.rawInput || '').trim()
    const verb = text.split(/\s+/)[0]
    if (!verb || verb === 'status') {
      const r = await run(['-m', 'nova3_team.dsh_status'], 30000)
      return r.ok
        ? { kind: 'success', text: r.out.slice(0, 1800) }
        : { kind: 'error', text: `status failed: ${(r.err || r.out).slice(0, 600)}` }
    }
    if (verb === 'on' || verb === 'off') {
      const r = await run(['-m', 'nova3_team.agent_control', verb, '--repo', github], 60000)
      return { kind: r.ok ? 'success' : 'error',
               text: `autopilot ${verb}: ${(r.out || r.err).slice(0, 600)}` }
    }
    if (verb === 'run') {
      const r = await run(['-m', 'nova3_team.autopilot', 'run', '--repo', github, '--json'], 180000)
      if (!r.ok) return { kind: 'error', text: `run failed: ${r.err.slice(0, 500)}` }
      try {
        const report = JSON.parse(r.out)
        const merged = (report.merged || []).map((m) => `#${m.pr}`).join(', ') || 'none'
        return { kind: 'success', text: `one pass: state=${report.state} approved=${(report.approved || []).length} merged=${merged}` }
      } catch {
        return { kind: 'success', text: r.out.slice(0, 800) }
      }
    }
    return { kind: 'error', text: 'Usage: /nova status | on | off | run' }
  }
}

export function apply(ctx, config) {
  const repo = repoDir(config)
  const github = githubRepo(config)
  let cache = { at: 0, body: null }
  let inflight = null

  const mission = async () => {
    const now = Date.now()
    if (cache.body && now - cache.at < MISSION_TTL_MS) return cache.body
    if (!inflight) {
      inflight = runPython(repo, ['-m', 'nova3_team.mission', '--root', repo,
        '--repo', github, '--json'], 60000)
        .then((stdout) => {
          cache = { at: Date.now(), body: JSON.parse(stdout) }
          return cache.body
        })
        .finally(() => { inflight = null })
    }
    return inflight
  }

  const missionRoute = async (req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      sendJson(res, 405, { error: 'method not allowed' })
      return
    }
    try {
      sendJson(res, 200, await mission())
    } catch (error) {
      sendJson(res, 503, {
        schema: 'nova3-mission/1', error: String(error.message || error),
        code: error.code || 'MISSION_UNAVAILABLE',
        hint: 'run python -m nova3_team.mission --json in the repository',
      })
    }
  }

  const automationRoute = async (req, res) => {
    if (req.method !== 'POST') {
      sendJson(res, 405, { error: 'method not allowed' })
      return
    }
    if (!sameOrigin(req)) {
      sendJson(res, 403, { error: 'cross-origin request refused' })
      return
    }
    try {
      const body = await readJson(req)
      let result
      if (body.enabled === false) {
        await runPython(repo, ['-m', 'nova3_team.autopilot', 'off'], 15000)
        result = { ok: true, action: 'off' }
      } else if (body.enabled === true) {
        const args = ['-m', 'nova3_team.autopilot', 'enable']
        if (body.auto_merge === false) args.push('--merge-mode', 'off')
        result = { ok: true, action: 'enable', output: await runPython(repo, args, 60000) }
      } else if (body.auto_merge === true || body.auto_merge === false) {
        const out = await runPython(repo, ['-m', 'nova3_team.autopilot', 'configure',
          '--merge-mode', body.auto_merge ? 'on' : 'off'], 15000)
        result = { ok: true, action: 'auto_merge', output: out }
      } else {
        sendJson(res, 400, { error: 'expected {enabled?: boolean, auto_merge?: boolean}' })
        return
      }
      cache = { at: 0, body: null }
      result.state = await mission()
      sendJson(res, 200, result)
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error.message || error),
        code: error.code || 'AUTOMATION_ERROR' })
    }
  }

  const runRoute = async (req, res) => {
    if (req.method !== 'POST') {
      sendJson(res, 405, { error: 'method not allowed' })
      return
    }
    if (!sameOrigin(req)) {
      sendJson(res, 403, { error: 'cross-origin request refused' })
      return
    }
    try {
      const out = await runPython(repo, ['-m', 'nova3_team.autopilot', 'run',
        '--repo', github, '--json'], 120000)
      cache = { at: 0, body: null }
      sendJson(res, 200, { ok: true, run: JSON.parse(out), state: await mission() })
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error.message || error),
        code: error.code || 'RUN_ERROR' })
    }
  }


  // One row per autonomous task round: the supervisor's own record, not a chat.
  const runsRoute = async (req, res) => {
    if (!sameOrigin(req)) {
      sendJson(res, 403, { error: 'cross-origin request refused' })
      return
    }
    try {
      const state = await mission()
      // The supervisor exposes one live agent record; the Runs view presents it
      // as a session card. There is no second run history to invent.
      sendJson(res, 200, { ok: true, agent: state.agent || null,
        worker: state.worker || null })
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error.message || error) })
    }
  }

  ctx.effect(() => {
    const disposers = [
      ctx.webServer.register({ kind: 'exact', path: '/nova3/api/mission', handler: missionRoute }),
      ctx.webServer.register({ kind: 'exact', path: '/nova3/api/automation', handler: automationRoute }),
      ctx.webServer.register({ kind: 'exact', path: '/nova3/api/run', handler: runRoute }),
      ctx.webServer.register({ kind: 'exact', path: '/nova3/api/runs', handler: runsRoute }),
    ]
    return () => { for (const dispose of disposers) dispose() }
  }, 'nova3-control: Mission routes')

  ctx.effect(() => {
    const registry = ctx.commands || (ctx.get ? ctx.get('commands') : undefined)
    if (!registry) {
      console.warn('[nova3-control] no command registry; /nova is unavailable')
      return () => {}
    }
    const dispose = registry.register({
      definitionId: '@nova3/dsh-control/nova',
      name: 'nova',
      input: { hint: 'status | on | off | run' },
      description: 'Manage the autonomous NOVA3 worker: status, on, off, run',
      handler: novaCommand({ repo, github }),
    })
    console.info('[nova3-control] registered /nova')
    return () => Promise.resolve(dispose).then((d) => typeof d === 'function' && d())
  }, 'nova3-control: /nova command')
}
