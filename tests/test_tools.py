import gzip
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from challenge.nbt import Reader, block_at, inventory, region_chunks, slab_budget
from challenge.server import batches, chunk_bounds
from find_seed import (BONUS, SUPPLY, inspect_candidate, inspect_filtered_candidate,
                       predicted_reachable)


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

    def test_region_filter_uses_absolute_negative_chunk_coordinates(self):
        header = bytearray(8192)
        sectors = []
        for sector, (slot, x, z) in enumerate(((0, -32, -32), (1023, -1, -1)), 2):
            header[slot * 4:slot * 4 + 4] = ((sector << 8) | 1).to_bytes(4, "big")
            nbt = b"\x0a\0\0\x03\0\x04xPos" + struct.pack(">i", x)
            nbt += b"\x03\0\x04zPos" + struct.pack(">i", z) + b"\0"
            payload = b"\x02" + zlib.compress(nbt)
            sectors.append((struct.pack(">I", len(payload)) + payload).ljust(4096, b"\0"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "r.-1.-1.mca"
            path.write_bytes(header + b"".join(sectors))
            all_chunks = list(region_chunks(path))
            self.assertEqual(all_chunks, [{"xPos": -32, "zPos": -32}, {"xPos": -1, "zPos": -1}])
            self.assertEqual(list(region_chunks(path, {(-1, -1)})), [all_chunks[1]])
            self.assertEqual(list(region_chunks(path, {(0, 0)})), [])
            # A skipped chunk's payload must never be read or decompressed.
            with path.open("r+b") as region:
                region.seek(8196)
                region.write(b"\x7f")
            self.assertEqual(list(region_chunks(path, {(-1, -1)})), [all_chunks[1]])
            with self.assertRaises(ValueError):
                list(region_chunks(path, {(-32, -32)}))


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


class PrefilterTests(unittest.TestCase):
    def test_refinement_uses_actual_spawn_and_includes_exact_distance(self):
        prediction = {"supply_positions": [[-380, -58]]}
        self.assertTrue(predicted_reachable(prediction, [-380, 200, -154], 96))
        self.assertFalse(predicted_reachable(prediction, [-380, 56, -155], 96))

    def test_impossible_saved_spawn_never_connects_a_player(self):
        server = SimpleNamespace(save=lambda: None,
                                 connect_player=lambda: self.fail("Impossible seed loaded player chunks"))
        prediction = {"seed": 42, "spawn_bounds": [-80, -80, 95, 95], "supply_positions": [[100, 100]]}
        with patch("find_seed.world_info", return_value={"seed": 42, "spawn": [0, 64, 0]}):
            report, snapshot = inspect_filtered_candidate(server, Path("unused"), 96, False, prediction)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["rejected_at"], "saved_spawn_filter")
        self.assertIsNone(snapshot)

    def test_unexpected_spawn_fails_instead_of_rejecting_a_seed(self):
        prediction = {"seed": 42, "spawn_bounds": [-80, -80, 95, 95], "supply_positions": []}
        with patch("find_seed.world_info", return_value={"seed": 42, "spawn": [96, 64, 0]}):
            with self.assertRaisesRegex(RuntimeError, "outside prefilter bounds"):
                inspect_filtered_candidate(SimpleNamespace(save=lambda: None), Path("unused"), 96, False, prediction)

    def test_viable_seed_uses_player_verification_without_rewriting_predictions(self):
        connected = []
        server = SimpleNamespace(connect_player=lambda: connected.append(True), save=lambda: None)
        prediction = {"seed": 42, "spawn_bounds": [-80, -80, 95, 95], "supply_positions": [[4, 8]]}
        with patch("find_seed.world_info", return_value={"seed": 42, "spawn": [0, 64, 0]}), \
                patch("find_seed.inspect_candidate", return_value=({"accepted": False}, None)) as inspect:
            inspect_filtered_candidate(server, Path("unused"), 96, False, prediction)
        self.assertEqual(connected, [True])
        inspect.assert_called_once_with(server, Path("unused"), 96, False)


class NaturalSelectionTests(unittest.TestCase):
    def candidate(self, wood_count, bonus_position=(1, 65, 0), supply_position=(6, 53, 0)):
        bonus = {"id": "minecraft:chest", **dict(zip(("x", "y", "z"), bonus_position)),
                 "LootTable": BONUS, "LootTableSeed": -77}
        supply = {"id": "minecraft:chest", **dict(zip(("x", "y", "z"), supply_position)),
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
            def __init__(self):
                self.commands = []
                self.player = SimpleNamespace(position=[0, 64, 0], chunks_received=256)
                self.player_view_distance = 16
                self.player_simulation_distance = 12
            def save(self, *before):
                self.commands.extend(before)
            def wait_loaded(self, dimension, chunks):
                assert 1 <= len(list(chunks)) <= 256
                self.commands.append("WAIT_LOADED")
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
        self.assertEqual(server.commands[0], "WAIT_LOADED")
        return report

    def test_match_publishes_original_seed_and_original_unopened_snapshot(self):
        report = self.candidate(45)
        self.assertTrue(report["accepted"])
        self.assertEqual(report["selected_chest"]["loot_seed"], 456)
        self.assertEqual(report["starter_position"], [1, 65, 0])
        self.assertNotIn("planned_starter_position", report)
        self.assertTrue(report["natural_loot_seeds_preserved"])

    def test_exact_horizontal_budget_uses_natural_bonus_position_without_buffer(self):
        report = self.candidate(7, bonus_position=(-2, 100, -3), supply_position=(1, -40, 0))
        self.assertEqual(report["bonus_chest"]["slabs_after_crafting_table"], 6)
        self.assertEqual(report["chests_checked"][0]["bridge_slabs_required"], 6)
        self.assertEqual(report["chests_checked"][0]["horizontal_distance"], 1)
        self.assertEqual(report["starter_position"], [-2, 100, -3])
        self.assertTrue(report["accepted"])

    def test_bridge_from_bonus_can_exceed_budget_even_when_spawn_is_close(self):
        report = self.candidate(7, bonus_position=(-6, 65, 0))
        self.assertEqual(report["chests_checked"][0]["bridge_slabs_required"], 12)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["reason"], "insufficient_natural_starter_wood")

    def test_search_waits_for_player_chunks_before_locate(self):
        report = self.candidate(45)
        self.assertTrue(report["accepted"])
        self.assertEqual(report["chunk_loading"]["mode"], "player")
        self.assertEqual(report["chunk_loading"]["view_distance"], 16)
        self.assertEqual(report["chunk_loading"]["simulation_distance"], 12)

    def test_required_items_do_not_override_insufficient_natural_bridge_materials(self):
        report = self.candidate(4)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["reason"], "insufficient_natural_starter_wood")


if __name__ == "__main__":
    unittest.main()
