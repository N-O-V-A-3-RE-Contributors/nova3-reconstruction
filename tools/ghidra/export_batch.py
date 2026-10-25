#!/usr/bin/env python3
"""Persistent PyGhidra import, bounded selected-function disassembly/decompilation.
Default address selection is the first reconstructed CRC chain; pass --start/--limit
for resumable symbol-address ordered batches. No full autoanalysis by default.
"""
import argparse,hashlib,json,pathlib,sqlite3,os,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(); p.add_argument('--binary',default='analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so'); p.add_argument('--addresses',default='0xde2cc,0x8d890c'); p.add_argument('--start',type=lambda s:int(s,0),default=0); p.add_argument('--limit',type=int,default=16); p.add_argument('--all-symbols',action='store_true'); p.add_argument('--retry',action='store_true'); args=p.parse_args()
os.environ.setdefault('JAVA_HOME','/usr/lib/jvm/java-21-openjdk')
import pyghidra
pyghidra.start(install_dir=pathlib.Path('/opt/ghidra'))
from ghidra.app.decompiler import DecompInterface
from ghidra.program.model.address import AddressSet
from ghidra.util.task import ConsoleTaskMonitor
binary=ROOT/args.binary; digest=hashlib.sha256(binary.read_bytes()).hexdigest(); db=sqlite3.connect(ROOT/'analysis_db/index.sqlite')
rows=db.execute('SELECT address,size,name,id,thumb FROM functions WHERE binary_hash=? AND address>=? ORDER BY address,name',(digest,args.start)).fetchall()
select=None if args.all_symbols else {int(x,0) for x in args.addresses.split(',')}
# Alias symbols share one actual function export.
chosen={}
for r in rows:
 if select is None or r[0] in select: chosen.setdefault(r[0],r)
rows=list(chosen.values())
out=ROOT/'generated/ghidra/exports'/digest; out.mkdir(parents=True,exist_ok=True)
rows=[r for r in rows if args.retry or not (out/f'{r[0]:08x}.json').exists()][:args.limit]
failures=0
with pyghidra.open_program(binary,project_location=ROOT/'generated/ghidra',project_name='premium',analyze=False,nested_project_location=False) as api:
 program=api.getCurrentProgram(); image_base=program.getImageBase().getOffset(); monitor=ConsoleTaskMonitor(); decomp=DecompInterface(); decomp.openProgram(program)
 print('OPEN',program.getName(),program.getLanguageID(),flush=True)
 # Remove the mislabeled function created by the initial unrebased experiment.
 # This is a project annotation only; original imported bytes remain untouched.
 stale=program.getFunctionManager().getFunctionAt(api.toAddr(0x8d890c))
 if stale is not None and str(stale.getName())=='_ZN3glf10CrcChecker5CRC32EPKvij' and image_base==0x10000:
  program.getFunctionManager().removeFunction(stale.getEntryPoint())
 for address,size,name,ident,thumb in rows:
  dest=out/f'{address:08x}.json'; aid=f'{digest}:{address:08x}:ghidra-12.1.2'
  if dest.exists() and not args.retry: print('SKIP',hex(address),flush=True); continue
  try:
   a=api.toAddr(address + image_base)
   from elftools.elf.elffile import ELFFile
   with binary.open('rb') as elfstream:
    elf=ELFFile(elfstream)
    segment=next(seg for seg in elf.iter_segments() if seg['p_type']=='PT_LOAD' and seg['p_vaddr'] <= address < seg['p_vaddr']+seg['p_filesz'])
    elfstream.seek(segment['p_offset']+address-segment['p_vaddr']); expected=elfstream.read(min(size,16))
   observed=bytes(int(program.getMemory().getByte(a.add(i))) & 255 for i in range(len(expected)))
   if observed != expected: raise RuntimeError('ELF VA / Ghidra byte mapping mismatch')
   # ELF symbols retain Thumb low bits; create disassembly in the observed mode.
   if thumb:
    from java.math import BigInteger
    program.getProgramContext().setValue(program.getRegister('TMode'),a,a,BigInteger.ONE)
   from ghidra.app.cmd.disassemble import DisassembleCommand
   bounds=AddressSet(a,a.add(max(size,1)-1))
   cmd=DisassembleCommand(a,bounds,True); cmd.enableCodeAnalysis(False)
   if not cmd.applyTo(program,monitor): raise RuntimeError('disassembly failed: '+str(cmd.getStatusMsg()))
   fn=program.getFunctionManager().getFunctionAt(a)
   if fn is None: fn=api.createFunction(a,name)
   if fn is None: raise RuntimeError('function creation failed')
   fn.setBody(bounds)
   decomp.flushCache()
   res=decomp.decompileFunction(fn,30,monitor)
   instructions=[]; calls=[]; refs=[]; blocks=[]
   from ghidra.program.model.block import BasicBlockModel
   bi=BasicBlockModel(program).getCodeBlocksContaining(bounds,monitor)
   while bi.hasNext():
    block=bi.next(); blocks.append({'start':str(block.getFirstStartAddress()),'end':str(block.getMaxAddress()),'flow':str(block.getFlowType())})
   it=program.getListing().getInstructions(fn.getBody(),True)
   while it.hasNext():
    ins=it.next(); instructions.append({'address':str(ins.getAddress()),'text':str(ins),'bytes':bytes(int(b)&255 for b in ins.getBytes()).hex()})
    for r in ins.getReferencesFrom():
     ref={'from':str(r.getFromAddress()),'to':str(r.getToAddress()),'type':str(r.getReferenceType())}; refs.append(ref)
     if r.getReferenceType().isCall(): calls.append(ref)
   incoming=[{'from':str(r.getFromAddress()),'type':str(r.getReferenceType())} for r in program.getReferenceManager().getReferencesTo(a)]
   c=str(res.getDecompiledFunction().getC()) if res.decompileCompleted() else None
   if c is None: failures+=1
   record=dict(ghidra_image_base=image_base,ghidra_address=address+image_base,byte_mapping_validated=True,binary_sha256=digest,architecture=str(program.getLanguageID()),address=address,raw_value=address|thumb,thumb=thumb,symbol=name,symbol_size=size,signature=str(fn.getSignature()),calling_convention=str(fn.getCallingConventionName()),ghidra_version='12.1.2',analysis_scope='ELF import plus selected disassembly; no global autoanalysis',basic_blocks=blocks,instructions=instructions,references=refs,incoming=incoming,calls=calls,c=c,error=str(res.getErrorMessage()))
   dest.write_text(json.dumps(record,indent=2)+'\n'); state='DECOMPILED' if c else 'DISASSEMBLED'
   db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?)',(aid,ident,'Ghidra 12.1.2 / PyGhidra 3.2.0',state,str(dest.relative_to(ROOT)),record['error']))
   db.execute("UPDATE functions SET state=CASE WHEN state IN ('UNDERSTOOD','IMPLEMENTED','TESTED','VERIFIED') THEN state ELSE ? END,signature=?,unresolved=? WHERE binary_hash=? AND address=?",(state,record['signature'],'caller/xref/global analysis incomplete',digest,address))
   db.execute('DELETE FROM edges WHERE source_id=?',(ident,))
   for r in calls:
    try: db.execute('INSERT OR REPLACE INTO edges VALUES(?,?,?,?)',(ident,int(r['to'],16)-image_base,'CALL',str(dest.relative_to(ROOT))))
    except ValueError: pass
   db.commit(); print(state,hex(address),name,len(instructions),flush=True)
  except Exception as ex:
   failures+=1
   db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?)',(aid,ident,'Ghidra 12.1.2','FAILED',str(dest.relative_to(ROOT)),repr(ex))); db.commit(); print('FAILED',hex(address),repr(ex),flush=True)
 decomp.dispose()
print('DONE', 'failures='+str(failures),flush=True)
sys.exit(1 if failures else 0)
