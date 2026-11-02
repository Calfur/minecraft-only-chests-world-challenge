import java.io.BufferedReader;
import java.io.FileNotFoundException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Predicate;

/**
 * Java 26.3 only. Calls official worldgen without a server, chunks, or loot.
 * Reflection lets a Java 17 JDK compile this helper for a Java 25 runtime that
 * may contain only a JRE. All Minecraft/Gson classes come from the server jar.
 *
 * MinecraftServer.setInitialSpawn searches an 11x11 chunk square around
 * ChunkGenerator.getOrigin. Every possible global spawn is inside
 * [origin-80, origin+95] on each horizontal axis. Retain every supply marker
 * within maxDistance of that entire rectangle, not just its initial origin.
 * Survivors still require fresh player-mode world and loot verification.
 */
public class SeedFilter {
    private static final String CONTEXT = "world.level.levelgen.structure.Structure$GenerationContext";
    private static final String PIECES = "world.level.levelgen.structure.structures.ShipwreckPieces";
    private static final String TEMPLATE = "world.level.levelgen.structure.templatesystem.StructureTemplate";
    private static final Map<String, List<Object>> MARKERS = new HashMap<>();

    private final Object generator;
    private final Object settings;
    private final Object noises;
    private final Object biomeSource;
    private final Object placement;
    private final Object height;
    private final List<Set<String>> allowed;
    private final Constructor<?> context;

    private static Class<?> cls(String name) throws Exception {
        return Class.forName(name.startsWith("com.") ? name : "net.minecraft." + name);
    }

    private static Object call(String owner, Object target, String name, Object... args) throws Exception {
        for (Method method : cls(owner).getMethods()) {
            if (!method.getName().equals(name) || method.getParameterCount() != args.length) continue;
            Class<?>[] types = method.getParameterTypes();
            boolean compatible = true;
            for (int i = 0; i < args.length; i++) {
                Class<?> type = types[i];
                if (type == long.class) type = Long.class;
                if (type == int.class) type = Integer.class;
                if (args[i] != null && !type.isInstance(args[i])) {
                    compatible = false;
                    break;
                }
            }
            if (compatible) return method.invoke(target, args);
        }
        throw new NoSuchMethodException(owner + "." + name + Arrays.toString(args));
    }

    private static Object field(String owner, String name) throws Exception {
        return cls(owner).getField(name).get(null);
    }

    private static Object privateField(String owner, String name) throws Exception {
        Field field = cls(owner).getDeclaredField(name);
        field.setAccessible(true);
        return field.get(null);
    }

    private static Object value(Object holder) throws Exception {
        return call("core.Holder", holder, "value");
    }

    // VanillaRegistries' lookup has unbound tags. Resolve the pinned jar's
    // biome tags directly, including nested tags, rather than using contains().
    private static Set<String> biomeTag(String tag) throws Exception {
        Set<String> result = new HashSet<>();
        String path = "/data/minecraft/tags/worldgen/biome/" + tag.substring(10) + ".json";
        try (InputStream input = SeedFilter.class.getResourceAsStream(path)) {
            if (input == null) throw new FileNotFoundException(path);
            Object json = call("com.google.gson.JsonParser", null, "parseReader", new InputStreamReader(input));
            Object object = call("com.google.gson.JsonElement", json, "getAsJsonObject");
            Object values = call("com.google.gson.JsonObject", object, "getAsJsonArray", "values");
            for (Object element : (Iterable<?>) values) {
                String entry = (String) call("com.google.gson.JsonElement", element, "getAsString");
                if (entry.startsWith("#")) result.addAll(biomeTag(entry.substring(1)));
                else result.add(entry);
            }
        }
        return result;
    }

    private static List<Object> supplies(String template) throws Exception {
        if (MARKERS.containsKey(template)) return MARKERS.get(template);
        List<Object> positions = new ArrayList<>();
        String path = "/data/minecraft/structure/" + template.substring(10) + ".nbt";
        try (InputStream input = SeedFilter.class.getResourceAsStream(path)) {
            if (input == null) throw new FileNotFoundException(path);
            Object nbt = call("nbt.NbtIo", null, "readCompressed", input,
                              call("nbt.NbtAccounter", null, "defaultQuota"));
            for (Object block : (Iterable<?>) call("nbt.CompoundTag", nbt, "getListOrEmpty", "blocks")) {
                Object data = call("nbt.CompoundTag", block, "getCompoundOrEmpty", "nbt");
                if (!call("nbt.CompoundTag", data, "getStringOr", "metadata", "").equals("supply_chest")) continue;
                Object pos = call("nbt.CompoundTag", block, "getListOrEmpty", "pos");
                int x = (int) call("nbt.ListTag", pos, "getIntOr", 0, 0);
                int y = (int) call("nbt.ListTag", pos, "getIntOr", 1, 0);
                int z = (int) call("nbt.ListTag", pos, "getIntOr", 2, 0);
                positions.add(cls("core.BlockPos").getConstructor(int.class, int.class, int.class)
                              .newInstance(x, y, z));
            }
        }
        MARKERS.put(template, positions);
        return positions;
    }

    private SeedFilter() throws Exception {
        call("SharedConstants", null, "tryDetectVersion");
        call("server.Bootstrap", null, "bootStrap");
        Object lookup = call("data.registries.VanillaRegistries", null, "createWorldLookup");
        Object stem = call("world.level.levelgen.presets.WorldPresets", null, "getNormalOverworld", lookup);
        generator = call("world.level.dimension.LevelStem", stem, "generator");
        settings = value(call("world.level.levelgen.NoiseBasedChunkGenerator", generator, "generatorSettings"));
        noises = call("core.HolderLookup$Provider", lookup, "lookupOrThrow", field("core.registries.Registries", "NOISE"));
        biomeSource = call("world.level.chunk.ChunkGenerator", generator, "getBiomeSource");
        allowed = List.of(biomeTag("minecraft:has_structure/shipwreck"),
                          biomeTag("minecraft:has_structure/shipwreck_beached"));
        Object sets = call("core.HolderLookup$Provider", lookup, "lookupOrThrow",
                           field("core.registries.Registries", "STRUCTURE_SET"));
        Object set = value(call("core.HolderGetter", sets, "getOrThrow",
                               field("world.level.levelgen.structure.BuiltinStructureSets", "SHIPWRECKS")));
        placement = call("world.level.levelgen.structure.StructureSet", set, "placement");
        height = call("world.level.LevelHeightAccessor", null, "create", -64, 384);
        context = Arrays.stream(cls(CONTEXT).getConstructors())
                        .filter(c -> c.getParameterCount() == 10).findFirst().orElseThrow();
    }

    private static int gap(int low, int high, int otherLow, int otherHigh) {
        return Math.max(0, Math.max(low - otherHigh, otherLow - high));
    }

    private String check(long seed, double distance) throws Exception {
        long began = System.nanoTime();
        Object random = call("world.level.levelgen.RandomState", null, "create", noises, seed, settings);
        Object origin = call("world.level.chunk.ChunkGenerator", generator, "getOrigin", random);
        int ox = (int) call("world.level.ChunkPos", origin, "x") * 16;
        int oz = (int) call("world.level.ChunkPos", origin, "z") * 16;
        Object sampler = call("world.level.levelgen.RandomState", random, "createClimateSampler",
                              field("world.level.levelgen.densityfunction.SamplerContext", "EMPTY_UNCACHED"));
        List<String> candidates = new ArrayList<>();
        // Largest rotated shipwreck fits within +/-32 of its origin. This
        // radius covers that extent plus the entire possible spawn rectangle.
        int radius = (int) Math.ceil(distance + 128);
        int spacing = (int) call("world.level.levelgen.structure.placement.RandomSpreadStructurePlacement",
                                 placement, "spacing");
        int regionSize = spacing * 16;
        for (int rx = Math.floorDiv(ox - radius, regionSize); rx <= Math.floorDiv(ox + radius, regionSize); rx++) {
            for (int rz = Math.floorDiv(oz - radius, regionSize); rz <= Math.floorDiv(oz + radius, regionSize); rz++) {
                Object chunk = call("world.level.levelgen.structure.placement.RandomSpreadStructurePlacement",
                                    placement, "getPotentialStructureChunk", seed, rx * spacing, rz * spacing);
                int x = (int) call("world.level.ChunkPos", chunk, "x") * 16;
                int z = (int) call("world.level.ChunkPos", chunk, "z") * 16;
                if (Math.hypot(gap(ox - 80, ox + 95, x - 32, x + 32),
                               gap(oz - 80, oz + 95, z - 32, z + 32)) > distance) continue;
                for (int variant = 0; variant < 2; variant++) {
                    Set<String> biomes = allowed.get(variant);
                    Predicate<Object> valid = biome -> {
                        try {
                            return biomes.contains((String) call("core.Holder", biome, "getRegisteredName"));
                        } catch (Exception error) {
                            throw new RuntimeException(error);
                        }
                    };
                    // The necessary biome-column test uses neither a template
                    // manager nor RegistryAccess, so both can be absent here.
                    Object ctx = context.newInstance(null, generator, biomeSource, sampler, random,
                                                     null, seed, chunk, height, valid);
                    // Retain all heights. A single-height test missed a known
                    // chest at a biome boundary; full verification resolves it.
                    if (!(boolean) call(CONTEXT, ctx, "couldValidBiomeExistOnTopOfChunkCenter")) continue;
                    // Mirror ShipwreckStructure.generatePieces: rotation first,
                    // then uniform template choice, using its independent RNG.
                    Object rng = call(CONTEXT, ctx, "random");
                    Object rotation = call("world.level.block.Rotation", null, "getRandom", rng);
                    Object[] templates = (Object[]) privateField(PIECES, variant == 1
                            ? "STRUCTURE_LOCATION_BEACHED" : "STRUCTURE_LOCATION_OCEAN");
                    Object template = templates[(int) call("util.RandomSource", rng, "nextInt", templates.length)];
                    for (Object marker : supplies(template.toString())) {
                        Object transformed = call(TEMPLATE, null, "transform", marker,
                                                  field("world.level.block.Mirror", "NONE"), rotation,
                                                  privateField(PIECES, "PIVOT"));
                        int sx = x + (int) call("core.Vec3i", transformed, "getX");
                        int sz = z + (int) call("core.Vec3i", transformed, "getZ");
                        if (Math.hypot(gap(ox - 80, ox + 95, sx, sx),
                                       gap(oz - 80, oz + 95, sz, sz)) <= distance) {
                            candidates.add("[" + sx + "," + sz + "]");
                        }
                    }
                }
            }
        }
        return "{\"seed\":" + seed + ",\"origin\":[" + ox + "," + oz + "],"
                + "\"spawn_bounds\":[" + (ox - 80) + "," + (oz - 80) + "," + (ox + 95) + "," + (oz + 95) + "],"
                + "\"supply_positions\":" + candidates + ",\"elapsed_seconds\":" + ((System.nanoTime() - began) / 1e9) + "}";
    }

    public static void main(String[] args) throws Exception {
        // Bootstrap redirects System.out to its logger. Keep the original
        // stream for an unambiguous line protocol to the Python controller.
        PrintStream protocol = System.out;
        SeedFilter filter = new SeedFilter();
        protocol.println("SF_READY");
        try (BufferedReader input = new BufferedReader(new InputStreamReader(System.in))) {
            for (String line; (line = input.readLine()) != null;) {
                String[] parts = line.split(" ");
                protocol.println("SF_RESULT " + filter.check(Long.parseLong(parts[0]), Double.parseDouble(parts[1])));
            }
        }
    }
}
