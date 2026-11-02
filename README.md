# Minecraft Only Chests World Challenge

Standard-library Python tools for **Minecraft Java 26.3**:

- `find_seed.py`: find a **natural world seed** with moss and bamboo together in a nearby shipwreck supply chest, using Minecraft's own loot evaluation.
- `pregenerate.py`: generate selected chunks in the Overworld, Nether, or End (or overlap all three), with progress checkpoints and configurable polling.
- `convert_world.py`: convert a separate copy of the prepared save into the chest-only void challenge in all three dimensions.
- `verify_void.py`: test a disposable copy with the official server, including spawning, End arrival, dragon defeat, and empty future chunks.

Seed finding and pregeneration produce **normal vanilla terrain**. Run the separate converter below to apply the challenge rules. The converter requires a new output directory and preserves the original save and original starter loot.

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

Generate all three dimensions concurrently through one vanilla server:

```sh
python3 pregenerate.py --world results/seed-<number>/world \
  --dimension all --radius 4000 --heap 8G --resume --accept-eula
```

Each dimension keeps at most one batch active and advances independently. All dimensions share the server's CPU workers and disk, so speedup depends on the machine. `--max-batches N` stops cleanly after N additional completed batches for bounded benchmarks; it does not mark the remaining plan complete. A run summary records throughput and time spent flushing saves in the work directory.

Alternatively, run the desired dimensions separately:

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
- Checkpoints are written only **after a confirmed disk flush**, every 32 completed batches across the selected dimensions by default (`--checkpoint-every`). Unsaved work may be repeated on resume. Generation stops at a saved checkpoint if less than 2 GiB of disk space remains.
- Resume the exact same plan with `--resume`. Any batch after the last saved checkpoint may be repeated safely. Keep the selected world unchanged between resume runs.
- Ctrl-C stops the server and clears the tools' forced chunks. These tools are for fresh preparation worlds; existing manual forceload tickets in the selected dimension will be cleared.
- Logs/checkpoints stay in `.work/pregeneration/`. Temporary working files and downloaded jars are ignored by Git. Published saves and reports under `results/` can be committed; temporary `session.lock` files remain ignored.

```sh
python3 pregenerate.py --world results/seed-<number>/world \
  --dimension overworld --radius 2304 --resume --accept-eula
```

## Convert the completed world

Close any server or Minecraft instance using the source save, then create a new copy:

```sh
python3 convert_world.py --world results/seed-<number>/world \
  --output results/seed-<number>/void-world-4k \
  --report results/seed-<number>/result.json --initialize-end --accept-eula
```

`--initialize-end` lets vanilla initialize the natural dragon and fountain on the output copy before terrain removal. Six worker processes convert independent region files. Every written region is read back, and retained chest NBT is checked against the source data. The converter refuses an existing output directory; a failed conversion leaves an incomplete output for inspection rather than modifying the source.

The rules are:

- Overworld: retain chest blocks, trapped chests, and End portal frames/portal blocks.
- Nether: retain chest blocks and trapped chests.
- End: retain chests, the central fountain, portal/gateway blocks, crystals, and the live dragon. Remove islands, pillars, cages, and other terrain. Preserve natural shulkers and elytra item frames; elytra frames are fixed so removal of their supporting ship does not destroy them.

Container positions, inventory NBT, loot tables, and loot seeds remain unchanged. Chest waterlogging is disabled and old fluid/block ticks are cleared so removed water does not return. Pending container records without actual chest blocks in unfinished border chunks are discarded. Heights and lighting are invalidated/rebuilt for the cleared terrain. The source's generated buffer chunks are converted too.

Empty noise generators keep new chunks void in all dimensions while retaining the original biome sources, climate routing, and structure placement lookup. Decoration, carving, aquifers, and new structure generation are disabled. Existing stronghold/fortress starts, references, and piece bounding boxes remain intact, so Eyes of Ender can find the preserved strongholds and fortress mobs can spawn on suitable player-built platforms. No new chests or structures generate beyond the prepared area. Minecraft displays its experimental-settings warning for these custom dimension generators.

The small included datapack places first-time players and respawning players on the original bonus chest, moves arriving End players to the fountain, and removes the automatic obsidian arrival platform. It does not edit loot or continuously clear player builds. Dragon respawn cleanup is intentionally omitted: respawning a defeated dragon can recreate vanilla pillars and cages.

The converted copy starts in Survival with commands enabled for verification (`/gamemode creative` to inspect). It does not carry over the seed finder's disposable player. A conversion receipt is written beside the output directory.

Run the server verification on a disposable copy:

```sh
python3 verify_void.py --world results/seed-<number>/void-world-4k \
  --report results/seed-<number>/result.json \
  --work-dir .work/verify-void
```

The verification work directory must not already contain a `runtime-test-world` folder. This server test uses the EULA acceptance already authorized for these tools. The delivered converted save is not played or opened during verification.

To repair an older flat-generator converted save, close it first and preserve a backup of the settings being replaced:

```sh
python3 repair_void_generation.py --world /path/to/converted-save \
  --original results/seed-<number>/world --backup .work/void-settings-backup
```

This changes only world-generation settings and the datapack. Chunks, player builds, inventory, and chest loot are untouched. The original save must have the same seed and use vanilla noise generators.

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
