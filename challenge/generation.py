"""Keep vanilla biome/structure lookup while generating empty future chunks."""
import copy
import contextlib
import io
import json
from pathlib import Path
import zipfile
from . import anvil as n
from .void import T, get, put


def install_void_generation(world, original_world, server_jar):
    world=Path(world);original_world=Path(original_world)
    original=get(original_world/'data/minecraft/world_gen_settings.dat')
    current=get(world/'data/minecraft/world_gen_settings.dat')
    settings=current[1].value['data'].value
    definitions=copy.deepcopy(original[1].value['data'].value['dimensions'])
    pack=world/'datapacks/chest_void'
    def write(path,value):
        path=pack/path;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,indent=2)+'\n')
    with contextlib.ExitStack() as stack:
        jar=stack.enter_context(zipfile.ZipFile(server_jar))
        if 'META-INF/versions.list' in jar.namelist():
            record=next(line.split('\t') for line in jar.read('META-INF/versions.list').decode().splitlines() if line.split('\t')[1]=='26.3')
            jar=stack.enter_context(zipfile.ZipFile(io.BytesIO(jar.read('META-INF/versions/'+record[2]))))
        for dimension,definition in definitions.value.items():
            generator=definition.value['generator'].value
            if generator['type'].value!='minecraft:noise':
                raise ValueError('Original world must use vanilla noise generators')
            vanilla=generator['settings'].value
            if not isinstance(vanilla,str) or not vanilla.startswith('minecraft:'):
                raise ValueError('Expected an unmodified vanilla noise settings identifier')
            noise=json.loads(jar.read('data/minecraft/worldgen/noise_settings/'+vanilla.split(':')[1]+'.json'))
            noise['noise_router']['final_density']=-1.0
            noise['default_block']='minecraft:air'
            noise['default_fluid']='minecraft:air'
            # Vanilla's global fluid picker supplies lava below min(-54, sea_level),
            # even without aquifers. Put that cutoff below this dimension's floor.
            noise['sea_level']=noise['noise']['min_y']-1
            noise['material_rule']={'type':'minecraft:block','result_state':'minecraft:air'}
            noise.pop('aquifers',None)
            noise['disable_mob_generation']=True  # Only world-generation mob placement.
            key='void_'+dimension.split(':')[1]
            write(Path('data/chest_void/worldgen/noise_settings')/(key+'.json'),noise)
            generator['settings']=T(8,'chest_void:'+key)
        # Retain original biome identities, climate routing, and natural spawn settings.
        # Only carving and decoration are removed from future chunks.
        for path in jar.namelist():
            if path.startswith('data/minecraft/worldgen/biome/') and path.endswith('.json'):
                biome=json.loads(jar.read(path));biome['features']=[];biome['carvers']=[]
                write(Path(path),biome)
    settings['dimensions']=definitions
    settings['generate_features']=T(1,0)  # Prevent new structures; existing starts/references stay usable.
    settings['bonus_chest']=T(1,0)
    put(world/'data/minecraft/world_gen_settings.dat',current)
