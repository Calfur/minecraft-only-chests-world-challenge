import gzip
import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from challenge.nbt import Reader, block_at, inventory, slab_budget
from challenge.server import batches, chunk_bounds
from find_seed import BONUS, SUPPLY, inspect_candidate


class ChunkPlanTests(unittest.TestCase):
    def test_original_large_plan_count(self):
        bounds = chunk_bounds(0, 0, 2304)
        plan = list(batches(bounds))
        self.assertEqual(len(plan), 324)
        self.assertEqual(sum((x1 - x0 + 1) * (z1 - z0 + 1) for x0, z0, x1, z1 in plan), 82944)

    def test_unaligned_negative_center_has_no_gaps_or_overlap(self):
        bounds = chunk_bounds(-17, 5, 40)
        seen = []
        for x0, z0, x1, z1 in batches(bounds, 3):
            self.assertLessEqual((x1 - x0 + 1) * (z1 - z0 + 1), 9)
            seen.extend((x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1))
        expected = {(x, z) for x in range(-4, 2) for z in range(-3, 3)}
        self.assertEqual(set(seen), expected)
        self.assertEqual(len(seen), len(expected))

    def test_batch_limit(self):
        with self.assertRaises(ValueError):
            list(batches((0, 0, 20, 20), 17))


class NativeFormatTests(unittest.TestCase):
    def test_nbt_mixed_palette_string_wrapper(self):
        # Compound root containing a list of compound-wrapped short-form
        # strings, the representation used by mixed 26.3 block-state palettes.
        def string(value):
            data = value.encode()
            return struct.pack(">H", len(data)) + data
        payload = b"\x0a\x00\x00\x09" + string("palette") + b"\x0a\x00\x00\x00\x01"
        payload += b"\x08\x00\x00" + string("minecraft:air") + b"\x00\x00"
        self.assertEqual(Reader(payload).root(), {"palette": [{"": "minecraft:air"}]})

    def test_actual_block_check_handles_negative_positions_and_packed_data(self):
        longs = [0] * 256
        index = (15 << 8) | (3 << 4) | 15
        longs[index // 16] = 1 << ((index % 16) * 4)
        chunk = {"xPos": -1, "zPos": 2, "sections": [{"Y": -1, "block_states": {
            "palette": [{"": "minecraft:air"}, {"id": "minecraft:chest", "properties": {"waterlogged": "true"}}],
            "data": longs}}]}
        self.assertEqual(block_at(chunk, -1, -1, 35), "minecraft:chest")
        self.assertEqual(block_at(chunk, -2, -1, 35), "minecraft:air")
        self.assertIsNone(block_at(chunk, 15, -1, 35))

    def test_truncated_nbt_is_rejected(self):
        with self.assertRaises(ValueError):
            Reader(b"\x0a\x00\x00\x08\x00\x01x\x00\x05ab").root()


class ReachabilityTests(unittest.TestCase):
    def test_observed_vanilla_bonus_roll_budget(self):
        # Actual naturally generated bonus chest from the 57352 smoke test.
        self.assertEqual(slab_budget({"minecraft:acacia_log": 3, "minecraft:birch_log": 6}), 60)

    def test_mixed_wood_table_and_slab_rounding(self):
        self.assertEqual(slab_budget({"minecraft:oak_planks": 2, "minecraft:birch_planks": 2}), 0)
        self.assertEqual(slab_budget({"minecraft:oak_log": 1}), 0)
        self.assertEqual(slab_budget({"minecraft:oak_planks": 2, "minecraft:birch_planks": 8}), 12)

    def test_inventory_aggregates_split_stacks(self):
        chest = {"Items": [{"id": "minecraft:bamboo", "count": 1}, {"id": "minecraft:bamboo", "count": 2},
                           {"id": "minecraft:moss_block", "count": 3}]}
        self.assertEqual(inventory(chest), {"minecraft:bamboo": 3, "minecraft:moss_block": 3})


class NaturalSelectionTests(unittest.TestCase):
    def candidate(self, wood_count):
        bonus = {"id": "minecraft:chest", "x": 1, "y": 65, "z": 0,
                 "LootTable": BONUS, "LootTableSeed": -77}
        supply = {"id": "minecraft:chest", "x": 6, "y": 53, "z": 0,
                  "LootTable": SUPPLY, "LootTableSeed": 456}
        original = [bonus, supply]
        opened = [
            {k: v for k, v in bonus.items() if k not in ("LootTable", "LootTableSeed")} | {
                "Items": [{"id": "minecraft:oak_planks", "count": wood_count}]},
            {k: v for k, v in supply.items() if k not in ("LootTable", "LootTableSeed")} | {
                "Items": [{"id": "minecraft:moss_block", "count": 1},
                          {"id": "minecraft:bamboo", "count": 2}]},
        ]
        class FakeServer:
            def __init__(self): self.commands = []
            def save(self): pass
            def force_batch(self, *args): pass
            def force_chunks(self, *args): pass
            def run(self, *commands):
                self.commands.extend(commands)
                if any("locate structure" in c for c in commands):
                    return ["The nearest #minecraft:shipwreck (minecraft:shipwreck_beached) is at [6, ~, 0] (6 blocks away)"]
                return []
        server = FakeServer()
        with tempfile.TemporaryDirectory() as folder:
            world = Path(folder) / "attempt/worlds/seed-10"
            world.mkdir(parents=True)
            (world / "pristine.bin").write_bytes(b"unchanged-source")
            with patch("find_seed.world_info", return_value={"seed": 10, "spawn": [0, 64, 0]}), \
                    patch("find_seed.chests_near_spawn", side_effect=[original, opened, original]):
                report, snapshot = inspect_candidate(server, world, 96, False)
            self.assertEqual((snapshot / "pristine.bin").read_bytes(), b"unchanged-source")
        self.assertEqual(bonus["LootTableSeed"], -77)
        self.assertEqual(supply["LootTableSeed"], 456)
        self.assertFalse(any("LootTableSeed:" in c or "setblock " in c or "data merge " in c for c in server.commands))
        return report

    def test_match_publishes_original_seed_and_original_unopened_snapshot(self):
        report = self.candidate(45)
        self.assertTrue(report["accepted"])
        self.assertEqual(report["selected_chest"]["loot_seed"], 456)
        self.assertEqual(report["planned_starter_position"], [0, 53, 0])
        self.assertTrue(report["natural_loot_seeds_preserved"])

    def test_required_items_do_not_override_insufficient_natural_bridge_materials(self):
        report = self.candidate(4)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["reason"], "insufficient_natural_starter_wood")


if __name__ == "__main__":
    unittest.main()
