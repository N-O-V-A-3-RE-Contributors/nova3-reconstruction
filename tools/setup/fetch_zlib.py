#!/usr/bin/env python3
"""Reproduce the unmodified, minimal zlib inflater source vendoring."""
import hashlib,json,pathlib,re,tarfile,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2]
VERSION="1.3.2"
URL=f"https://zlib.net/fossils/zlib-{VERSION}.tar.gz"
SHA256="bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16"
CACHE=ROOT/".cache/zlib"/f"zlib-{VERSION}.tar.gz"
DEST=ROOT/"reconstruction/third_party"/f"zlib-{VERSION}"
SOURCES=("adler32.c","crc32.c","inflate.c","inffast.c","inftrees.c","zutil.c")

def main():
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    if not CACHE.exists():
        with urllib.request.urlopen(URL,timeout=60) as r:
            blob=r.read(8*1024*1024+1)
        if len(blob)>8*1024*1024:raise ValueError("download exceeds 8 MiB")
        if hashlib.sha256(blob).hexdigest()!=SHA256:raise ValueError("source checksum mismatch")
        CACHE.write_bytes(blob)
    blob=CACHE.read_bytes()
    if len(blob)>8*1024*1024 or hashlib.sha256(blob).hexdigest()!=SHA256:raise ValueError("source checksum/size mismatch")
    with tarfile.open(CACHE,"r:gz") as t:
        members=t.getmembers()
        if len(members)>10000 or sum(m.size for m in members)>50*1024*1024:raise ValueError("archive resource limit")
        for m in members:
            p=pathlib.PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts or (not m.isfile() and not m.isdir()):raise ValueError("unsafe archive member")
            if not p.parts or p.parts[0]!=f"zlib-{VERSION}":raise ValueError("wrong archive root")
        files={str(pathlib.PurePosixPath(m.name).relative_to(f"zlib-{VERSION}")):m for m in members if m.isfile()}
        selected=set(SOURCES)|{"LICENSE"};pending=list(SOURCES)
        while pending:
            name=pending.pop();data=t.extractfile(files[name]).read()
            for quoted in re.findall(rb"^\s*#\s*include\s*\"([^\"]+)\"",data,re.M):
                include=quoted.decode("ascii")
                if include not in files:raise ValueError("missing upstream include "+include)
                if include not in selected:selected.add(include);pending.append(include)
        hashes={}
        for name in sorted(selected):
            data=t.extractfile(files[name]).read();target=DEST/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            hashes[name]=hashlib.sha256(data).hexdigest()
    manifest=dict(name="zlib",version=VERSION,license="Zlib",source_url=URL,source_archive_sha256=SHA256,homepage="https://www.zlib.net/",upstream_files_unmodified=True,sources=list(SOURCES),file_sha256=hashes,selection="six inflater/checksum/support C units plus recursive quoted includes and LICENSE; no compressor/gz wrapper",dependencies="C standard library; no original game executable input")
    (DEST.parent/"zlib_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(f"Vendored {len(hashes)} unchanged files to {DEST}")
if __name__=="__main__":main()
