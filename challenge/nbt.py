"""Read-only NBT/Anvil support for Minecraft Java 26.3 (standard library only)."""
import gzip
import math
from pathlib import Path
import struct
import zlib


class Reader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.offset = 0

    def take(self, count):
        if count < 0 or self.offset + count > len(self.data):
            raise ValueError("Truncated NBT")
        result = self.data[self.offset:self.offset + count]
        self.offset += count
        return result

    def number(self, fmt):
        return struct.unpack(">" + fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        # NBT uses Java modified UTF-8. World identifiers are ASCII, but handle
        # the special null encoding and surrogate pairs in player-written text.
        raw = bytes(self.take(self.number("H"))).replace(b"\xc0\x80", b"\x00")
        text = raw.decode("utf-8", errors="surrogatepass")
        return text.encode("utf-16", errors="surrogatepass").decode("utf-16")

    def value(self, kind):
        if kind in (1, 2, 3, 4, 5, 6):
            return self.number({1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[kind])
        if kind == 7:
            return bytes(self.take(self.number("i")))
        if kind == 8:
            return self.string()
        if kind == 9:
            element_type, count = self.number("b"), self.number("i")
            if not 0 <= count <= 10_000_000:
                raise ValueError("Invalid NBT list length")
            return [self.value(element_type) for _ in range(count)]
        if kind == 10:
            result = {}
            while True:
                child_type = self.number("b")
                if child_type == 0:
                    return result
                name = self.string()
                result[name] = self.value(child_type)
        if kind in (11, 12):
            count = self.number("i")
            if not 0 <= count <= 10_000_000:
                raise ValueError("Invalid NBT array length")
            fmt, size = ("i", 4) if kind == 11 else ("q", 8)
            return list(struct.unpack(">" + str(count) + fmt, self.take(count * size)))
        raise ValueError(f"Unsupported NBT type {kind}")

    def root(self):
        if self.number("b") != 10:
            raise ValueError("Expected compound NBT root")
        self.string()
        return self.value(10)


def read_file(path):
    return Reader(gzip.decompress(Path(path).read_bytes())).root()


def world_info(world):
    world = Path(world)
    level = read_file(world / "level.dat")["Data"]
    if level.get("Version", {}).get("Name") != "26.3":
        raise ValueError("These utilities target Minecraft Java 26.3 worlds")
    settings = read_file(world / "data/minecraft/world_gen_settings.dat")["data"]
    return {"seed": settings["seed"], "spawn": level["spawn"]["pos"], "level": level}


def region_chunks(path, chunks=None):
    """Read saved chunks, optionally restricted to absolute chunk coordinates."""
    path = Path(path)
    rx, rz = map(int, path.stem.split(".")[1:])
    with path.open("rb") as region:
        size = path.stat().st_size
        header = region.read(8192)
        if len(header) < 8192:
            raise ValueError(f"Invalid region: {path}")
        for slot in range(1024):
            x, z = rx * 32 + slot % 32, rz * 32 + slot // 32
            if chunks is not None and (x, z) not in chunks:
                continue
            location = int.from_bytes(header[slot * 4:slot * 4 + 4], "big")
            if not location:
                continue
            offset, sectors = location >> 8, location & 255
            start = offset * 4096
            if start < 8192 or start + 5 > size:
                raise ValueError(f"Invalid chunk offset in {path}")
            region.seek(start)
            length = int.from_bytes(region.read(4), "big")
            if length < 1 or length + 4 > sectors * 4096 or start + length + 4 > size:
                raise ValueError(f"Invalid chunk length in {path}")
            compression = region.read(1)[0]
            if compression & 128:
                payload = (path.parent / f"c.{x}.{z}.mcc").read_bytes()
                compression &= 127
            else:
                payload = region.read(length - 1)
            if compression == 1:
                payload = gzip.decompress(payload)
            elif compression == 2:
                payload = zlib.decompress(payload)
            elif compression != 3:
                raise ValueError(f"Unsupported region compression {compression}; use deflate")
            yield Reader(payload).root()


def state_name(state):
    if isinstance(state, str):
        return state
    return state.get("id", state.get("", state.get("Name", "minecraft:air")))


def block_at(chunk, x, y, z):
    if (x // 16, z // 16) != (chunk["xPos"], chunk["zPos"]):
        return None
    for section in chunk.get("sections", []):
        if section["Y"] != y // 16:
            continue
        states = section.get("block_states")
        if not states:
            return "minecraft:air"
        palette = states["palette"]
        if len(palette) == 1:
            return state_name(palette[0])
        bits = max(4, (len(palette) - 1).bit_length())
        per_long = 64 // bits
        index = ((y & 15) << 8) | ((z & 15) << 4) | (x & 15)
        word = states["data"][index // per_long] & ((1 << 64) - 1)
        entry = (word >> ((index % per_long) * bits)) & ((1 << bits) - 1)
        return state_name(palette[entry])
    return "minecraft:air"


def chests_near_spawn(world, spawn, radius, positions=None):
    """Find real chest blocks, reading only chunks that can contain targets."""
    directory = Path(world) / "dimensions/minecraft/overworld/region"
    if positions is None:
        chunks = {(x, z)
                  for x in range(math.floor((spawn[0] - radius) / 16), math.floor((spawn[0] + radius) / 16) + 1)
                  for z in range(math.floor((spawn[2] - radius) / 16), math.floor((spawn[2] + radius) / 16) + 1)}
    else:
        positions = set(positions)
        chunks = {(x // 16, z // 16) for x, y, z in positions}
    result = []
    for rx, rz in sorted({(x // 32, z // 32) for x, z in chunks}):
        path = directory / f"r.{rx}.{rz}.mca"
        if not path.exists():
            continue
        for chunk in region_chunks(path, chunks):
            if chunk.get("Status") != "minecraft:full":
                continue
            for chest in chunk.get("block_entities", []):
                if chest.get("id") not in ("minecraft:chest", "minecraft:trapped_chest"):
                    continue
                pos = tuple(chest[k] for k in ("x", "y", "z"))
                if positions is not None and pos not in positions:
                    continue
                distance = math.hypot(pos[0] - spawn[0], pos[2] - spawn[2])
                if distance <= radius and block_at(chunk, *pos) in ("minecraft:chest", "minecraft:trapped_chest"):
                    result.append(chest)
    return result


def inventory(chest):
    totals = {}
    for item in chest.get("Items", []):
        name = item["id"]
        totals[name] = totals.get(name, 0) + item.get("count", 1)
    return totals


def slab_budget(items):
    """Maximum slab count after spending four mixed planks on a crafting table."""
    planks = {}
    for name, count in items.items():
        if name.endswith("_log"):
            wood = name[:-4]
            planks[wood] = planks.get(wood, 0) + 4 * count
        elif name.endswith("_planks"):
            wood = name[:-7]
            planks[wood] = planks.get(wood, 0) + count
    # Dynamic programming permits a crafting table made of mixed wood types.
    best = {0: 0}
    for count in planks.values():
        next_best = {}
        for used, slabs in best.items():
            for spend in range(min(count, 4 - used) + 1):
                key = used + spend
                value = slabs + ((count - spend) // 3) * 6
                next_best[key] = max(next_best.get(key, -1), value)
        best = next_best
    return best.get(4, 0)
