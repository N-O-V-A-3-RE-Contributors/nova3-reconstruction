#!/usr/bin/env python3
"""Compare all discovered SB header fields and source payload CRC/size checks."""
import collections,hashlib,json,pathlib,struct,subprocess,tempfile,zipfile,zlib
ROOT=pathlib.Path(__file__).resolve().parents[2];cli=ROOT/'build/host/nova3_scrambled_zip';reports=[];total=0;decoded=0
with tempfile.TemporaryDirectory(prefix='nova3-sb-') as td:
 td=pathlib.Path(td)
 for edition in ('main','patch'):
  with zipfile.ZipFile(ROOT/f'.cache/assets/premium-{edition}.obb') as z:
   for info in z.infolist():
    if not info.filename.endswith('.gla'):continue
    with z.open(info) as f:magic=f.read(4)
    if magic[:2]!=b'SB' and magic!=b'SEND':continue
    data=z.read(info);key=info.filename.rsplit('/',1)[-1].encode();file=td/'archive.gla';file.write_bytes(data)
    actual=json.loads(subprocess.check_output([str(cli),str(file),key.decode(),'--verify'],text=True));expected=[];p=0;payload_bytes=0
    while data[p:p+2]==b'SB':
     length=struct.unpack_from('<H',data,p+2)[0];header=bytes(v^key[i%len(key)] for i,v in enumerate(data[p+4:p+4+length]));version,flags,method,time,date,crc,compressed,size,nl,extra=struct.unpack_from('<5H3I2H',header);assert flags==0 and length==26+nl+extra
     name=header[26:26+nl];offset=p+4+length;payload=data[offset:offset+compressed];plain=zlib.decompress(payload,-15) if method==8 else payload
     assert len(plain)==size and zlib.crc32(plain)==crc
     expected.append(dict(name_hex=name.hex(),offset=offset,compressed=compressed,size=size,crc=crc,method=method));payload_bytes+=size;p=offset+compressed
    assert actual==expected and data[p:p+4] in (b'SDIR',b'SEND')
    for target in ('NV_DiffuseVC.bdae','NV_DiffuseVC_FS.glsl','NV_DiffuseVC_VS.glsl'):
     if any(bytes.fromhex(e['name_hex']).decode()==target for e in expected):
      output=ROOT/'.cache/assets'/f'{edition}-{target}';subprocess.run([str(cli),str(file),key.decode(),target,str(output)],check=True)
    reports.append(dict(obb=edition,archive=info.filename,sha256=hashlib.sha256(data).hexdigest(),entries=expected,entry_count=len(expected),decoded_bytes=payload_bytes,scan_end=p,trailer_size=len(data)-p,trailer_magic=data[p:p+4].decode()))
    total+=len(expected);decoded+=payload_bytes
    print('PASS',edition,info.filename,len(expected),'entries',flush=True)
report=dict(archive_count=len(reports),entry_count=total,decoded_bytes=decoded,archives=reports,scope='Actual SB/SEND local-record subset; source header fields match independent Python XOR/ZIP-body reader. Every scanned payload raw-inflates/stores to declared size and matches CRC in both implementations. SDIR/trailer contents and custom big-endian PAK variants are not parsed; native key/scan/deflate semantics backed by offline decompilation, no full native-reader execution claimed.')
(ROOT/'research/elf/scrambled_zip_source_verification.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(reports),'archives;',total,'entries;',decoded,'decoded bytes')
