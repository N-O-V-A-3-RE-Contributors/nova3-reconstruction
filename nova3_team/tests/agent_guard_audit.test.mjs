import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, roleGuard } from '../agent_guard.mjs'

function fixture(t, role='reviewer') {
  const parent=mkdtempSync(join(tmpdir(), 'nova-role-audit-'))
  t.after(()=>rmSync(parent,{recursive:true,force:true}))
  const root=join(parent,'checkout'); mkdirSync(root)
  mkdirSync(join(root,'reconstruction'))
  writeFileSync(join(root,'reconstruction','code.cpp'),'source')
  writeFileSync(join(parent,'outside'),'private')
  symlinkSync(join(parent,'outside'),join(root,'reconstruction','escape.cpp'))
  mkdirSync(join(root,'.local')); writeFileSync(join(root,'.local','secret'),'private')
  const guard=roleGuard({root,role,allowedWrites:['reconstruction/code.cpp','reconstruction/escape.cpp'],allowedReads:['reconstruction/code.cpp','reconstruction/escape.cpp','.local/secret','.env','credentials.yaml','.credentials.yaml'],maxToolCalls:100})
  return {root,parent,guard}
}

test('all bounded roles deny shell network permissions and recursive tools',t=>{
  for(const role of ['reviewer','repairer','analysis','test_design']) {
    const {guard}=fixture(t,role)
    for(const name of ['bash','web_fetch','web_search','subagent','subagent_fork','workflow','agent_teams_create','squads_run_member','task_board_run','permission','working_directory','read_mcp_resource','browser_navigate'])
      assert.equal(typeof guard({name,arguments:{}}),'string',`${role} ${name}`)
  }
})
test('read-only roles cannot write and repair scope is exact',t=>{
  const {guard}=fixture(t)
  assert.equal(typeof guard({name:'write',arguments:{file_path:'reconstruction/code.cpp'}}),'string')
  const repair=fixture(t,'repairer').guard
  assert.equal(repair({name:'edit',arguments:{file_path:'reconstruction/code.cpp'}}),undefined)
  assert.equal(typeof repair({name:'write',arguments:{file_path:'reconstruction/new.cpp'}}),'string')
})
test('symlink and direct escapes are denied for read and repair',t=>{
  for(const role of ['reviewer','repairer']) {
    const {guard,parent}=fixture(t,role)
    for(const name of ['read','write']) for(const file_path of ['../outside',join(parent,'outside'),'reconstruction/escape.cpp','.local/secret'])
      assert.equal(typeof guard({name,arguments:{file_path}}),'string')
  }
})
test('approved broken symlink cannot write an outside nonexistent target',t=>{
  const {root,parent}=fixture(t,'repairer')
  symlinkSync(join(parent,'not-created'),join(root,'reconstruction','broken.cpp'))
  const guard=roleGuard({root,role:'repairer',allowedWrites:['reconstruction/broken.cpp'],allowedReads:['reconstruction/broken.cpp']})
  assert.equal(typeof guard({name:'write',arguments:{file_path:'reconstruction/broken.cpp'}}),'string')
  assert.equal(typeof guard({name:'read',arguments:{file_path:'reconstruction/broken.cpp'}}),'string')
})
test('tool-call guard survives permission changes and tool budget expires',t=>{
  const {root}=fixture(t)
  let installed
  const ctx={tools:{guard:g=>{installed=g;return ()=>{}}},effect:fn=>fn()}
  apply(ctx,{root,role:'reviewer',maxToolCalls:1})
  assert.equal(typeof installed({name:'permission',arguments:{preset:'danger-full-access'}}),'string')
  assert.equal(typeof installed({name:'read',arguments:{file_path:'reconstruction/code.cpp'}}),'string')
})
test('recursive root searches cannot expose private subtrees',t=>{
  const {guard}=fixture(t)
  for(const name of ['grep','glob'])
    assert.equal(typeof guard({name,arguments:{path:'.',pattern:'secret'}}),'string',name)
})
test('tracked public files permit explicit read and grep but innocuous untracked files do not',t=>{
  const {root,guard}=fixture(t)
  assert.equal(guard({name:'read',arguments:{file_path:'reconstruction/code.cpp'}}),undefined)
  assert.equal(guard({name:'grep',arguments:{path:'reconstruction/code.cpp',pattern:'source'}}),undefined)
  writeFileSync(join(root,'reconstruction','innocuous.cpp'),'private local contents')
  assert.equal(typeof guard({name:'read',arguments:{file_path:'reconstruction/innocuous.cpp'}}),'string')
  assert.equal(typeof guard({name:'grep',arguments:{path:'reconstruction/innocuous.cpp',pattern:'.'}}),'string')
})
test('credential-shaped files outside private directories remain inaccessible',t=>{
  const {root,guard}=fixture(t)
  for(const file_path of ['.env','credentials.yaml','.credentials.yaml']) {
    writeFileSync(join(root,file_path),'TOKEN=private')
    assert.equal(typeof guard({name:'read',arguments:{file_path}}),'string',file_path)
  }
})
