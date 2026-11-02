#!/usr/bin/env python3
"""Find a natural Java 26.3 seed with moss + bamboo in a reachable shipwreck chest."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import sys
import time
import uuid

from challenge.nbt import chests_near_spawn, inventory, slab_budget, world_info
from challenge.prefilter import SeedPrefilter
from challenge.server import (DIMENSIONS, Server, add_runtime_arguments, atomic_json,
                              batches, chunk_bounds, ensure_server, find_java)

OVERWORLD = DIMENSIONS["overworld"]
SUPPLY = "minecraft:chests/shipwreck_supply"
BONUS = "minecraft:chests/spawn_bonus_chest"
REQUIRED = {"minecraft:moss_block", "minecraft:bamboo"}


def position(chest):
    return tuple(chest[key] for key in ("x", "y", "z"))


def predicted_reachable(prediction, spawn, max_distance):
    return any(math.hypot(x - spawn[0], z - spawn[2]) <= max_distance
               for x, z in prediction["supply_positions"])


def inspect_filtered_candidate(server, world, max_distance, distance_only, prediction):
    # Before any player joins, level.dat can still contain the placeholder
    # spawn written during initialization. Flush the actual in-memory spawn.
    server.save()
    info = world_info(world)
    if info["seed"] != prediction["seed"]:
        raise RuntimeError("Saved world seed disagrees with prefilter")
    x0, z0, x1, z1 = prediction["spawn_bounds"]
    if not (x0 <= info["spawn"][0] <= x1 and z0 <= info["spawn"][2] <= z1):
        raise RuntimeError("Vanilla spawn fell outside prefilter bounds; refusing to skip seeds")
    if not predicted_reachable(prediction, info["spawn"], max_distance):
        return {"seed": info["seed"], "spawn": info["spawn"], "accepted": False,
                "reason": "no_reachable_supply_chest", "rejected_at": "saved_spawn_filter"}, None
    server.connect_player()
    return inspect_candidate(server, world, max_distance, distance_only)


def inspect_candidate(server, world, max_distance, distance_only):
    if server.player is None:
        raise RuntimeError("Seed search requires a connected local player")
    # Startup has already saved level metadata. No chunks are inspected yet.
    info = world_info(world)
    spawn = info["spawn"]
    report = {"seed": info["seed"], "spawn": spawn}
    radius = math.ceil(max_distance + 32)
    generated_at = time.monotonic()
    # Wait for normal player tickets before /locate can request structures.
    for x0, z0, x1, z1 in batches(chunk_bounds(spawn[0], spawn[2], radius)):
        server.wait_loaded(OVERWORLD, ((x, z) for x in range(x0, x1 + 1)
                                      for z in range(z0, z1 + 1)))
    report["chunk_loading"] = {"mode": "player", "player_position": server.player.position,
                               "view_distance": server.player_view_distance,
                               "simulation_distance": server.player_simulation_distance,
                               "chunks_received": server.player.chunks_received}
    # Tagged lookup includes beached and underwater shipwrecks. The generous
    # padding rejects only clearly distant structures; actual chest distance
    # is checked below, never the structure-origin distance alone.
    lines = server.run(f"execute in {OVERWORLD} positioned {spawn[0]} {spawn[1]} {spawn[2]} run locate structure #minecraft:shipwreck")
    located = next((line for line in lines if "The nearest " in line), "")
    match = re.search(r"\[(-?\d+), ~, (-?\d+)\] \((\d+) blocks", located)
    if not match:
        raise RuntimeError("Could not parse shipwreck lookup; see controller.log")
    report["structure_distance"] = int(match[3])
    if int(match[3]) > max_distance + 128:
        return report | {"accepted": False, "reason": "no_nearby_shipwreck"}, None

    # Freeze automatic saves before the one flush used both for inspection and
    # the unopened snapshot. Nothing edits containers between these steps.
    server.save("save-off")
    report["spawn_area_generation_seconds"] = round(time.monotonic() - generated_at, 3)
    nearby = chests_near_spawn(world, spawn, radius)
    supplies = sorted((c for c in nearby if c.get("LootTable") == SUPPLY
                       and math.hypot(c["x"] - spawn[0], c["z"] - spawn[2]) <= max_distance),
                      key=lambda c: math.hypot(c["x"] - spawn[0], c["z"] - spawn[2]))
    bonuses = [c for c in nearby if c.get("LootTable") == BONUS]
    if not supplies:
        return report | {"accepted": False, "reason": "no_reachable_supply_chest"}, None
    if not bonuses:
        return report | {"accepted": False, "reason": "no_natural_bonus_chest"}, None
    bonus = min(bonuses, key=lambda c: math.hypot(c["x"] - spawn[0], c["z"] - spawn[2]))
    targets = [bonus] + supplies
    if any(c.get("LootTableSeed", 0) == 0 for c in targets):
        return report | {"accepted": False, "reason": "non_deterministic_zero_loot_seed"}, None

    # Retain the already-flushed unopened world while automatic saves are off.
    # Only the disposable server world is probed. The snapshot's LootTableSeed
    # and LootTable fields are never rewritten or replaced with selected rolls.
    snapshot = world.parent.parent / "pristine-world"
    shutil.copytree(world, snapshot, ignore=shutil.ignore_patterns("session.lock"))
    probe_chunks = [(c["x"] // 16, c["z"] // 16) for c in targets]
    server.wait_loaded(OVERWORLD, probe_chunks)
    probes = []
    for chest in targets:
        x, y, z = position(chest)
        # getItem invokes vanilla's own lazy chest-loot generation. Reading and
        # replacing the same slot does not add items. An empty-slot message is
        # harmless: reading it has still unpacked the container's loot table.
        probes.append(f"execute in {OVERWORLD} run item replace block {x} {y} {z} container.0 from block {x} {y} {z} container.0")
    server.run(*probes)
    server.save()
    opened = {position(c): c for c in chests_near_spawn(world, spawn, radius, positions=map(position, targets))}
    for chest in targets:
        if position(chest) not in opened or "LootTable" in opened[position(chest)]:
            raise RuntimeError("Vanilla did not unpack a probe chest; refusing to infer its loot")
    bonus_items = inventory(opened[position(bonus)])
    budget = slab_budget(bonus_items)
    report["bonus_chest"] = {"position": list(position(bonus)), "loot_seed": bonus["LootTableSeed"],
                              "items": bonus_items, "slabs_after_crafting_table": budget}
    report["chests_checked"] = []
    for chest in supplies:
        items = inventory(opened[position(chest)])
        distance = math.hypot(chest["x"] - spawn[0], chest["z"] - spawn[2])
        # Use the natural bonus-chest position, counting only horizontal steps.
        bridge_required = abs(chest["x"] - bonus["x"]) + abs(chest["z"] - bonus["z"])
        candidate = {"position": list(position(chest)), "loot_seed": chest["LootTableSeed"],
                     "horizontal_distance": distance, "items": items,
                     "bridge_slabs_required": bridge_required}
        report["chests_checked"].append(candidate)
        if REQUIRED <= items.keys() and (distance_only or budget >= bridge_required):
            # Verify that the published snapshot still has the original unopened
            # containers and their original, naturally generated loot seeds.
            pristine = {position(c): c for c in chests_near_spawn(snapshot, spawn, radius,
                                                               positions=(position(bonus), position(chest)))}
            for original in (bonus, chest):
                assert pristine[position(original)] == original, "Pristine chest data changed"
            report.update(accepted=True, reason="matched", selected_chest=candidate,
                          starter_position=list(position(bonus)),
                          bridge_materials_required=not distance_only,
                          minecraft_version="26.3", natural_loot_seeds_preserved=True)
            return report, snapshot
    reason = "missing_required_loot"
    if any(REQUIRED <= c["items"].keys() for c in report["chests_checked"]):
        reason = "insufficient_natural_starter_wood"
    return report | {"accepted": False, "reason": reason}, snapshot


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(p)
    p.add_argument("--start-seed", type=int, help="First signed 64-bit seed; otherwise choose a random starting seed")
    p.add_argument("--max-attempts", type=int, default=200)
    p.add_argument("--max-distance", type=float, default=96, help="Maximum horizontal distance from real spawn to supply chest")
    p.add_argument("--distance-only", action="store_true", help="Skip the natural bonus-chest bridge-material requirement")
    p.add_argument("--work-dir", type=Path, help="New, unused run directory")
    p.add_argument("--output-dir", type=Path, default=Path("results"))
    p.add_argument("--keep-rejected", action="store_true", help="Retain rejected temporary worlds for debugging")
    p.add_argument("--view-distance", type=int, default=16, help="Local player view distance in chunks (2..32)")
    p.add_argument("--simulation-distance", type=int, default=12, help="Local player simulation distance (2..32)")
    p.add_argument("--javac", help="Java 17+ compiler for the cached vanilla prefilter helper")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not 1 <= args.max_attempts <= 1_000_000 or not 1 <= args.max_distance <= 512:
        raise ValueError("Use 1..1,000,000 attempts and 1..512 blocks maximum distance")
    if not 2 <= args.view_distance <= 32 or not 2 <= args.simulation_distance <= 32:
        raise ValueError("Player view/simulation distances must be 2..32 chunks")
    # Cover the square's corners, allowing a chunk of offset for player spawn.
    needed = math.ceil(math.sqrt(2) * (math.ceil((args.max_distance + 32) / 16) + 1)) + 1
    if needed > args.view_distance:
        raise ValueError("Increase --view-distance or reduce --max-distance to cover the player search area")
    start = args.start_seed if args.start_seed is not None else secrets.randbelow(2**64 - args.max_attempts) - 2**63
    if not -(2**63) <= start <= 2**63 - args.max_attempts:
        raise ValueError("Seed range must fit signed 64-bit integers")
    run = (args.work_dir or Path(".work") / ("search-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])).expanduser().resolve()
    if run.exists():
        raise FileExistsError(f"Choose a new --work-dir; refusing to overwrite {run}")
    run.mkdir(parents=True)
    atomic_json(run / "run.json", {"minecraft_version": "26.3", "start_seed": start,
                                  "max_attempts": args.max_attempts, "max_distance": args.max_distance,
                                  "chunk_loading": "player", "view_distance": args.view_distance,
                                  "simulation_distance": args.simulation_distance})
    java = find_java(args.java)
    jar = ensure_server(args.server_jar, Path(".cache"))
    print(f"Searching from seed {start}; log: {run / 'attempts.jsonl'}", flush=True)
    setup_began = time.monotonic()
    with SeedPrefilter(jar, java, run / "runtime", args.javac) as prefilter:
        atomic_json(run / "prefilter.json", {"setup_seconds": round(time.monotonic() - setup_began, 3),
                                             "spawn_search_chunks": 11,
                                             "minecraft_version": "26.3"})
        return search(args, run, java, jar, start, prefilter)


def search(args, run, java, jar, start, prefilter):
    for offset in range(args.max_attempts):
        seed = start + offset
        attempt = run / f"attempt-{offset + 1:06d}"
        world = attempt / "worlds" / f"seed-{seed}"
        world.parent.mkdir(parents=True)
        began = time.monotonic()
        print(f"Attempt {offset + 1}/{args.max_attempts}: seed {seed}", flush=True)
        prediction = prefilter.check(seed, args.max_distance)
        filtered = time.monotonic()
        if not prediction["supply_positions"]:
            report = {"seed": seed, "accepted": False, "reason": "no_possible_supply_chest",
                      "rejected_at": "worldgen_prefilter"}
            snapshot = None
            started = inspected = finished = filtered
        else:
            with Server(jar, java, run / "runtime", world, args.heap, seed=seed,
                        accept_eula=args.accept_eula, bonus_chest=True, poll_interval=args.poll_interval,
                        player_view_distance=args.view_distance,
                        player_simulation_distance=args.simulation_distance, defer_player=True) as server:
                started = time.monotonic()
                report, snapshot = inspect_filtered_candidate(server, world, args.max_distance,
                                                               args.distance_only, prediction)
                inspected = time.monotonic()
            finished = time.monotonic()
        report["prefilter"] = prediction
        report["timings_seconds"] = {"prefilter": round(filtered - began, 3),
                                     "startup": round(started - filtered, 3),
                                     "inspection": round(inspected - started, 3),
                                     "shutdown": round(finished - inspected, 3)}
        report["elapsed_seconds"] = round(time.monotonic() - began, 3)
        with (run / "attempts.jsonl").open("a") as log:
            log.write(json.dumps(report) + "\n")
        print(f"  {report['reason']} ({report['elapsed_seconds']:.1f}s)", flush=True)
        if report["accepted"]:
            destination = args.output_dir.expanduser().resolve() / f"seed-{seed}"
            if destination.exists():
                raise FileExistsError(f"Refusing to replace {destination}; pristine world retained at {snapshot}")
            destination.mkdir(parents=True)
            shutil.move(str(snapshot), str(destination / "world"))
            report["world"] = str(destination / "world")
            atomic_json(destination / "result.json", report)
            print(json.dumps(report, indent=2), flush=True)
            if not args.keep_rejected:
                shutil.rmtree(attempt)
            return 0
        if not args.keep_rejected:
            shutil.rmtree(attempt)
    print("No match in this range. Continue with --start-seed " + str(start + args.max_attempts), flush=True)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped; Minecraft has been shut down. Run logs are retained.", file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
