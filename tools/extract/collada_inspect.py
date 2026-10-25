#!/usr/bin/env python3
"""Bounded Premium SCollada geometry research, based on native field accesses."""
import hashlib
import json
import struct
from pathlib import Path


def inspect(data):
    if len(data) < 60 or data[:4] != b"BRES":
        raise ValueError("BRES header required")
    def u(p):
        if p < 0 or p > len(data)-4:
            raise ValueError("field outside asset")
        return struct.unpack_from("<I",data,p)[0]
    def region(p,n):
        if p < 0 or n < 0 or p > len(data) or n > len(data)-p:
            raise ValueError("region outside asset")
        return data[p:p+n]
    root=u(0x20)
    geometries=[]
    count,array=u(root+0x84),u(root+0x88)
    region(array,count*16)
    for i in range(count):
        g=array+i*16
        tag,mesh=u(g+8),u(g+12)
        if tag != 0:
            geometries.append({"offset":g,"tag":tag,"scope":"unsupported geometry variant"})
            continue
        vertex_count=u(mesh+4)
        attrs=[]
        counts=[u(mesh+x) for x in (8,16,24,32)]
        if len(set(counts)) != 1:
            raise ValueError("attribute vector count mismatch")
        for a in range(counts[0]):
            attrs.append({k:u(u(mesh+offset)+a*4) for k,offset in
                          [("stride",12),("offset",20),("type",28),("components",36)]})
        buffers=[]
        for j in range(u(mesh+0x38)):
            b=u(mesh+0x3c)+j*56
            index_count=u(b+0x28)
            # Tail-buffer +4 rule supported by native split relocation 0x69cc84.
            index_payload=u(b+0x2c)+4
            width=2 if u(b+0x24)-u(b+0x20) <= 0xfffe else 4
            raw_indices=region(index_payload,index_count*width)
            indices=struct.unpack("<"+("H" if width==2 else "I")*index_count,raw_indices)
            if any(v>=vertex_count for v in indices):
                raise ValueError("index outside vertex count")
            buffers.append({"offset":b,"raw_primitive_type":u(b),"min_vertex":u(b+0x20),
                            "max_vertex":u(b+0x24),"index_count":index_count,"index_width":width,
                            "index_payload":index_payload,"indices_sha256":hashlib.sha256(raw_indices).hexdigest()})
        vertex_payload=u(mesh+0x30)+4
        vertex_bytes=region(vertex_payload,vertex_count*attrs[0]["stride"])
        bbox=struct.unpack("<6f",region(mesh+0x40,24))
        geometries.append({"offset":g,"tag":tag,"mesh":mesh,"vertex_count":vertex_count,
                           "attributes":attrs,"vertex_payload":vertex_payload,
                           "vertices_sha256":hashlib.sha256(vertex_bytes).hexdigest(),
                           "bbox":bbox,"buffers":buffers})
    return {"asset_sha256":hashlib.sha256(data).hexdigest(),"root":root,
            "geometry_count":count,"geometry_array":array,"geometries":geometries,
            "scope":"research parser, inline geometry tag0 and split-tail +4 convention; no general runtime claimed"}


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser();p.add_argument("asset",type=Path);a=p.parse_args()
    print(json.dumps(inspect(a.asset.read_bytes()),indent=2))
