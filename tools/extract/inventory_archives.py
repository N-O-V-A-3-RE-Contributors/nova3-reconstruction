#!/usr/bin/env python3
"""Read-only bounded archive inspection. No filesystem member extraction.
Uses 7z only for listing / bounded RAR streams; ZIP uses Python stdlib.
"""
import argparse, collections, hashlib, json, pathlib, re, struct, subprocess, zipfile, zlib

ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=ROOT/"research/assets"

def safe_path(name):
    p=pathlib.PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not p.is_absolute() and ".." not in p.parts and not re.match(r"^[A-Za-z]:",name) and "\0" not in name

def exact(f,n):
    b=f.read(n)
    if len(b)!=n: raise ValueError(f"truncated read {len(b)} of {n}")
    return b

def skip(f,n):
    while n:
        b=exact(f,min(n,1024*1024)); n-=len(b)

def zip_local_index(f, edition, obb_name, obb_size):
    records=[]; offset=0; specimens=[]
    while True:
        sig=exact(f,4)
        if sig==b"PK\x01\x02": break
        if sig!=b"PK\x03\x04": raise ValueError(f"unexpected ZIP signature {sig.hex()} at {offset}")
        h=exact(f,26)
        version,flags,method,mtime,mdate,crc,packed,size,nlen,xlen=struct.unpack("<5H3I2H",h)
        name=exact(f,nlen).decode("utf-8" if flags&0x800 else "cp437")
        extra=exact(f,xlen)
        if flags&0x9: raise ValueError("encrypted/data-descriptor entry unsupported")
        if packed==0xffffffff or size==0xffffffff: raise ValueError("ZIP64 unsupported")
        if not safe_path(name): raise ValueError(f"unsafe path {name!r}")
        if offset+30+nlen+xlen+packed>obb_size: raise ValueError("member outside OBB bounds")
        record=dict(name=name,offset=offset,data_offset=offset+30+nlen+xlen,compressed_size=packed,size=size,method=method,flags=flags,crc32=f"{crc:08x}",safe_path=True)
        if len(records)==0 and packed<=1024*1024 and size<=1024*1024:
            compressed=exact(f,packed)
            if method==8:
                inflater=zlib.decompressobj(-15)
                b=inflater.decompress(compressed,size+1)
                if not inflater.eof or inflater.unconsumed_tail: raise ValueError("invalid/oversize DEFLATE specimen")
            else: b=compressed
            if len(b)!=size or zlib.crc32(b)!=crc: raise ValueError("specimen CRC/size mismatch")
            specimen=OUT/(edition+"_"+name.replace("/","_")+".specimen")
            specimen.write_bytes(b)
            record.update(specimen=str(specimen.relative_to(ROOT)),sha256=hashlib.sha256(b).hexdigest(),prefix_hex=b[:128].hex())
        else: skip(f,packed)
        records.append(record);offset+=30+nlen+xlen+packed
    # Consume remainder so outer ZIP CRC is verified; preserve central directory bytes.
    tail=sig+f.read(16*1024*1024+1)
    if len(tail)>16*1024*1024: raise ValueError("unexpectedly large central directory")
    cursor=0; central=[]
    while tail[cursor:cursor+4]==b"PK\x01\x02":
        fields=struct.unpack_from("<6H3I5H2I",tail,cursor+4)
        nlen,xlen,clen=fields[9:12]
        central.append(tail[cursor+46:cursor+46+nlen].decode("utf8" if fields[2]&0x800 else "cp437"))
        cursor+=46+nlen+xlen+clen
    if tail[cursor:cursor+4]!=b"PK\x05\x06": raise ValueError("missing ZIP EOCD")
    if central!=[r["name"] for r in records]: raise ValueError("local/central names differ")
    return dict(obb_name=obb_name,obb_size=obb_size,state="INVENTORIED",entries=records,total_entries=len(records),total_uncompressed=sum(r["size"] for r in records),central_directory_validated=True,extensions=dict(collections.Counter(pathlib.PurePosixPath(r["name"]).suffix for r in records)))

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    summaries=[]
    for edition in ("premium","freemium"):
        p=ROOT/f"Archive/Nova3_{edition}_only_OBB.rar"
        result=subprocess.run(["7z","l","-slt",str(p)],capture_output=True,text=True,check=True)
        (OUT/f"{edition}_rar_listing.txt").write_text(result.stdout)
        members=[]
        for block in result.stdout.split("\n\n"):
            fields=dict(line.split(" = ",1) for line in block.splitlines() if " = " in line)
            if fields.get("Path","").endswith(".obb"):
                name=fields["Path"]
                if not safe_path(name) or any(fields.get(k) for k in ("Symbolic Link","Hard Link","Copy Link")): raise ValueError("unsafe outer member")
                members.append(dict(name=name,size=int(fields["Size"]),packed=int(fields["Packed Size"]),crc32=fields["CRC"],ratio=int(fields["Size"])/max(1,int(fields["Packed Size"]))))
        summary=dict(edition=edition,archive=str(p.relative_to(ROOT)),size=p.stat().st_size,signature_hex=p.open("rb").read(8).hex(),members=members,total_size=sum(m["size"] for m in members),path_validation="all member OBB paths safe; no links",limits="no bulk extraction; 64 KiB OBB prefixes; streaming ZIP index")
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                for m in members:
                    with z.open(m["name"]) as f:
                        index=zip_local_index(f,edition,m["name"],m["size"])
                    target=OUT/(edition+"_"+pathlib.PurePosixPath(m["name"]).name+".index.json")
                    target.write_text(json.dumps(index,indent=2)+"\n")
                    print(target.name,index["total_entries"],index["total_uncompressed"],flush=True)
                    m["index"]=str(target.relative_to(ROOT))
        for m in members:
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z:
                    with z.open(m["name"]) as f: prefix=f.read(65536)
            else:
                proc=subprocess.Popen(["7z","x","-so",str(p),m["name"]],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                prefix=proc.stdout.read(65536);proc.terminate();proc.stdout.close();proc.wait()
            (OUT/(edition+"_"+pathlib.PurePosixPath(m["name"]).name+".prefix")).write_bytes(prefix)
            m["prefix_sha256"]=hashlib.sha256(prefix).hexdigest();m["signature_hex"]=prefix[:32].hex()
        summaries.append(summary)
    (OUT/"archive_inventory.json").write_text(json.dumps(summaries,indent=2)+"\n")

if __name__=="__main__":main()
