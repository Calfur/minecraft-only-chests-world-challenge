# Minecraft Only Chests World Challenge

Two standard-library Python tools for **Minecraft Java 26.3**:

- `find_seed.py`: find a **natural world seed** with moss and bamboo together in a nearby shipwreck supply chest, using Minecraft's own loot evaluation.
- `pregenerate.py`: generate selected chunks in the Overworld, Nether, or End, with progress checkpoints and configurable polling.

These scripts generate **normal vanilla terrain**. They do not yet remove terrain, install a mod, or modify your existing challenge save. The all-dimension void conversion discussed for the challenge is a separate step still to implement. That step must preserve Overworld End portal frames, Nether/End chests, the End fountain, crystal entities, and dragon-fight behavior.

## Requirements

- Python **3.10+** and Java **25+** on macOS or Linux.
- The official 26.3 server bundle. If `--server-jar` is omitted, the tools download the pinned version from Mojang's version manifest and verify its SHA-1.
- Accept the [Minecraft EULA](https://aka.ms/MinecraftEULA) with `--accept-eula` when starting each new temporary runtime.

Java is discovered from `JAVA_HOME`, your PATH, and installed macOS Minecraft/Modrinth runtimes. Override it with `--java /path/to/java`. Use `--heap 8G` if desired; the default is 4G. All temporary servers bind only to `127.0.0.1`, have no RCON, and stop when the script finishes or you press Ctrl-C. Minecraft's own session lock prevents opening a world already in use.

## Find a natural seed

From this repository:

```sh
python3 find_seed.py --accept-eula --start-seed 1 --max-attempts 200
```

Omit `--start-seed` to choose a random starting seed. Every attempt uses a new signed 64-bit world seed. Both beached and underwater shipwrecks are considered.

The default acceptance criteria are:

1. Read the **actual saved world spawn**, not an assumed `(0, 0)`.
2. Find an actual, generated shipwreck **supply chest** within **96 horizontal blocks**. Its block state must really be a chest; unfinished block-entity records are ignored.
3. Let vanilla unpack that chest's **original generated loot seed** in a disposable world, and require **both moss blocks and bamboo in that same chest**.
4. Generate a **natural bonus chest** using vanilla's `--bonusChest` option. Its original loot must provide enough wood for a crafting table and a slab bridge to the supply chest, with two blocks of margin.

Reachability assumes the challenge converter will put the starter chest at the natural spawn's X/Z, aligned to the supply chest's height, preserving the bonus chest's original contents/loot seed. The script reports this planned position; it does not move any chest itself. The bridge budget accounts for different wood types and slab crafting batches. It does not include optional parkour, chest recycling, or later resources.

Useful options:

```sh
python3 find_seed.py --accept-eula --max-distance 64 --max-attempts 500
python3 find_seed.py --accept-eula --distance-only --max-distance 96
python3 find_seed.py --help
```

`--distance-only` retains the natural moss/bamboo and distance checks but disables the bonus-wood bridge test. `--keep-rejected` keeps temporary rejected worlds for diagnosis. `--work-dir` must name a new directory; existing runs are never overwritten.

### Natural loot guarantee

No chest loot seed is selected, injected, or rerolled. Before evaluating loot, the script freezes automatic saves, flushes the server, and snapshots the unopened world. It then asks vanilla to read each target container in the **disposable working world**. It reads the resulting binary NBT inventory after a confirmed save.

For a match, it publishes the **pristine snapshot**, after checking that the selected supply chest and bonus chest retain their original unopened NBT data. A zero/unset loot seed is rejected rather than treated as a reproducible roll.

Successful output:

```text
results/seed-<number>/
  result.json   # seed, spawn, coordinates, natural loot seeds/items, bridge budget, timings
  world/        # unopened vanilla world, ready for pregeneration
```

Per-attempt reasons and timings are retained under `.work/search-*/attempts.jsonl`. Exit code **0** means a match, **2** means the requested range had no match, **1** means an error, and **130** means interrupted. On range exhaustion, the script prints the next starting seed. A new vanilla JVM is started per world seed; this is not a persistent multi-world generator.

## Pregenerate the selected world

Close Minecraft/the server using that world first. The pregenerator runs the official server on the selected world **in place**, so use the fresh world produced by the finder.

Inspect the plan without starting Minecraft:

```sh
python3 pregenerate.py --world results/seed-<number>/world \
  --dimension overworld --radius 2304 --dry-run
```

Then run the desired dimensions separately:

```sh
python3 pregenerate.py --world results/seed-<number>/world \
  --dimension overworld --radius 2304 --accept-eula

python3 pregenerate.py --world results/seed-<number>/world \
  --dimension nether --radius 1024 --accept-eula

python3 pregenerate.py --world results/seed-<number>/world \
  --dimension end --radius 2048 --accept-eula
```

Replace `seed-<number>` with the actual directory name before running commands. Radii above are **examples**, not automatically executed defaults. End radius 2048 is a much larger job than generating just the central dragon island. Choose dimensions and sizes deliberately; a small End radius will not cover distant End-city chests.

The Overworld centers on the real world spawn by default. Nether and End center on `(0, 0)`. Override either coordinate with `--center-x` and `--center-z` (Nether coordinates are not automatically scaled from the Overworld).

The selected square is `[center-radius, center+radius)` in blocks, rounded outward to full chunks. Include any desired border/render buffer in the requested radius. For example, a 4,096-block playable square commonly needs an additional buffer; a 2,304-block generation radius supplies about 256 blocks beyond each side.

Performance and interruption behavior:

- At most **256 chunks** per vanilla `forceload` batch; side length configurable with `--batch-size 1..16`.
- Completion polls default to **0.1 seconds**, replacing the old two-second polling delay.
- Every chunk in a batch must be fully loaded before advancing.
- Checkpoints are written only **after a confirmed disk flush**, every eight batches by default (`--checkpoint-every`).
- Resume the exact same plan with `--resume`. Any batch after the last saved checkpoint may be repeated safely. Keep the selected world unchanged between resume runs.
- Ctrl-C stops the server and clears the tools' forced chunks. These tools are for fresh preparation worlds; existing manual forceload tickets in the selected dimension will be cleared.
- Logs/checkpoints stay in `.work/pregeneration/`. Generated files and downloaded jars are ignored by Git.

```sh
python3 pregenerate.py --world results/seed-<number>/world \
  --dimension overworld --radius 2304 --resume --accept-eula
```

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests cover negative/unaligned chunk bounds, partial batches, 26.3 NBT palette forms, actual chest-block validation, and natural starter-wood bridge budgets. Development also uses bounded real-26.3-server smoke tests; no full map or long seed search is started by the test suite.

Version reference: [Minecraft Java Edition 26.3](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3).
