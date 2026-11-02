#!/usr/bin/env python3
"""Repair a closed converted save's structure lookup without editing chunks/player data."""
import argparse
import fcntl
from pathlib import Path
import shutil
from challenge.generation import install_void_generation
from challenge.nbt import world_info
from challenge.server import atomic_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--world',required=True,type=Path)
    p.add_argument('--original',required=True,type=Path)
    p.add_argument('--backup',required=True,type=Path,help='New backup directory for previous settings and datapack')
    p.add_argument('--server-jar',type=Path,default=Path('.cache/server-26.3.jar'))
    args=p.parse_args();world=args.world.expanduser().resolve();original=args.original.expanduser().resolve()
    if world==original:raise ValueError('Original must be separate from the converted world')
    if world_info(world)['seed']!=world_info(original)['seed']:raise ValueError('World seeds differ')
    # Java session locks use POSIX record locks. Do not patch an open game.
    lock=None
    if (world/'session.lock').exists():
        lock=(world/'session.lock').open('r+b')
        fcntl.lockf(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.backup.exists():raise FileExistsError(args.backup)
    args.backup.mkdir(parents=True)
    relative=Path('data/minecraft/world_gen_settings.dat')
    (args.backup/relative.parent).mkdir(parents=True)
    shutil.copy2(world/relative,args.backup/relative)
    shutil.copytree(world/'datapacks/chest_void',args.backup/'datapacks/chest_void')
    try:
        install_void_generation(world,original,args.server_jar)
    except BaseException:
        shutil.copy2(args.backup/relative,world/relative)
        shutil.rmtree(world/'datapacks/chest_void')
        shutil.copytree(args.backup/'datapacks/chest_void',world/'datapacks/chest_void')
        raise
    atomic_json(args.backup/'receipt.json',{'world':str(world),'original':str(original),
                'changed':['data/minecraft/world_gen_settings.dat','datapacks/chest_void'],
                'chunks_and_player_data_untouched':True})
    print('Repaired void generation and structure lookup:',world)

if __name__=='__main__':main()
