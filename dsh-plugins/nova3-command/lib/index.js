/**
 * `/nova` — manage the autonomous reconstruction worker from chat.
 *
 * A dedicated tab for one toggle is more UI than the job needs; this registers a
 * command instead. It shells out to the project's own CLI (`nova3_team.autopilot`
 * / `nova3_team.dsh_status`) so there is exactly one implementation of the
 * switch, and reports the worker's real state rather than a summary.
 *
 * Usage: /nova status | on | off | run
 */
import { execFile } from 'node:child_process'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const name = 'nova3-command'
export const inject = ['commands']

const REPO = process.env.NOVA3_REPO || '/home/jason/Projects/nova3_re'
const PYTHON = process.env.NOVA3_PYTHON || join(REPO, '.venv/bin/python')
const GITHUB = 'N-O-V-A-3-RE-Contributors/nova3-reconstruction'

function runPython(args, timeoutMs = 30000) {
  return new Promise((resolve) => {
    execFile(PYTHON, args, { cwd: REPO, timeout: timeoutMs, maxBuffer: 1 << 20 },
      (error, stdout, stderr) => resolve({
        ok: !error,
        out: String(stdout || '').trim(),
        err: String(stderr || '').trim(),
      }))
  })
}

function usage() {
  return 'Usage: /nova status | on | off | run'
}

async function handle(invocation) {
  const text = String((invocation && invocation.input) || '').trim()
  const [verb] = text.split(/\s+/)
  if (!verb || verb === 'status') {
    const result = await runPython(['-m', 'nova3_team.dsh_status'], 30000)
    if (!result.ok) return { kind: 'error', text: `status failed: ${result.err || result.out}`.slice(0, 900) }
    return { kind: 'success', text: result.out.slice(0, 1800) }
  }
  if (verb === 'on' || verb === 'off') {
    const result = await runPython(['-m', 'nova3_team.autopilot', verb,
      '--repo', GITHUB], 60000)
    const body = (result.out || result.err || '').slice(0, 900)
    return { kind: result.ok ? 'success' : 'error',
             text: `autopilot ${verb}: ${body}` }
  }
  if (verb === 'run') {
    const result = await runPython(['-m', 'nova3_team.autopilot', 'run',
      '--repo', GITHUB, '--json'], 180000)
    if (!result.ok) return { kind: 'error', text: `run failed: ${(result.err || '').slice(0, 600)}` }
    try {
      const report = JSON.parse(result.out)
      const merged = (report.merged || []).map((m) => `#${m.pr}`).join(', ') || 'none'
      return { kind: 'success',
               text: `one pass: state=${report.state} approved=${(report.approved || []).length} merged=${merged}` }
    } catch {
      return { kind: 'success', text: result.out.slice(0, 900) }
    }
  }
  return { kind: 'error', text: usage() }
}

export function apply(ctx) {
  ctx.effect(function* () {
    yield ctx.commands.register({
      definitionId: '@nova3/dsh-command-nova',
      name: 'nova',
      description: 'Manage the autonomous NOVA3 worker: status, on, off, run',
      handler: handle,
    })
  }, 'nova3-command lifecycle')
}
