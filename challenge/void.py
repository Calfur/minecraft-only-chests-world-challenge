"""Chest-only block conversion; original container NBT is retained verbatim."""
import collections
import gzip
import math
from . import anvil as n
from .nbt import Reader

T=n.Tag
KEEP={'minecraft:chest','minecraft:trapped_chest','minecraft:end_portal_frame',
      'minecraft:end_portal','minecraft:end_gateway'}

def val(d,k,default=None):return d[k].value if k in d else default

def packed(values,bits):
    per=64//bits
    words=[0]*math.ceil(len(values)/per)
    for i,v in enumerate(values):words[i//per]|=v<<((i%per)*bits)
    return [v if v<1<<63 else v-(1<<64) for v in words]

def unpack(bs):
    palette=val(bs,'palette')[1]
    if len(palette)==1:return [0]*4096,palette
    bits=max(4,(len(palette)-1).bit_length());per=64//bits
    longs=val(bs,'data')
    return [((longs[i//per]&((1<<64)-1))>>((i%per)*bits))&((1<<bits)-1) for i in range(4096)],palette

def name(state):
    if isinstance(state,str):return state
    return val(state,'id',val(state,'',val(state,'Name','minecraft:air')))

def get(path):return n.read_nbt(gzip.decompress(path.read_bytes()))
def put(path,root):path.write_bytes(gzip.compress(n.write_nbt(root)))
def plain(tag):return Reader(n.write_nbt(('',tag))).root()

def convert_chunk(tag,dimension):
    c=tag.value
    cx,cz=val(c,'xPos'),val(c,'zPos')
    heights=[0]*256; retained=set();counts=collections.Counter()
    allowed_blocks = {'minecraft:chest', 'minecraft:trapped_chest'}
    if dimension=='overworld':allowed_blocks |= {'minecraft:end_portal_frame','minecraft:end_portal'}
    if dimension=='the_end':allowed_blocks |= {'minecraft:end_portal','minecraft:end_gateway'}
    sections=val(c,'sections',(10,[]))[1]
    # Vanilla End dimension is 0..255; Overworld and Nether both store their full section range.
    min_y=-64 if dimension=='overworld' else 0
    height_bits=9
    for section in sections:
        bs=section.get('block_states')
        if not bs:continue
        sy=val(section,'Y')
        palette=val(bs.value,'palette')[1]
        names=[name(s) for s in palette]
        fountain_chunk=dimension=='the_end' and cx in [-1,0] and cz in [-1,0]
        allowed=allowed_blocks | ({'minecraft:bedrock','minecraft:wall_torch','minecraft:torch'} if fountain_chunk else set())
        if not any(block in allowed for block in names):
            section['block_states']=T(10,{'palette':T(9,(10,[{'id':T(8,'minecraft:air')}]))})
            section.pop('BlockLight',None);section.pop('SkyLight',None)
            continue
        indices,palette=unpack(bs.value)
        out=[0]*4096
        outpal=['minecraft:air'];lookup={}
        for i,old in enumerate(indices):
            block=names[old]
            x=cx*16+(i&15);y=sy*16+(i>>8);z=cz*16+((i>>4)&15)
            fountain=dimension=='the_end' and block in {'minecraft:bedrock','minecraft:wall_torch','minecraft:torch'} and abs(x)<=4 and abs(z)<=4 and 50<=y<=80
            if block not in allowed_blocks and not fountain:continue
            if old not in lookup:
                entry=palette[old]
                if not isinstance(entry,str):
                    props=entry.get('properties') or entry.get('Properties')
                    if props and 'waterlogged' in props.value:props.value['waterlogged']=T(8,'false')
                lookup[old]=len(outpal);outpal.append(entry)
            out[i]=lookup[old]
            heights[i&255]=max(heights[i&255],y+1-min_y)
            retained.add((x,y,z));counts[block]+=1
        # 26.3 accepts mixed palette representations only through its compound format.
        entries=[{'id':T(8,s)} if isinstance(s,str) else s for s in outpal]
        states={'palette':T(9,(10,entries))}
        if len(outpal)>1:states['data']=T(12,packed(out,max(4,(len(outpal)-1).bit_length())))
        section['block_states']=T(10,states)
        section.pop('BlockLight',None);section.pop('SkyLight',None)
    entities=[]
    for e in val(c,'block_entities',(10,[]))[1]:
        if (val(e,'x'),val(e,'y'),val(e,'z')) in retained and val(e,'id') in {'minecraft:chest','minecraft:trapped_chest','minecraft:end_gateway','minecraft:end_portal'}:
            entities.append(e)
    c['block_entities']=T(9,(10,entities))
    c['block_ticks']=T(9,(10,[]));c['fluid_ticks']=T(9,(10,[]))
    c['PostProcessing']=T(9,(9,[(0,[]) for _ in sections]))
    c['Status']=T(8,'minecraft:full');c['isLightOn']=T(1,0)
    # Clear pending worldgen/retrogen; fully saved chest and portal data above is retained.
    for key in ['blending_data','below_zero_retrogen','UpgradeData','CarvingMasks']:
        c.pop(key,None)
    if 'Heightmaps' in c:
        for key in c['Heightmaps'].value:
            c['Heightmaps'].value[key]=T(12,packed(heights,height_bits))
    return counts
