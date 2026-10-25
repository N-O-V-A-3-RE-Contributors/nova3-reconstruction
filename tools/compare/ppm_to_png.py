#!/usr/bin/env python3
"""Encode the source texture CLI's RGB8 PPM as PNG, without external packages."""
import argparse,pathlib,struct,zlib
p=argparse.ArgumentParser();p.add_argument('input',type=pathlib.Path);p.add_argument('output',type=pathlib.Path);args=p.parse_args()
magic,dims,maximum,rgb=args.input.read_bytes().split(b'\n',3);w,h=map(int,dims.split())
if magic!=b'P6' or maximum!=b'255' or not 0<w<=16384 or not 0<h<=16384 or len(rgb)!=w*h*3:raise ValueError('Expected source CLI RGB8 PPM')
def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data))
pixels=b''.join(b'\0'+rgb[y*w*3:(y+1)*w*3] for y in range(h))
args.output.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))
