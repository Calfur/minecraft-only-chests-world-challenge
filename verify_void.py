#!/usr/bin/env python3
"""Bounded real-vanilla checks on a disposable copy of a converted 26.3 save."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import time

from challenge.nbt import chests_near_spawn, read_file, region_chunks
from challenge.player import LocalPlayer, varint
from challenge.server import Server, find_java, atomic_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--world',type=Path,required=True)
    p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    work=args.work_dir.resolve();work.mkdir(parents=True,exist_ok=True)
    world=args.world.resolve();check=work/'runtime-test-world'
    if check.exists():raise FileExistsError(check)
    r=json.loads(args.report.read_text())
    positions=[tuple(r[k]['position']) for k in ('bonus_chest','selected_chest')]
    original=chests_near_spawn(world,r['spawn'],96,positions)
    assert len(original)==2
    subprocess.run(['/bin/cp','-cR',str(world),str(check)],check=True)
    # The probe acknowledges server teleports but sends no extra movement packet.
    send=LocalPlayer.send
    def stationary(self,packet_id,payload=b''):
        if self.state=='play' and packet_id==31:return
        return send(self,packet_id,payload)
    LocalPlayer.send=stationary
    proofs=[]
    with Server(Path('.cache/server-26.3.jar').resolve(),find_java(),work/'runtime',check,
                heap='4G',accept_eula=True,player_view_distance=4) as s:
        def run(*commands):
            lines=s.run(*commands)
            for line in lines:
                if any(word in line.lower() for word in ('unknown function','error executing','failed to load')):
                    raise RuntimeError(line)
            return lines
        def expect(command,marker,timeout=10):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                lines=run(command+' run say '+marker)
                if any(l.endswith('[Server] '+marker) for l in lines):
                    proofs.append(marker);print(marker,flush=True);return
                time.sleep(.2)
            raise RuntimeError('Check timed out: '+marker)
        x,y,z=r['bonus_chest']['position']
        expect(f'execute in minecraft:overworld if block {x} {y} {z} minecraft:chest if block {x} {y-1} {z} minecraft:air','VOID_START')
        expect(f'execute if entity @a[name=SeedInspector,x={x},y={y+.8},z={z},dx=1,dy=1,dz=1]','CHEST_TOP_SPAWN')
        run('execute in minecraft:the_end run tp SeedInspector 0 85 0 0 0')
        s.force_batch('minecraft:the_end',(-4,-4,4,4))
        expect('execute in minecraft:the_end if entity @a[name=SeedInspector,x=0,y=66,z=0,dx=1,dy=1,dz=1]','SAFE_END_ARRIVAL')
        expect('execute in minecraft:the_end if entity @e[type=minecraft:ender_dragon] if block 0 65 0 minecraft:bedrock if block 0 60 0 minecraft:air','LIVE_DRAGON_AND_FOUNTAIN')
        expect('execute in minecraft:the_end if block 42 90 0 minecraft:air','NO_ORIGINAL_PILLARS')
        # Prove future chunks stay void in each dimension, beyond the prepared 4k square.
        for dimension in ('overworld','the_nether','the_end'):
            s.force_batch('minecraft:'+dimension,(400,400,400,400))
            expect(f'execute in minecraft:{dimension} if block 6400 60 6400 minecraft:air','FUTURE_VOID_'+dimension)
        s.force_batch('minecraft:the_end',(-4,-4,4,4))
        run('execute in minecraft:the_end run kill @e[type=minecraft:ender_dragon]')
        expect('execute in minecraft:the_end if block 1 62 0 minecraft:end_portal unless entity @e[type=minecraft:ender_dragon]','DRAGON_KILL_EXIT_PORTAL',timeout=30)
        run('execute in minecraft:overworld run tp SeedInspector 132.5 67 54.5')
        s.save()
    # The runtime test must never unpack the natural starter loot.
    assert chests_near_spawn(check,r['spawn'],96,positions)==original
    assert chests_near_spawn(world,r['spawn'],96,positions)==original
    for dimension in ('overworld','the_nether','the_end'):
        path=check/f'dimensions/minecraft/{dimension}/region/r.12.12.mca'
        chunks=list(region_chunks(path,{(400,400)}));assert len(chunks)==1
        from challenge.nbt import state_name
        assert all(state_name(e)=='minecraft:air' for section in chunks[0].get('sections',[])
                   for e in section.get('block_states',{}).get('palette',[]))
    atomic_json(work/'verification.json',{'passed':True,'checks':proofs,'starter_chests_unchanged':True,
                                        'dragon_kill_tested':True,'dragon_respawn_out_of_scope':True})
    print('ALL CHECKS PASSED',flush=True)

if __name__=='__main__':main()
