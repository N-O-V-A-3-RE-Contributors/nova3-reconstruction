#!/usr/bin/env python3
"""Record structurally observed Freemium OBB fields; no name decoding claims."""
import json,pathlib,struct
ROOT=pathlib.Path(__file__).resolve().parents[2]

def inspect(prefix,total_size):
    if len(prefix)<16: raise ValueError("short header")
    unknown,data_start,names_start,count=struct.unpack_from(">4I",prefix)
    if count>4096 or names_start!=16+count*16 or not names_start<=data_start<=len(prefix):
        raise ValueError("invalid/unsupported table bounds")
    entries=[];expected=data_start
    for i in range(count):
        offset,size,name_offset,unknown_entry=struct.unpack_from(">4I",prefix,16+i*16)
        if offset!=expected or offset+size>total_size: raise ValueError("non-contiguous/out-of-bounds data")
        if name_offset>=data_start-names_start: raise ValueError("name offset outside names blob")
        entries.append(dict(index=i,data_offset=offset,size=size,name_blob_offset=name_offset,unknown_u32=unknown_entry))
        expected=offset+size
    if expected!=total_size: raise ValueError("entries do not cover OBB data")
    return dict(state="INVENTORIED",format="INFERRED big-endian table, unknown signature/version",header_unknown_u32=unknown,data_start=data_start,names_start=names_start,entry_count=count,all_payloads_contiguous=True,exact_file_coverage=True,names_status="nonplaintext bytes; decoding unknown",entries=entries)

if __name__=="__main__":
    inventory=json.loads((ROOT/"research/assets/archive_inventory.json").read_text())
    freemium=next(x for x in inventory if x["edition"]=="freemium")
    for member in freemium["members"]:
        p=ROOT/"research/assets"/("freemium_"+pathlib.PurePosixPath(member["name"]).name+".prefix")
        result=inspect(p.read_bytes(),member["size"])
        result["prefix_sha256"]=member["prefix_sha256"]
        p.with_suffix(".table.json").write_text(json.dumps(result,indent=2)+"\n")
        print(p.name,result["entry_count"],"entries; exact coverage",member["size"])
