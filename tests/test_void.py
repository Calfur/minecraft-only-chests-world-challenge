import unittest
from challenge import anvil as n
from challenge.nbt import Reader, block_at
from challenge.void import T, convert_chunk, packed, plain

class VoidTests(unittest.TestCase):
    def chunk(self, dimension, x=0,z=0):
        palette=[{'id':T(8,k)} for k in ('minecraft:stone','minecraft:chest','minecraft:end_portal_frame','minecraft:bedrock')]
        palette[1]['properties']=T(10,{'waterlogged':T(8,'true')})
        indices=[0]*4096
        indices[1]=1;indices[2]=2;indices[3]=3
        chest={'id':T(8,'minecraft:chest'),'x':T(3,x*16+1),'y':T(3,64),'z':T(3,z*16),
               'LootTable':T(8,'minecraft:chests/shipwreck_supply'),'LootTableSeed':T(4,6009839295362045710)}
        return T(10,{'xPos':T(3,x),'zPos':T(3,z),'Status':T(8,'minecraft:features'),
          'sections':T(9,(10,[{'Y':T(1,4),'block_states':T(10,{'palette':T(9,(10,palette)),'data':T(12,packed(indices,4))})}])),
          'block_entities':T(9,(10,[chest])), 'fluid_ticks':T(9,(10,[{'i':T(8,'minecraft:water')}])),
          'Heightmaps':T(10,{'WORLD_SURFACE':T(12,[0]*37)}),'blending_data':T(10,{})})

    def test_dimension_rules_and_original_loot(self):
        for dim in ('overworld','the_nether','the_end'):
            root=self.chunk(dim)
            old=plain(root)['block_entities']
            counts=convert_chunk(root,dim)
            result=plain(root)
            self.assertEqual(result['block_entities'],old)
            self.assertEqual(block_at(result,1,64,0),'minecraft:chest')
            self.assertEqual(block_at(result,0,64,0),'minecraft:air')
            self.assertEqual(block_at(result,2,64,0),'minecraft:end_portal_frame' if dim=='overworld' else 'minecraft:air')
            self.assertEqual(block_at(result,3,64,0),'minecraft:bedrock' if dim=='the_end' else 'minecraft:air')
            self.assertEqual(result['fluid_ticks'],[])
            self.assertEqual(result['Status'],'minecraft:full')
            self.assertNotIn('blending_data',result)
            self.assertEqual(result['sections'][0]['block_states']['palette'][1]['properties']['waterlogged'],'false')
            # Heightmap is X-fastest: retained chest in column X=1, Z=0.
            values=result['Heightmaps']['WORLD_SURFACE']
            self.assertEqual((values[0]>>9)&511,129 if dim=='overworld' else 65)

    def test_fountain_exception_does_not_keep_pillars(self):
        root=self.chunk('the_end',x=2,z=0)
        convert_chunk(root,'the_end')
        self.assertEqual(block_at(plain(root),35,64,0),'minecraft:air')

    def test_pending_chest_record_without_block_is_removed(self):
        root=self.chunk('overworld')
        root.value['block_entities'].value[1][0]['x']=T(3,7)
        convert_chunk(root,'overworld')
        self.assertEqual(plain(root)['block_entities'],[])

    def test_typed_codec_preserves_unicode_null_and_arrays(self):
        root=('',T(10,{'text':T(8,'hello\x00😀'),'array':T(12,[-1,1<<62]),'empty':T(11,[])}))
        encoded=n.write_nbt(root)
        self.assertEqual(n.write_nbt(n.read_nbt(encoded)),encoded)
        self.assertEqual(Reader(encoded).root()['text'],'hello\x00😀')

class PlayerTeleportTests(unittest.TestCase):
    def test_relative_rotation_and_position_are_acknowledged(self):
        import struct
        import threading
        from challenge.player import LocalPlayer, varint
        player=object.__new__(LocalPlayer)
        player.state='play';player.position=[10.,20.,30.];player.rotation=[45.,10.]
        player.error=None;player.ready=threading.Event();player.stopping=threading.Event()
        payload=varint(1)+struct.pack('>ddddddffI',1.,2.,3.,0.,0.,0.,5.,6.,31)
        def receive():
            player.stopping.set()
            return 73,payload
        sent=[]
        player._receive=receive
        player.send=lambda packet,payload=b'':sent.append(packet)
        player._pump()
        self.assertIsNone(player.error)
        self.assertEqual(player.position,[11.,22.,33.])
        self.assertEqual(player.rotation,[50.,16.])
        self.assertEqual(sent,[0,31,44])

if __name__=='__main__':unittest.main()
