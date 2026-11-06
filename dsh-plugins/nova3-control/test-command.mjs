import test from 'node:test'
import assert from 'node:assert/strict'
import { novaCommand, apply } from './lib/index.js'

test('registered slash command advertises free-form subcommands', () => {
  const definitions = []
  apply({
    webServer: { register: () => () => {} },
    commands: { register: (def) => { definitions.push(def); return () => {} } },
    effect: (fn) => fn(),
  }, { repo: '/tmp/project', github: 'owner/project' })
  assert.equal(definitions[0].name, 'nova')
  assert.match(definitions[0].input.hint, /status/)
})

function setup() {
  const calls = []
  const handler = novaCommand({ repo: '/tmp/project', github: 'owner/project',
    executor: async (args) => { calls.push(args); return { ok: true, out: 'status', err: '' } } })
  return { calls, handler }
}
test('DSH rawInput is parsed, not nonexistent input', async () => {
  const {calls, handler} = setup()
  const result = await handler({rawInput: 'off'})
  assert.equal(result.kind, 'success')
  assert.deepEqual(calls[0], ['-m', 'nova3_team.agent_control', 'off', '--repo', 'owner/project'])
})
test('on uses authorized control, not nonexistent autopilot on', async () => {
  const {calls, handler} = setup()
  await handler({rawInput: 'on'})
  assert.equal(calls[0][1], 'nova3_team.agent_control')
  assert.equal(calls[0][2], 'on')
})
test('status is read-only; unknown verbs cannot run commands', async () => {
  const {calls, handler} = setup()
  await handler({rawInput: 'status'})
  assert.deepEqual(calls[0], ['-m', 'nova3_team.dsh_status'])
  assert.equal((await handler({rawInput: 'off; evil'})).kind, 'error')
  assert.equal(calls.length, 1)
})
