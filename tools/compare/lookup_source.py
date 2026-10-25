#!/usr/bin/env python3
"""Compare C++ lookup primitives against captured original ARM observations."""
import json,pathlib,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
j=json.loads((ROOT/'tools/compare/lookup_evidence_results.json').read_text());exe=str(ROOT/'build/host/nova3_zip_lookup');count=0
for c in j['cases']:
 actual=subprocess.check_output([exe,c['kind'],str(c['flags']),c['name']],text=True).strip()
 if c['kind']=='entry':assert actual==c['key']
 else:
  found=int(subprocess.check_output([exe,'find',actual,*c['keys']],text=True))
  # Native -1 is unsigned32, source sentinel follows host size_t width.
  if c['index']==0xffffffff:assert found in (0xffffffff,0xffffffffffffffff)
  else:assert found==c['index']
 count+=1
for c in j['sort_cases']:
 actual=[int(x) for x in subprocess.check_output([exe,'sort',*c['keys']],text=True).split()];assert actual==c['archive_index_order']
report=dict(normalization_lookup_cases=count,duplicate_sort_cases=len(j['sort_cases']),scope='C++ logical-key/heap/lower_bound primitives match original ARM observations. Native oracle models COW string and allocator helpers; whole native ownership/class equivalence is excluded.')
(ROOT/'research/elf/lookup_source_verification.json').write_text(json.dumps(report,indent=2)+'\n');print(f'PASS: {count} C++ normalization/lookup cases and3 duplicate orders match ARM observations')
