#!/usr/bin/env python3
"""Install pinned official rev.ng research tool locally; never executes installer."""
import hashlib,json,pathlib,posixpath,tarfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
ARCHIVE=ROOT/".cache/revng-c865bc3.tar.xz"
DEST=ROOT/".cache/revng-c865bc3"
URL="https://revng-website-downloads.s3.fr-par.scw.cloud/binary-archives/public/linux-x86-64/revng-distributable-public-demo/default/none_c865bc3d1d83af224d0b92b0369b3bd412a46c04.tar.xz"
EXPECTED_SIZE=544913104
EXPECTED_SHA256="a1e7d2a622ea48a8c8888d0bdbed7b9ed965a38ebfe16fcc4582a7957e96cc94"

def safe(name):
    return not name.startswith("/") and ".." not in pathlib.PurePosixPath(name).parts

def main():
    if ARCHIVE.stat().st_size!=EXPECTED_SIZE:raise ValueError("download incomplete")
    digest=hashlib.file_digest(ARCHIVE.open("rb"),"sha256").hexdigest()
    if digest!=EXPECTED_SHA256:raise ValueError("archive digest mismatch")
    report=dict(url=URL,archive=str(ARCHIVE.relative_to(ROOT)),size=ARCHIVE.stat().st_size,sha256=digest)
    with tarfile.open(ARCHIVE,"r:xz") as tar:
        members=tar.getmembers();total=sum(x.size for x in members)
        if len(members)>100000 or total>4*1024**3:raise ValueError("archive resource limit")
        for m in members:
            if not safe(m.name) or m.isdev() or m.isfifo():raise ValueError("unsafe archive member "+m.name)
            if m.issym() or m.islnk():
                target=posixpath.normpath(posixpath.join(posixpath.dirname(m.name),m.linkname)) if m.issym() else posixpath.normpath(m.linkname)
                if not safe(target):raise ValueError("escaping archive link "+m.name+" -> "+m.linkname)
        report.update(member_count=len(members),total_uncompressed=total,top_level=sorted(set(m.name.split("/")[0] for m in members)))
        (ROOT/"tools/revng/distribution.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(report),flush=True)
        DEST.mkdir(exist_ok=True)
        tar.extractall(DEST,filter="data")
    print("Installed",DEST,flush=True)
if __name__=="__main__":main()
