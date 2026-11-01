#!/usr/bin/env python3
"""Pregenerate a stopped vanilla 26.3 world, with explicit dimensions and resumable batches."""
import argparse
import hashlib
import json
import math
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
    p.add_argument("--dimension", required=True, choices=DIMENSIONS, help="Run separately for each desired dimension")
    p.add_argument("--radius", required=True, type=int, help="Half-width in blocks, including any desired buffer")
    p.add_argument("--center-x", type=int)
    p.add_argument("--center-z", type=int)
    p.add_argument("--batch-size", type=int, default=16, help="Side length in chunks, 1..16")
    p.add_argument("--work-dir", type=Path, default=Path(".work/pregeneration"))
    p.add_argument("--resume", action="store_true", help="Resume confirmed/saved batches from this exact plan")
    p.add_argument("--checkpoint-every", type=int, default=8, help="Flush before checkpointing this many batches")
    p.add_argument("--dry-run", action="store_true", help="Print bounds and chunk count without starting Minecraft")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    world = args.world.expanduser().resolve()
    info = world_info(world)
    if not 1 <= args.radius <= 30_000_000 or not 1 <= args.checkpoint_every <= 1024 or not 1 <= args.batch_size <= 16:
        raise ValueError("Invalid radius or checkpoint interval")
    spawn = info["spawn"] if args.dimension == "overworld" else [0, 0, 0]
    cx = args.center_x if args.center_x is not None else spawn[0]
    cz = args.center_z if args.center_z is not None else spawn[2]
    bounds = chunk_bounds(cx, cz, args.radius)
    if min(bounds) < -1_875_000 or max(bounds) > 1_874_999:
        raise ValueError("Requested chunks exceed Minecraft's coordinate limits")
    plan = {"world": str(world), "seed": info["seed"], "version": "26.3",
            "dimension": DIMENSIONS[args.dimension], "center": [cx, cz],
            "radius": args.radius, "bounds_in_chunks": list(bounds), "batch_size": args.batch_size}
    task_count = math.ceil((bounds[2] - bounds[0] + 1) / args.batch_size) * math.ceil((bounds[3] - bounds[1] + 1) / args.batch_size)
    chunk_count = (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1)
    print(json.dumps(plan | {"chunks": chunk_count, "batches": task_count}, indent=2), flush=True)
    if args.dry_run:
        return 0
    directory = args.work_dir.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:20]
    checkpoint = directory / f"progress-{key}.json"
    completed = 0
    if args.resume and checkpoint.exists():
        previous = json.loads(checkpoint.read_text())
        if previous["plan"] != plan:
            raise ValueError("Checkpoint plan differs")
        completed = previous["completed_batches"]
        if not 0 <= completed <= task_count:
            raise ValueError("Invalid checkpoint batch count")
    if completed == task_count:
        print("This plan is already complete.", flush=True)
        return 0
    java = find_java(args.java)
    jar = ensure_server(args.server_jar, Path(".cache"))
    began = time.monotonic()
    initial = completed
    with Server(jar, java, directory / "runtime", world, args.heap,
                accept_eula=args.accept_eula, poll_interval=args.poll_interval) as server:
        for index, task in enumerate(batches(bounds, args.batch_size)):
            if index < completed:
                continue
            server.force_batch(plan["dimension"], task)
            completed = index + 1
            elapsed = time.monotonic() - began
            print(f"Batch {completed}/{task_count}; {elapsed:.1f}s this run; {plan['dimension']}", flush=True)
            if completed % args.checkpoint_every == 0 or completed == task_count:
                # Checkpoint only after a confirmed flush, never just after a
                # chunk-loaded event. An interrupted unsaved batch is repeated.
                server.save()
                atomic_json(checkpoint, {"plan": plan, "completed_batches": completed,
                                         "elapsed_seconds_this_run": round(elapsed, 3)})
    print(f"Completed {chunk_count} chunks; {completed - initial} batches processed this run.", flush=True)
    print("This generated normal terrain. Run the separate challenge converter/mod to remove blocks.", flush=True)
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
