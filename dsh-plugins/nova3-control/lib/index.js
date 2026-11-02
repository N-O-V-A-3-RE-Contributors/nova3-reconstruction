/**
 * NOVA3 owner control plane as a DSH slash command.
 *
 * A DSH plugin is a cordis plugin module: it exports `name`, `inject` and
 * `apply(ctx)`. This one registers `/nova` with the composed command registry,
 * so the owner can read worker status, project ETA, review verdicts, model
 * routing and toolchain plans from any DSH profile (web or TUI) instead of a
 * single chat session.
 *
 * The command shells out to the repository's own read-only CLIs. It never
 * mutates GitHub, never merges and never reads credentials; every subcommand is
 * one of the audited `python -m nova3_team.*` entry points.
 *
 * Configuration: `NOVA3_PROJECT_ROOT` (default: the checkout this plugin was
 * installed from is passed in the profile patch as `config.repo`).
 */
import { execFile } from 'node:child_process'

export const name = 'nova3-control'
export const inject = ['commands']

const MAX_OUTPUT = 12000

const USAGE = [
  'Usage: /nova <command>',
  '  status                 worker state, queue, open owner requests',
  '  eta                    project inventory, coverage and ETA scenarios',
  '  review <pr>            owner/admin compliance review of a PR',
  '  fleet [task]           model/host routing recommendation',
  '  toolchain [tier]       per-OS toolchain plan (core/project/full)',
].join('\n')

function repoDir(ctx) {
  return (ctx && ctx.config && ctx.config.repo) ||
    process.env.NOVA3_PROJECT_ROOT ||
    '/home/jason/Projects/nova3_re'
}

function runPython(repo, args) {
  return new Promise((resolve) => {
    execFile('python3', args, { cwd: repo, timeout: 120000, maxBuffer: 4 * 1024 * 1024 },
      (error, stdout, stderr) => {
        const text = String(stdout || stderr || '').slice(-MAX_OUTPUT)
        resolve({ kind: error ? 'error' : 'success', text: text || '(no output)' })
      })
  })
}

async function execute(ctx, invocation) {
  const raw = String(invocation.rawInput || '').trim()
  const [sub, ...rest] = raw.split(/\s+/).filter(Boolean)
  const repo = repoDir(ctx)
  switch (sub || 'status') {
    case 'status':
      return runPython(repo, ['-m', 'nova3_team.dsh_status'])
    case 'eta':
    case 'analysis':
      return runPython(repo, ['-m', 'nova3_team.analysis', '--root', repo])
    case 'review': {
      const pr = rest[0]
      if (!pr || !/^\d+$/.test(pr)) return { kind: 'error', text: USAGE }
      const repoName = (ctx && ctx.config && ctx.config.github) || 'N-O-V-A-3-RE-Contributors/nova3-reconstruction'
      return runPython(repo, ['-m', 'nova3_team.review', '--repo', repoName, '--pr', pr])
    }
    case 'fleet':
      return runPython(repo, ['-m', 'nova3_team.fleet', '--task', rest[0] || 'recon'])
    case 'toolchain':
      return runPython(repo, ['-m', 'tools.dsh_loop.toolchain', '--tier', rest[0] || 'project', '--plan'])
    case 'help':
      return { kind: 'success', text: USAGE }
    default:
      return { kind: 'error', text: USAGE }
  }
}

export function apply(ctx) {
  const active = new Set()
  const handler = (invocation) => {
    const operation = execute(ctx, invocation)
    active.add(operation)
    const retire = () => active.delete(operation)
    operation.then(retire, retire)
    return operation
  }
  ctx.effect(function* () {
    yield async () => { await Promise.allSettled(active) }
    yield ctx.commands.register({
      definitionId: '@nova3/dsh-control',
      name: 'nova',
      description: 'NOVA3 control: status, ETA, review, fleet, toolchain',
      handler,
    })
  }, 'nova3-control lifecycle')
}
