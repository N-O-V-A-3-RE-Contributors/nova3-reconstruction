// Trusted per-process role enforcement. Never load this as an unrestricted user toggle.
import { realpathSync, existsSync, statSync, lstatSync } from 'node:fs'
import { resolve, relative, sep } from 'node:path'
export const name = 'nova3-bounded-role'
export const inject = ['tools']
const PRIVATE = new Set(['.git', '.local', '.cache', '.venv', '.env', '.credentials.yaml', '.nova-private', 'node_modules', 'reference', 'originals', 'analysis', 'research', 'generated', 'Archive', 'analysis_db', 'materials', 'synthetic', 'asm'])

function canonical(path) {
  if (existsSync(path) || lstatSync(path, { throwIfNoEntry: false })?.isSymbolicLink()) return realpathSync(path)
  const parent = resolve(path, '..')
  return resolve(realpathSync(parent), path.split(sep).at(-1))
}
export function roleGuard(config) {
  const root = realpathSync(resolve(config.root))
  const writable = config.role === 'repairer'
  // Recursive glob/grep can expose ignored/private descendants even when the
  // root is public. Allow read/grep of explicit files only; no directory scans.
  const reads = new Set(['read', 'grep'])
  const writes = new Set(['edit', 'write'])
  const allowed = config.allowedWrites || []
  let calls = 0
  return (exec) => {
    if (++calls > (config.maxToolCalls || 40)) return 'Role tool-call budget exhausted'
    // No shell, network, credentials, process/session management or recursive agents.
    if (!reads.has(exec.name) && !(writable && writes.has(exec.name))) return 'Tool unavailable to this bounded role'
    const args = exec.arguments || {}
    const raw = args.file_path ?? args.path ?? '.'
    if (typeof raw !== 'string') return 'Path must be a string'
    let rel
    try { rel = relative(root, canonical(resolve(root, raw))) } catch { return 'Cannot resolve confined path' }
    if (rel === '..' || rel.startsWith('..' + sep) || rel.startsWith(sep)) return 'Path outside task checkout'
    const parts = rel.split(sep)
    if (parts.some(part => PRIVATE.has(part) || part.startsWith('.') || /^(?:credentials?|secrets?|tokens?|passwords?|api[_-]?keys?|id_rsa|id_ed25519)(?:\.|$)/i.test(part))) return 'Private/reference/control files are unavailable to this role'
    if (reads.has(exec.name)) {
      if (!(config.allowedReads || []).some(path => rel === path || (path.endsWith('/') && rel.startsWith(path)))) return 'File is not tracked or explicitly approved'
      try { if (!statSync(resolve(root, raw)).isFile()) return 'Read/search requires an explicit file' }
      catch { return 'Read/search target unavailable' }
    }
    if (writes.has(exec.name) && !allowed.some(path => rel === path || (path.endsWith('/') && rel.startsWith(path)))) return 'Write outside approved task scope'
    return undefined
  }
}
export function apply(ctx, config) {
  const guard = roleGuard(config)
  ctx.effect(() => ctx.tools.guard(guard), 'NOVA bounded role enforcement')
}
