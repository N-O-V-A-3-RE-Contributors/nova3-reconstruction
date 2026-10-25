#!/usr/bin/env python3
"""Diagnostic asset geometry projection, not a game renderer/reference frame."""
import argparse,math,pathlib
p=argparse.ArgumentParser();p.add_argument('obj',type=pathlib.Path);p.add_argument('svg',type=pathlib.Path);a=p.parse_args();vertices=[];faces=[]
for line in a.obj.read_text().splitlines():
 fields=line.split()
 if not fields:continue
 if fields[0]=='v':vertices.append(tuple(float(x) for x in fields[1:4]))
 if fields[0]=='f':faces.append(tuple(int(x.split('/')[0])-1 for x in fields[1:]))
assert vertices and all(len(f)==3 and all(0<=i<len(vertices) for i in f) for f in faces)
center=tuple((min(v[i] for v in vertices)+max(v[i] for v in vertices))/2 for i in range(3))
# Diagnostic camera only: no recovered camera/scene/material claims.
yaw=0.65;pitch=0.35
points=[]
for v in vertices:
 x,y,z=(v[i]-center[i] for i in range(3));u=x*math.cos(yaw)+z*math.sin(yaw);d=z*math.cos(yaw)-x*math.sin(yaw);h=y*math.cos(pitch)-d*math.sin(pitch);depth=y*math.sin(pitch)+d*math.cos(pitch);points.append((u,-h,depth))
span=max(max(p[i] for p in points)-min(p[i] for p in points) for i in (0,1));scale=520/max(span,1e-12)
projected=[(400+x*scale,330+y*scale,d) for x,y,d in points]
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="700" viewBox="0 0 800 700">','<rect width="800" height="700" fill="#111827"/>']
for f in sorted(faces,key=lambda f:sum(points[i][2] for i in f)/len(f)):
 xy=' '.join(f'{projected[i][0]:.3f},{projected[i][1]:.3f}' for i in f);svg.append(f'<polygon points="{xy}" fill="#6ca6bf" fill-opacity="0.36" stroke="#c0e7ed" stroke-width="1.2"/>')
svg+=['<text x="30" y="40" fill="#f3f4f6" font-family="sans-serif" font-size="22">Recovered asset geometry</text>',f'<text x="30" y="658" fill="#d1d5db" font-family="sans-serif" font-size="16">{len(vertices)} vertices · {len(faces)} triangles</text>','<text x="30" y="682" fill="#9ca3af" font-family="sans-serif" font-size="13">Diagnostic projection; no game materials, scene transforms or gameplay</text>','</svg>'];a.svg.write_text('\n'.join(svg)+'\n');print(a.svg)
