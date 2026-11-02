# Minecraft Only Chests World Challenge

Two standard-library Python tools for **Minecraft Java 26.3**:

- `find_seed.py`: find a **natural world seed** with moss and bamboo together in a nearby shipwreck supply chest, using Minecraft's own loot evaluation.
- `pregenerate.py`: generate selected chunks in the Overworld, Nether, or End, with progress checkpoints and configurable polling.

These scripts generate **normal vanilla terrain**. They do not yet remove terrain, install a mod, or modify your existing challenge save. The all-dimension void conversion discussed for the challenge is a separate step still to implement. That step must preserve Overworld End portal frames, Nether/End chests, the End fountain, crystal entities, and dragon-fight behavior.

## Requirements

- Python **3.10+** and Java **25+** on macOS or Linux.
- The seed finder also needs a **Java 17+ JDK compiler** (`javac`) for its small cached helper. The compiler can come from a different installation than the Java 25 runtime; override discovery with `--javac /path/to/javac`.
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
4. Generate a **natural bonus chest** using vanilla's `--bonusChest` option. Its original loot must provide enough wood for a crafting table and a slab bridge from its natural position to the supply chest.

The bonus chest stays at its naturally generated position, with its original contents/loot seed. Required bridge slabs are `abs(supply_x - bonus_x) + abs(supply_z - bonus_z)`, with no buffer and no allowance for vertical distance. The separate 96-block horizontal distance limit is measured from world spawn. The bridge budget accounts for a crafting table, different wood types, and slab crafting batches. It does not include optional parkour, chest recycling, or later resources. Reports record the natural `starter_position` and each candidate's `bridge_slabs_required`; `horizontal_distance` remains the distance from world spawn.

Useful options:

```sh
python3 find_seed.py --accept-eula --max-distance 64 --max-attempts 500
python3 find_seed.py --accept-eula --distance-only --max-distance 96
python3 find_seed.py --help
```

`--distance-only` retains the natural moss/bamboo and distance checks but disables the bonus-wood bridge test. `--keep-rejected` keeps temporary rejected worlds for diagnosis. `--work-dir` must name a new directory; existing runs are never overwritten.

### Fast rejection before world generation

The finder keeps one small JVM alive to call the official 26.3 jar's spawn-origin, structure-placement, biome-column, rotation, and template-selection code. It reads the official templates' supply-chest markers. It generates no chunks, worlds, or chest loot at this stage. Most seeds can be rejected without starting a Minecraft server.

The filter covers **every possible world-spawn position** in vanilla's 11×11-chunk spawn search, rather than assuming spawn equals the initial origin. A seed survives if any predicted supply chest could be within the requested distance of that whole area. The biome test checks the entire possible vertical column, retaining uncertain candidates near biome boundaries. This filter can admit unsuitable seeds; acceptance always requires the full check below.

For each survivor, the finder starts a fresh vanilla server, flushes the actual world spawn, and checks the predicted chest distances again. Only seeds still in range connect a player and generate the player area. A disagreement with the predicted spawn bounds stops the search instead of silently rejecting the seed. Reports distinguish `worldgen_prefilter` and `saved_spawn_filter` rejections; filtered seeds have no verified loot or player-position data. `--keep-rejected` cannot retain a world for seeds rejected without creating one.

`prefilter.json` records one-time setup time, and each attempt records filter, startup, inspection, and shutdown times. Include setup time when comparing short runs. The helper uses up to 1 GiB heap in addition to the verifier's `--heap` limit, and reuses libraries from the official server bundle. No third-party generator or approximate loot simulator is used.

### Player-centered verification

Every seed that passes the filters connects a stationary local creative test player and lets vanilla load the area around that player. View distance defaults to 16 chunks and simulation distance to 12; override them as needed:

```sh
python3 find_seed.py --accept-eula \
  --view-distance 16 --simulation-distance 12 \
  --start-seed -7050104159075989928 --max-attempts 1 --keep-rejected
```

The finder uses vanilla player chunk tickets and waits for the search area before running `/locate`. It is still a dedicated server, rather than an integrated singleplayer server, and does not reproduce rendering or player movement. The search area must fit inside the chosen view distance; incompatible settings fail before starting a server. The finder uses a temporary offline-mode runtime bound to `127.0.0.1` so the built-in client needs no account credentials.

Reports record the loading settings and test player's position. Chunk generation can produce different shipwreck heights and loot seeds across loading methods. Bonus-chest positions and contents can also vary between fresh worlds. Verified loot and bridge budgets therefore describe the preserved generated save; recreating its seed alone is not a guarantee of the same result.

### Natural loot guarantee

No chest loot seed is selected, injected, or rerolled. Before evaluating loot, the script freezes automatic saves, flushes the server, and snapshots the unopened world. It then asks vanilla to read each target container in the **disposable working world**. It reads the resulting binary NBT inventory after a confirmed save.

For a match, it publishes the **pristine snapshot**, after checking that the selected supply chest and bonus chest retain their original unopened NBT data. A zero/unset loot seed is rejected rather than treated as a reproducible roll.

Successful output:

```text
results/seed-<number>/
  result.json   # seed, spawn, coordinates, natural loot seeds/items, bridge budget, timings
  world/        # unopened vanilla world, ready for pregeneration
```

Per-attempt reasons and timings are retained under `.work/search-*/attempts.jsonl`. Exit code **0** means a match, **2** means the requested range had no match, **1** means an error, and **130** means interrupted. On range exhaustion, the script prints the next starting seed. The prefilter persists across seeds; every seed needing world verification starts a fresh vanilla JVM.

Chest inspection reads only chunks intersecting the search area. After loot evaluation, it rereads only the target chest chunks; unrelated saved chunks are neither read nor decoded. Probe commands share one console round trip, and the flush before inspection also supplies the unopened snapshot. Reports include `timings_seconds` for startup, inspection, and shutdown, in addition to total elapsed time. Player loading distances and vanilla loot evaluation stay the same.

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
- Logs/checkpoints stay in `.work/pregeneration/`. Temporary working files and downloaded jars are ignored by Git. Published saves and reports under `results/` can be committed; temporary `session.lock` files remain ignored.

```sh
python3 pregenerate.py --world results/seed-<number>/world \
  --dimension overworld --radius 2304 --resume --accept-eula
```

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests cover negative/unaligned chunk bounds, partial batches, 26.3 NBT palette forms, actual chest-block validation, and natural starter-wood bridge budgets. Development also uses bounded real-26.3-server smoke tests; no full map or long seed search is started by the test suite.

Run the optional official-jar prefilter tests, including a biome-boundary regression, with:

```sh
MINECRAFT_PREFILTER_TESTS=1 python3 -m unittest discover -s tests -v
```

These tests compile and run the helper using `.cache/server-26.3.jar` (override with `MINECRAFT_SERVER_JAR`). They start no Minecraft servers and generate no worlds.

Version reference: [Minecraft Java Edition 26.3](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3).
