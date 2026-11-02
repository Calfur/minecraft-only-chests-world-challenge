import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from challenge import anvil as n
from challenge.generation import install_void_generation
from challenge.nbt import read_file


class VoidGenerationTests(unittest.TestCase):
    def test_preserves_lookup_biomes_and_spawns_while_disabling_new_terrain(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);original=root/'original';world=root/'converted'
            definitions={}
            for dim,key in [('overworld','overworld'),('the_nether','nether'),('the_end','end')]:
                definitions['minecraft:'+dim]=n.Tag(10,{'generator':n.Tag(10,{
                    'type':n.Tag(8,'minecraft:noise'),'settings':n.Tag(8,'minecraft:'+key),
                    'biome_source':n.Tag(10,{'type':n.Tag(8,'minecraft:multi_noise'),'preset':n.Tag(8,'minecraft:'+key)})})})
            payload=gzip.compress(n.write_nbt(('',n.Tag(10,{'data':n.Tag(10,{
                'seed':n.Tag(4,-12345),'dimensions':n.Tag(10,definitions),'generate_features':n.Tag(1,1)})}))))
            for w in (original,world):
                p=w/'data/minecraft/world_gen_settings.dat';p.parent.mkdir(parents=True);p.write_bytes(payload)
            (world/'builds-and-player-data').write_bytes(b'unchanged')
            climate={'temperature':'minecraft:temperature','vegetation':'minecraft:vegetation','final_density':1.0}
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as jar:
                for key,miny in [('overworld',-64),('nether',0),('end',0)]:
                    jar.writestr('data/minecraft/worldgen/noise_settings/'+key+'.json',json.dumps({
                        'noise_router':climate,'noise':{'min_y':miny,'height':384},'sea_level':63,
                        'default_block':'minecraft:stone','default_fluid':'minecraft:water',
                        'aquifers':{},'material_rule':'minecraft:overworld','disable_mob_generation':False}))
                jar.writestr('data/minecraft/worldgen/biome/plains.json',json.dumps({
                    'features':[['tree']],'carvers':['cave'],'attributes':{'natural_spawns':'unchanged'}}))
            # Exercise the official bundle format as well as the noise data itself.
            bundle=root/'server.jar'
            with zipfile.ZipFile(bundle,'w') as jar:
                jar.writestr('META-INF/versions.list','hash\t26.3\t26.3/server.jar\n')
                jar.writestr('META-INF/versions/26.3/server.jar',buffer.getvalue())
            install_void_generation(world,original,bundle)
            current=read_file(world/'data/minecraft/world_gen_settings.dat')['data']
            self.assertEqual(current['seed'],-12345)
            self.assertEqual(current['generate_features'],0)
            self.assertEqual((original/'data/minecraft/world_gen_settings.dat').read_bytes(),payload)
            self.assertEqual((world/'builds-and-player-data').read_bytes(),b'unchanged')
            for dim in ('overworld','the_nether','the_end'):
                gen=current['dimensions']['minecraft:'+dim]['generator']
                old=read_file(original/'data/minecraft/world_gen_settings.dat')['data']['dimensions']['minecraft:'+dim]['generator']
                self.assertEqual(gen['type'],old['type'])
                self.assertEqual(gen['biome_source'],old['biome_source'])
                settings=json.loads((world/f'datapacks/chest_void/data/chest_void/worldgen/noise_settings/void_{dim}.json').read_text())
                self.assertEqual(settings['noise_router']['temperature'],climate['temperature'])
                self.assertEqual(settings['noise_router']['vegetation'],climate['vegetation'])
                self.assertEqual(settings['noise_router']['final_density'],-1.0)
                self.assertEqual(settings['default_fluid'],'minecraft:air')
                self.assertNotIn('aquifers',settings)
                self.assertLess(settings['sea_level'],settings['noise']['min_y'])
            biome=json.loads((world/'datapacks/chest_void/data/minecraft/worldgen/biome/plains.json').read_text())
            self.assertEqual(biome['features'],[])
            self.assertEqual(biome['carvers'],[])
            self.assertEqual(biome['attributes'],{'natural_spawns':'unchanged'})

if __name__=='__main__':unittest.main()
