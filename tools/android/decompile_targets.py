#!/usr/bin/env python3
"""Bounded offline evidence export. Generated Java is analysis only, never runtime source."""
import argparse,os,pathlib,subprocess,time,json
root=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument("--force",action="store_true");a=p.parse_args()
targets={"premium":["GL2JNIActivity","GL2JNILib","GL2JNIView","installer.GameInstaller"],"freemium":["GameActivity","GL2JNIActivity"]}
for edition,classes in targets.items():
 for cls in classes:
  package="com.gameloft.android."+("GAND.GloftN3LT" if edition=="premium" else "ANMP.GloftNAHM")
  output=root/f"generated/jadx/{edition}-{cls.rsplit(chr(46),1)[-1]}.java"
  if output.exists() and not a.force: continue
  output.parent.mkdir(parents=True,exist_ok=True)
  cmd=["jadx","-r","-j","1","--single-class",package+"."+cls,"--single-class-output",str(output),str(root/f"originals/N.O.V.A.3_{edition}.apk")]
  log=root/f"research/android/logs/{edition}-{cls}-jadx.log"
  start=time.monotonic()
  with log.open("w") as f:
   try:
    r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env={**os.environ,"JAVA_OPTS":"-Xmx512m"},timeout=240)
    record={"command":cmd,"exit_code":r.returncode,"seconds":time.monotonic()-start}
   except subprocess.TimeoutExpired:
    record={"command":cmd,"exit_code":None,"timeout_seconds":240}
  log.with_suffix(".json").write_text(json.dumps(record,indent=2)+"\n")
  print(json.dumps(record))
