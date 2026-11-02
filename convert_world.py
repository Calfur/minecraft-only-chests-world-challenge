#!/usr/bin/env python3
"""Convert a stopped, pregenerated 26.3 save into a separate chest-only void save."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from challenge import anvil as n
from challenge.generation import install_void_generation
from challenge.nbt import world_info, chests_near_spawn
from challenge.server import atomic_json, add_runtime_arguments, Server, find_java, ensure_server
from challenge.void import T, val, get, put, plain, name, convert_chunk


def chest_data(root):
    return sorted([plain(T(10,e)) for e in val(root.value,'block_entities',(10,[]))[1]
                   if val(e,'id') in {'minecraft:chest','minecraft:trapped_chest'}],
                  key=lambda e:(e['x'],e['y'],e['z']))


def convert_region(task):
    path,dimension=task
    records,timestamps=n.raw_chunks(path)
    blocks=Counter();chests=0;digest=hashlib.sha256();discarded=0
    for _,root in records.values():
        before=chest_data(root)
        blocks.update(convert_chunk(root,dimension))
        after=chest_data(root)
        # Incomplete halo chunks can contain pending records with no actual chest.
        originals={(e['x'],e['y'],e['z']):e for e in before}
        for e in after:
            if originals.get((e['x'],e['y'],e['z']))!=e:
                raise RuntimeError(f'Chest NBT changed in {path}')
        discarded+=len(before)-len(after)
        chests+=len(after)
        digest.update(json.dumps(after,sort_keys=True).encode())
    n.write_region(path,records,timestamps)
    # Read back the written payload, checking the codec and all original chest NBT.
    saved,_=n.raw_chunks(path)
    check=hashlib.sha256()
    for _,root in saved.values():
        for section in val(root.value,'sections',(10,[]))[1]:
            bs=section.get('block_states')
            if bs:
                for state in val(bs.value,'palette')[1]:
                    if name(state) not in {'minecraft:air','minecraft:chest','minecraft:trapped_chest',
                                          'minecraft:end_portal_frame','minecraft:end_portal',
                                          'minecraft:end_gateway','minecraft:bedrock','minecraft:torch','minecraft:wall_torch'}:
                        raise RuntimeError(f'Terrain survived in {path}')
        check.update(json.dumps(chest_data(root),sort_keys=True).encode())
    if len(saved)!=len(records) or check.digest()!=digest.digest():
        raise RuntimeError(f'Region verification failed: {path}')
    return {'dimension':dimension,'chunks':len(records),'chests':chests,'blocks':dict(blocks),
            'discarded_pending_records':discarded,'file':str(path)}


def convert_entities(world):
    counts=Counter();crystals=[]
    for dim in (world/'dimensions/minecraft').iterdir():
        for region in sorted((dim/'entities').glob('*.mca')):
            records,timestamps=n.raw_chunks(region)
            for _,root in records.values():
                keep=[]
                for e in val(root.value,'Entities',(10,[]))[1]:
                    kind=val(e,'id')
                    if dim.name=='the_end' and kind in {'minecraft:end_crystal','minecraft:ender_dragon','minecraft:shulker'}:
                        keep.append(e)
                        if kind=='minecraft:end_crystal':crystals.append(val(e,'Pos')[1])
                    elif kind=='minecraft:item_frame' and 'Item' in e and val(e['Item'].value,'id')=='minecraft:elytra':
                        e['Fixed']=T(1,1);keep.append(e)
                root.value['Entities']=T(9,(10,keep))
                counts.update(val(e,'id') for e in keep)
            n.write_region(region,records,timestamps)
        if (dim/'poi').exists():shutil.rmtree(dim/'poi')
        for p in dim.glob('data/**/*chunk_tickets.dat'):
            r=get(p);r[1].value['data']=T(10,{});put(p,r)
    if len(crystals)!=10 or counts['minecraft:ender_dragon']!=1:
        raise RuntimeError(f'Initialize the natural dragon on an isolated source copy first: {dict(counts)}')
    return counts,crystals


def configure(world,report,crystals,display_name,server_jar=None):
    install_void_generation(world,world,server_jar or Path('.cache/server-26.3.jar'))
    x,y,z=report['bonus_chest']['position']
    p=world/'level.dat';r=get(p);d=r[1].value['Data'].value
    d['LevelName']=T(8,display_name);d['GameType']=T(3,0);d['allowCommands']=T(1,1)
    d['LastPlayed']=T(4,int(time.time()*1000));d.pop('singleplayer_uuid',None);d.pop('Player',None)
    d['spawn']=T(10,{'pos':T(11,[x,y+1,z]),'dimension':T(8,'minecraft:overworld'),'yaw':T(5,0),'pitch':T(5,0)})
    enabled=d['DataPacks'].value['Enabled'].value[1]
    if 'file/chest_void' not in enabled:enabled.append('file/chest_void')
    put(p,r)
    rules=world/'data/minecraft/game_rules.dat';r=get(rules)
    r[1].value['data'].value['minecraft:respawn_radius']=T(3,0);put(rules,r)
    # Remove only the generated copy's disposable test-player data.
    if (world/'players').exists():shutil.rmtree(world/'players')
    (world/'level.dat_old').unlink(missing_ok=True)
    (world/'session.lock').unlink(missing_ok=True)
    base=world/'datapacks/chest_void'
    def write(path,text):
        p=base/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    write('pack.mcmeta',json.dumps({'pack':{'min_format':[121,0],'max_format':[121,0],
          'description':'Chest-only void: safe starting-chest spawn and End arrival'}}))
    write('data/minecraft/tags/function/load.json',json.dumps({'values':['chest_void:load']}))
    write('data/minecraft/tags/function/tick.json',json.dumps({'values':['chest_void:tick']}))
    write('data/chest_void/function/load.mcfunction','scoreboard objectives add cv_deaths deathCount\n')
    write('data/chest_void/function/join.mcfunction',
          f'execute in minecraft:overworld run tp @s {x+.5} {y+.875} {z+.5}\n'
          f'spawnpoint @s {x} {y+1} {z}\ntag @s add cv_joined\nscoreboard players set @s cv_deaths 0\n')
    write('data/chest_void/function/enter_end.mcfunction',
          'tp @s 0.5 66 0.5\nfill 98 48 -2 102 48 2 minecraft:air replace minecraft:obsidian\ntag @s add cv_in_end\n')
    tick=['execute as @a[tag=!cv_joined] run function chest_void:join',
          'execute as @a[scores={cv_deaths=1..},nbt=!{Health:0.0f}] run function chest_void:join',
          'execute as @a[nbt=!{Dimension:"minecraft:the_end"}] run tag @s remove cv_in_end',
          'execute in minecraft:the_end as @a[nbt={Dimension:"minecraft:the_end"},tag=!cv_in_end] run function chest_void:enter_end']
    write('data/chest_void/function/tick.mcfunction','\n'.join(tick)+'\n')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(p)
    p.add_argument('--initialize-end',action='store_true',help='Initialize the natural dragon on the output copy before conversion')
    p.add_argument('--world',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True,help='New output directory; source stays untouched')
    p.add_argument('--report',type=Path,required=True,help='Seed finder result.json with original bonus chest position')
    p.add_argument('--name',default='Only Chests - 4k Void')
    p.add_argument('--workers',type=int,default=6)
    args=p.parse_args(argv)
    if not 1<=args.workers<=12:p.error('workers must be 1..12')
    source=args.world.expanduser().resolve();world_info(source)
    output=args.output.expanduser().resolve()
    if source==output or source in output.parents or output in source.parents:
        p.error('Source and output must be separate directories')
    if output.exists():p.error('Output already exists; refusing to overwrite')
    lock=(source/'session.lock').open('rb') if (source/'session.lock').exists() else None
    if lock:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    report=json.loads(args.report.read_text())
    positions=[tuple(report[k]['position']) for k in ('bonus_chest','selected_chest')]
    original=chests_near_spawn(source,report['spawn'],96,positions)
    if len(original)!=2:raise RuntimeError('Missing starter chests')
    output.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['/bin/cp','-cR',str(source),str(output)],check=True)
    if args.initialize_end:
        with Server(ensure_server(args.server_jar,Path('.cache')),find_java(args.java),
                    output.parent/(output.name+'-runtime'),output,heap=args.heap,
                    accept_eula=args.accept_eula,player_view_distance=4) as server:
            server.run('execute in minecraft:the_end run tp SeedInspector 0 85 0 0 0')
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                lines=server.run('execute in minecraft:the_end if entity @e[type=minecraft:ender_dragon] run say CV_DRAGON_READY')
                if any(l.endswith('[Server] CV_DRAGON_READY') for l in lines):break
                time.sleep(.2)
            else:raise RuntimeError('Natural dragon initialization failed')
            server.save()
    began=time.monotonic();entities,crystals=convert_entities(output)
    tasks=[(p,d.name) for d in sorted((output/'dimensions/minecraft').iterdir())
           for p in sorted((d/'region').glob('*.mca'))]
    summary={d:{'chunks':0,'chests':0,'blocks':Counter(),'discarded_pending_records':0}
             for d in ('overworld','the_nether','the_end')}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i,result in enumerate(pool.map(convert_region,tasks),1):
            state=summary[result['dimension']]
            for k in ('chunks','chests','discarded_pending_records'):state[k]+=result[k]
            state['blocks'].update(result['blocks'])
            if i%16==0 or i==len(tasks):
                print(f'Regions {i}/{len(tasks)}; {time.monotonic()-began:.1f}s',flush=True)
    if summary['overworld']['blocks']['minecraft:end_portal_frame']<12:
        raise RuntimeError('Missing natural End portal frames')
    if not summary['the_end']['blocks']['minecraft:bedrock']:
        raise RuntimeError('Missing End fountain')
    if not summary['the_nether']['chests'] or not summary['the_end']['chests']:
        raise RuntimeError('Missing Nether or End chests')
    configure(output,report,crystals,args.name,server_jar=args.server_jar)
    if chests_near_spawn(output,report['spawn'],96,positions)!=original:
        raise RuntimeError('Starter chest NBT changed')
    receipt={'complete':True,'source':str(source),'output':str(output),'dimensions':summary,
             'entities':dict(entities),'crystals':crystals,'starter_chests_unchanged':True,
             'elapsed_seconds':round(time.monotonic()-began,3),
             'new_chunks':'empty in all dimensions; natural loot exists only in pregenerated areas'}
    atomic_json(output.parent/(output.name+'-conversion.json'),receipt)
    print(json.dumps(receipt,indent=2),flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
