#!/usr/bin/env python3
"""Read-only Android inventory using installed aapt/readelf; no code execution."""
import hashlib,json,pathlib,re,subprocess,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=ROOT/"research/android"
OUT.mkdir(parents=True,exist_ok=True)
report={"tools":{},"editions":{}}
for tool,args in [("aapt",["version"]),("jadx",["--version"]),("apktool",["--version"])]:
    p=subprocess.run([tool,*args],capture_output=True,text=True)
    report["tools"][tool]={"exit_code":p.returncode,"version":p.stdout.strip(),"stderr":p.stderr.strip()}
for edition in ("premium","freemium"):
    apk=ROOT/f"originals/N.O.V.A.3_{edition}.apk"
    badging=subprocess.run(["aapt","dump","badging",str(apk)],capture_output=True,text=True,check=True).stdout
    manifest=subprocess.run(["aapt","dump","xmltree",str(apk),"AndroidManifest.xml"],capture_output=True,text=True,check=True).stdout
    (OUT/f"logs/{edition}-badging.txt").write_text(badging)
    (OUT/f"logs/{edition}-manifest-tree.txt").write_text(manifest)
    package=re.search("package: name=\x27([^\x27]+)\x27 versionCode=\x27([^\x27]+)\x27 versionName=\x27([^\x27]+)\x27",badging).groups()
    e={"apk":str(apk.relative_to(ROOT)),"sha256":hashlib.file_digest(apk.open("rb"),"sha256").hexdigest(),"package":package[0],"version_code":int(package[1]),"version_name":package[2],"min_sdk":re.search("sdkVersion:\x27([^\x27]+)",badging).group(1),"target_sdk":re.search("targetSdkVersion:\x27([^\x27]+)",badging).group(1),"permissions":re.findall("uses-permission: name=\x27([^\x27]+)",badging),"launch_activity":re.search("launchable-activity: name=\x27([^\x27]+)",badging).group(1)}
    with zipfile.ZipFile(apk) as z:
        e["dex"]=[{"path":i.filename,"size":i.file_size,"sha256":hashlib.sha256(z.read(i)).hexdigest()} for i in z.infolist() if re.fullmatch(r"classes\d*\.dex",i.filename)]
        e["native_members"]=[{"path":i.filename,"size":i.file_size} for i in z.infolist() if re.fullmatch(r"lib/[^/]+/[^/]+\.so",i.filename)]
    exports=[]
    for so in sorted((ROOT/f"analysis/N.O.V.A.3_{edition}/lib").glob("*/*.so")):
        p=subprocess.run(["readelf","--dyn-syms","--wide",str(so)],capture_output=True,text=True,check=True)
        (OUT/f"logs/{edition}-{so.name}-dynsym.txt").write_text(p.stdout)
        for line in p.stdout.splitlines():
            cols=line.split()
            if len(cols)>=8 and cols[3]=="FUNC" and cols[6]!="UND" and (cols[7].startswith("Java_") or cols[7]=="JNI_OnLoad"):
                raw=int(cols[1],16)
                exports.append({"binary":str(so.relative_to(ROOT)),"symbol":cols[7],"symbol_value":raw,"address":raw&~1,"thumb":bool(raw&1),"size":int(cols[2]),"evidence":"readelf --dyn-syms --wide"})
    e["jni_exports"]=exports
    report["editions"][edition]=e
(OUT/"inventory.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:{"package":v["package"],"native_members":len(v["native_members"]),"jni_exports":len(v["jni_exports"])} for k,v in report["editions"].items()},indent=2))
