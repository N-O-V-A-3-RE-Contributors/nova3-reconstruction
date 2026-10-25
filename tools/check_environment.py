#!/usr/bin/env python3
"""Read-only host health snapshot; no package or device state changes."""
import datetime,importlib.metadata,json,os,pathlib,platform,shutil,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[1]
def run(argv):
 try:
  p=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
  return dict(command=argv,exit=p.returncode,output=p.stdout[:12000])
 except Exception as e:return dict(command=argv,error=repr(e))
commands=[['uname','-a'],['df','-h',str(ROOT)],['free','-h'],['pacman','-Q','ghidra','jadx','7zip','unrar','cmake','ninja','python','jdk21-openjdk'],['java','-version'],['cmake','--version'],['ninja','--version'],['c++','--version'],['clang','--version'],['adb','version'],['adb','devices','-l']]
r=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),cwd=str(ROOT),uid=os.getuid(),python=platform.python_version(),executables={x:shutil.which(x) for x in ['ghidra','jadx','apktool','7z','unrar','revng','adb','cmake','ninja','clang','llvm-objdump','readelf','git']},python_packages={x:importlib.metadata.version(x) for x in ['pyghidra','pyelftools','capstone','pytest','unicorn']},commands=[run(c) for c in commands])
print(json.dumps(r,indent=2))
