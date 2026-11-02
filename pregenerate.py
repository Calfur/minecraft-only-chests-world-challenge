#!/usr/bin/env python3
"""Pregenerate a stopped vanilla 26.3 world, with explicit dimensions and resumable batches."""
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
import sys
import time

from challenge.nbt import world_info
from challenge.server import (DIMENSIONS, Server, add_runtime_arguments, atomic_json,
                              batches, chunk_bounds, ensure_server, find_java)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(p)
    p.add_argument("--world", required=True, type=Path, help="Existing, stopped 26.3 world; modified in place")
    p.add_argument("--dimension", required=True, choices=[*DIMENSIONS, "all"], help="Use all to overlap generation across dimensions in one server")
    p.add_argument("--radius", required=True, type=int, help="Half-width in blocks, including any desired buffer")
    p.add_argument("--center-x", type=int)
    p.add_argument("--center-z", type=int)
    p.add_argument("--batch-size", type=int, default=16, help="Side length in chunks, 1..16")
    p.add_argument("--work-dir", type=Path, default=Path(".work/pregeneration"))
    p.add_argument("--resume", action="store_true", help="Resume confirmed/saved batches from this exact plan")
    p.add_argument("--checkpoint-every", type=int, default=32, help="Flush before checkpointing this many completed batches across dimensions")
    p.add_argument("--max-batches", type=int, help="Stop cleanly after this many additional batches, for benchmarks")
    p.add_argument("--dry-run", action="store_true", help="Print bounds and chunk count without starting Minecraft")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    world = args.world.expanduser().resolve()
    info = world_info(world)
    if not 1 <= args.radius <= 30_000_000 or not 1 <= args.checkpoint_every <= 1024 or not 1 <= args.batch_size <= 16:
        raise ValueError("Invalid radius or checkpoint interval")
    if args.max_batches is not None and args.max_batches < 1:
        raise ValueError("max-batches must be positive")
    directory = args.work_dir.expanduser().resolve()
    states = []
    dimensions = list(DIMENSIONS) if args.dimension == "all" else [args.dimension]
    for dimension in dimensions:
        spawn = info["spawn"] if dimension == "overworld" else [0, 0, 0]
        cx = args.center_x if args.center_x is not None else spawn[0]
        cz = args.center_z if args.center_z is not None else spawn[2]
        bounds = chunk_bounds(cx, cz, args.radius)
        if min(bounds) < -1_875_000 or max(bounds) > 1_874_999:
            raise ValueError("Requested chunks exceed Minecraft's coordinate limits")
        plan = {"world": str(world), "seed": info["seed"], "version": "26.3",
                "dimension": DIMENSIONS[dimension], "center": [cx, cz],
                "radius": args.radius, "bounds_in_chunks": list(bounds), "batch_size": args.batch_size}
        tasks = list(batches(bounds, args.batch_size))
        chunks = (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1)
        print(json.dumps(plan | {"chunks": chunks, "batches": len(tasks)}, indent=2), flush=True)
        key = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:20]
        checkpoint = directory / f"progress-{key}.json"
        completed = 0
        if args.resume and checkpoint.exists():
            previous = json.loads(checkpoint.read_text())
            if previous["plan"] != plan:
                raise ValueError("Checkpoint plan differs")
            completed = previous["completed_batches"]
            if not 0 <= completed <= len(tasks):
                raise ValueError("Invalid checkpoint batch count")
        states.append({"plan": plan, "tasks": tasks, "completed": completed,
                       "checkpoint": checkpoint, "chunks": chunks, "active_since": None})
    if args.dry_run:
        return 0
    if all(s["completed"] == len(s["tasks"]) for s in states):
        print("This plan is already complete.", flush=True)
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    java = find_java(args.java)
    jar = ensure_server(args.server_jar, Path(".cache"))
    began = time.monotonic()
    processed = 0
    generated_chunks = 0
    save_seconds = 0.0
    # Keep room for the server to save and stop safely on this nearly full disk.
    minimum_free = 2 * 1024 ** 3
    with Server(jar, java, directory / "runtime", world, args.heap,
                accept_eula=args.accept_eula, poll_interval=args.poll_interval) as server:
        def checkpoint():
            nonlocal save_seconds
            started = time.monotonic()
            server.save()
            save_seconds += time.monotonic() - started
            for state in states:
                atomic_json(state["checkpoint"], {"plan": state["plan"],
                            "completed_batches": state["completed"],
                            "elapsed_seconds_this_run": round(time.monotonic() - began, 3)})

        def queue_next(state):
            if state["completed"] < len(state["tasks"]):
                server.start_batch(state["plan"]["dimension"], state["tasks"][state["completed"]])
                state["active_since"] = time.monotonic()

        for state in states:
            queue_next(state)
        while any(s["completed"] < len(s["tasks"]) for s in states):
            advanced = False
            for state in states:
                if state["completed"] == len(state["tasks"]):
                    continue
                dimension = state["plan"]["dimension"]
                task = state["tasks"][state["completed"]]
                if not server.batch_loaded(dimension, task):
                    if time.monotonic() - state["active_since"] > 180:
                        raise TimeoutError(f"Chunks did not become fully loaded in {dimension}")
                    continue
                advanced = True
                state["completed"] += 1
                processed += 1
                x0, z0, x1, z1 = task
                generated_chunks += (x1 - x0 + 1) * (z1 - z0 + 1)
                elapsed = time.monotonic() - began
                print(f"Batch {state['completed']}/{len(state['tasks'])}; {elapsed:.1f}s this run; {dimension}", flush=True)
                if processed % args.checkpoint_every == 0:
                    checkpoint()
                    if shutil.disk_usage(world).free < minimum_free:
                        raise RuntimeError("Stopped at a saved checkpoint: less than 2 GiB disk space remains")
                if args.max_batches and processed >= args.max_batches:
                    checkpoint()
                    break
                queue_next(state)
            if args.max_batches and processed >= args.max_batches:
                break
            if not advanced:
                time.sleep(args.poll_interval)
        checkpoint()
    elapsed = time.monotonic() - began
    complete = all(s["completed"] == len(s["tasks"]) for s in states)
    summary = {"complete": complete, "heap": args.heap, "dimensions": dimensions,
               "processed_batches": processed, "chunks_this_run": generated_chunks,
               "elapsed_seconds": round(elapsed, 3), "save_seconds": round(save_seconds, 3),
               "chunks_per_second": round(generated_chunks / elapsed, 3)}
    atomic_json(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    print("Generation complete." if complete else "Benchmark stopped cleanly; checkpoints saved.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped; rerun with --resume to continue from the last saved checkpoint.", file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
