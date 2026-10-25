#!/usr/bin/env python3
"""Prove portable build/tests in a disposable tree containing only owned source."""
import pathlib,shutil,subprocess,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='nova3-source-only-') as td:
 p=pathlib.Path(td);shutil.copy2(ROOT/'CMakeLists.txt',p/'CMakeLists.txt');shutil.copytree(ROOT/'reconstruction',p/'reconstruction',ignore=shutil.ignore_patterns('__pycache__'))
 for cmd in [['cmake','-S',str(p),'-B',str(p/'build'),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(p/'build')],['ctest','--test-dir',str(p/'build'),'--output-on-failure']]:subprocess.run(cmd,check=True)
 print('PASS: build and CTests from owned source alone, with no original/APK/archive/research files')
