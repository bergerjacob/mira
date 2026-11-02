import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.authlib.GameProfile;
import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.gametest.framework.GameTestServer;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.TagParser;
import net.minecraft.resources.Identifier;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.packs.repository.PackRepository;
import net.minecraft.server.packs.repository.ServerPacksSource;
import net.minecraft.util.RandomSource;
import net.minecraft.util.Util;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.BaseContainerBlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructurePlaceSettings;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;
import net.minecraft.world.level.storage.LevelStorageSource;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.BooleanSupplier;

/**
 * Batched vanilla tracer for MIRA: boots one in-process GameTestServer (same recipe as
 * Nucleation's tools/gametest TraceCapture) and runs many capture jobs in it.
 *
 * Differences from TraceCapture (whose per-tick scan/use/place logic is reused verbatim):
 * jobs come from a JSON file, structures are read from SNBT text in-process (no datapack
 * or Snbt2Nbt launch), several --use/--set ops per job, the initial state and container
 * contents are emitted in full, and the region is wiped between jobs.
 *
 * usage: OracleBatch --universe DIR --jobs jobs.json --out DIR [--idle N]
 * job:   {id, snbt, ticks, preroll, settle: place|known_shape, seed, ops: [{tick, op: use|set, pos, block}]}
 * Positions are structure-local; the structure's min corner is placed at the printed origin.
 */
public final class OracleBatch {
    private OracleBatch() {}

    private static final int MARGIN = 4;
    private static BlockPos ORIGIN;
    private static Method tickServer, waitUntilNextTick;
    private static MinecraftServer server;
    private static ServerLevel level;

    public static void main(String[] args) throws Exception {
        String universe = arg(args, "--universe", "universe");
        Path jobsPath = Path.of(arg(args, "--jobs", "jobs.json"));
        Path outDir = Path.of(arg(args, "--out", "out"));
        int idle = Integer.parseInt(arg(args, "--idle", "10"));
        boolean pace = Boolean.parseBoolean(arg(args, "--pace", "false"));

        long t0 = System.nanoTime();
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        Util.startTimerHackThread();

        LevelStorageSource storage = LevelStorageSource.createDefault(Paths.get(universe));
        LevelStorageSource.LevelStorageAccess access = storage.createAccess("gametestworld");
        PackRepository packs = ServerPacksSource.createPackRepository(access);
        GameTestServer gts = GameTestServer.create(
                Thread.currentThread(), access, packs, Optional.empty(), false, 1);
        server = gts;
        Method initServer = MinecraftServer.class.getDeclaredMethod("initServer");
        tickServer = MinecraftServer.class.getDeclaredMethod("tickServer", BooleanSupplier.class);
        waitUntilNextTick = MinecraftServer.class.getDeclaredMethod("waitUntilNextTick");
        initServer.setAccessible(true);
        tickServer.setAccessible(true);
        waitUntilNextTick.setAccessible(true);
        initServer.invoke(server);
        level = server.overworld();
        BlockPos spawn = level.getRespawnData().pos();
        ORIGIN = new BlockPos(spawn.getX(), 100, spawn.getZ());
        double bootMs = (System.nanoTime() - t0) / 1e6;
        System.out.printf("boot %.0f ms, origin %s%n", bootMs, ORIGIN);

        Files.createDirectories(outDir);
        JsonArray jobs = JsonParser.parseString(Files.readString(jobsPath)).getAsJsonArray();
        BlockPos dirtyMin = null, dirtyMax = null;
        for (JsonElement el : jobs) {
            JsonObject job = el.getAsJsonObject();
            String id = job.get("id").getAsString();
            long j0 = System.nanoTime();
            JsonObject result;
            BlockPos[] box = null;
            try {
                if (dirtyMin != null) {
                    wipe(dirtyMin, dirtyMax, idle);
                }
                result = runJob(job, pace);
                box = new BlockPos[] {
                        new BlockPos(result.get("_min").getAsJsonArray().get(0).getAsInt(),
                                result.get("_min").getAsJsonArray().get(1).getAsInt(),
                                result.get("_min").getAsJsonArray().get(2).getAsInt()),
                        new BlockPos(result.get("_max").getAsJsonArray().get(0).getAsInt(),
                                result.get("_max").getAsJsonArray().get(1).getAsInt(),
                                result.get("_max").getAsJsonArray().get(2).getAsInt())};
                result.remove("_min");
                result.remove("_max");
            } catch (Throwable e) {
                result = new JsonObject();
                result.addProperty("error", e.toString());
                System.out.println("job " + id + " failed: " + e);
            }
            result.addProperty("id", id);
            result.addProperty("job_ms", (System.nanoTime() - j0) / 1e6);
            result.addProperty("boot_ms", bootMs);
            Files.writeString(outDir.resolve(id + ".json"), new Gson().toJson(result));
            if (box != null) {
                dirtyMin = dirtyMin == null ? box[0] : min(dirtyMin, box[0]);
                dirtyMax = dirtyMax == null ? box[1] : max(dirtyMax, box[1]);
            }
            System.out.printf("job %s done in %.0f ms%n", id, (System.nanoTime() - j0) / 1e6);
        }
        server.halt(false);
        deleteRecursively(Paths.get(universe));
        System.exit(0);
    }

    private static BlockPos min(BlockPos a, BlockPos b) {
        return new BlockPos(Math.min(a.getX(), b.getX()), Math.min(a.getY(), b.getY()),
                Math.min(a.getZ(), b.getZ()));
    }

    private static BlockPos max(BlockPos a, BlockPos b) {
        return new BlockPos(Math.max(a.getX(), b.getX()), Math.max(a.getY(), b.getY()),
                Math.max(a.getZ(), b.getZ()));
    }

    private static void tick() throws Exception {
        tickServer.invoke(server, (BooleanSupplier) () -> true);
        waitUntilNextTick.invoke(server);
    }

    /** Clear everything a previous job could have left behind, then drain pending ticks. */
    private static void wipe(BlockPos min, BlockPos max, int idle) throws Exception {
        for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
            level.setBlock(pos, Blocks.AIR.defaultBlockState(), 2);
        }
        AABB box = new AABB(min.getX(), min.getY(), min.getZ(),
                max.getX() + 1, max.getY() + 1, max.getZ() + 1);
        for (Entity e : level.getEntitiesOfClass(Entity.class, box)) {
            if (!(e instanceof Player)) {
                e.discard();
            }
        }
        try {
            java.lang.reflect.Field f = net.minecraft.world.level.block.RedstoneTorchBlock.class
                    .getDeclaredField("RECENT_TOGGLES");
            f.setAccessible(true);
            ((Map<?, ?>) f.get(null)).clear();
        } catch (ReflectiveOperationException ignored) {
            // burnout history is best-effort; --idle also ages it out
        }
        for (int i = 0; i < idle; i++) {
            tick();
        }
    }

    private static JsonObject runJob(JsonObject job, boolean pace) throws Exception {
        int ticks = job.get("ticks").getAsInt();
        int preroll = job.has("preroll") ? job.get("preroll").getAsInt() : 0;
        String settle = job.has("settle") ? job.get("settle").getAsString() : "place";
        long seed = job.has("seed") ? job.get("seed").getAsLong() : 0;
        CompoundTag tag = TagParser.parseCompoundFully(job.get("snbt").getAsString());
        // Older builds (corpus files): let the game's own datafixers upgrade blocks and block entities.
        int srcVersion = tag.getIntOr("DataVersion", 0);
        if (srcVersion > 0 && srcVersion < SharedConstants.getCurrentVersion().dataVersion().version()) {
            tag = net.minecraft.util.datafix.DataFixTypes.STRUCTURE.updateToCurrentVersion(
                    net.minecraft.util.datafix.DataFixers.getDataFixer(), tag, srcVersion);
        }
        StructureTemplate template = readTemplate(tag);
        net.minecraft.core.Vec3i size = template.getSize();
        BlockPos min = ORIGIN.offset(-MARGIN, -MARGIN, -MARGIN);
        BlockPos max = ORIGIN.offset(size.getX() + MARGIN, size.getY() + MARGIN, size.getZ() + MARGIN);

        // Region must simulate, not merely load: ticket every chunk it touches (see TraceCapture).
        List<ChunkPos> chunks = new ArrayList<>();
        for (int cx = (min.getX() >> 4) - 1; cx <= (max.getX() >> 4) + 1; cx++) {
            for (int cz = (min.getZ() >> 4) - 1; cz <= (max.getZ() >> 4) + 1; cz++) {
                ChunkPos chunk = new ChunkPos(cx, cz);
                if (cx >= (min.getX() >> 4) && cx <= (max.getX() >> 4)
                        && cz >= (min.getZ() >> 4) && cz <= (max.getZ() >> 4)) {
                    chunks.add(chunk);
                }
                level.getChunkSource().addTicketWithRadius(
                        net.minecraft.server.level.TicketType.PLAYER_SIMULATION, chunk, 2);
                level.getChunkSource().addTicketWithRadius(
                        net.minecraft.server.level.TicketType.FORCED, chunk, 2);
                level.setChunkForced(cx, cz, true);
            }
        }
        int warmup = 0;
        while (warmup < 600 && !chunks.stream()
                .allMatch(c -> level.isPositionTickingWithEntitiesLoaded(c.pack()))) {
            tick();
            warmup++;
        }
        for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
            level.setBlock(pos, Blocks.AIR.defaultBlockState(), 2);
        }
        level.getRandom().setSeed(seed);

        StructurePlaceSettings settings = new StructurePlaceSettings();
        if (settle.equals("known_shape")) {
            settings.setKnownShape(true);
        }
        if (!template.placeInWorld(level, ORIGIN, ORIGIN, settings, RandomSource.create(0), 3)) {
            throw new IllegalStateException("failed to place structure");
        }


        Grid grid = new Grid(min, max);
        grid.scan();
        JsonObject out = new JsonObject();
        out.addProperty("mc_version", SharedConstants.getCurrentVersion().name());
        out.addProperty("data_version", SharedConstants.getCurrentVersion().dataVersion().version());
        out.addProperty("source_data_version", srcVersion);
        out.add("origin", ints(ORIGIN.getX(), ORIGIN.getY(), ORIGIN.getZ()));
        out.add("_min", ints(min.getX(), min.getY(), min.getZ()));
        out.add("_max", ints(max.getX(), max.getY(), max.getZ()));
        out.addProperty("warmup_ticks", warmup);
        out.add("placed", grid.dump());
        Map<BlockPos, String[]> inv = containers(min, max);
        out.add("inventory_placed", dumpContainers(inv));

        for (int i = 0; i < preroll; i++) {
            tick();
        }
        grid.scan();
        out.add("initial", grid.dump());
        inv = containers(min, max);
        out.add("inventory_initial", dumpContainers(inv));

        Map<Integer, List<JsonObject>> ops = new HashMap<>();
        for (JsonElement el : job.getAsJsonArray("ops")) {
            JsonObject o = el.getAsJsonObject();
            ops.computeIfAbsent(o.get("tick").getAsInt(), k -> new ArrayList<>()).add(o);
        }
        JsonArray tickArr = new JsonArray();
        JsonArray invEvents = new JsonArray();
        long t0 = System.nanoTime();
        for (int t = 0; t < ticks; t++) {
            for (JsonObject o : ops.getOrDefault(t, List.of())) {
                JsonElement p = o.get("pos");
                BlockPos pos = ORIGIN.offset(p.getAsJsonArray().get(0).getAsInt(),
                        p.getAsJsonArray().get(1).getAsInt(), p.getAsJsonArray().get(2).getAsInt());
                switch (o.get("op").getAsString()) {
                    case "use" -> useBlock(pos);
                    case "set" -> level.setBlock(pos, BuiltInRegistries.BLOCK
                            .getValue(Identifier.parse(o.get("block").getAsString()))
                            .defaultBlockState(), 3);
                    default -> throw new IllegalArgumentException("unknown op " + o);
                }
            }
            tick();
            JsonArray changes = grid.diff();
            if (changes.size() > 0) {
                JsonObject entry = new JsonObject();
                entry.addProperty("tick", t);
                entry.add("changes", changes);
                tickArr.add(entry);
            }
            Map<BlockPos, String[]> now = containers(min, max);
            for (Map.Entry<BlockPos, String[]> e : now.entrySet()) {
                String[] was = inv.get(e.getKey());
                for (int s = 0; s < e.getValue().length; s++) {
                    String a = was != null && s < was.length ? was[s] : "";
                    if (!a.equals(e.getValue()[s])) {
                        JsonObject ev = new JsonObject();
                        ev.addProperty("tick", t);
                        ev.add("pos", ints(e.getKey().getX() - ORIGIN.getX(),
                                e.getKey().getY() - ORIGIN.getY(), e.getKey().getZ() - ORIGIN.getZ()));
                        ev.addProperty("slot", s);
                        ev.addProperty("from", a);
                        ev.addProperty("to", e.getValue()[s]);
                        invEvents.add(ev);
                    }
                }
            }
            inv = now;
        }
        out.addProperty("tick_ms", (System.nanoTime() - t0) / 1e6);
        out.addProperty("ticks", ticks);
        out.add("tick_changes", tickArr);
        out.add("inventory_events", invEvents);
        return out;
    }

    /** Structure NBT -> template. DataVersion is rewritten to the game's, so no datafixing. */
    private static StructureTemplate readTemplate(CompoundTag tag) {
        StructureTemplate template = new StructureTemplate();
        template.load(server.registryAccess().lookupOrThrow(net.minecraft.core.registries.Registries.BLOCK), tag);
        return template;
    }

    /** Block-state grid over the region; identity-compares BlockState (canonical instances). */
    private static final class Grid {
        final BlockPos min, max;
        final int sx, sy, sz;
        final BlockState[] cur, prev;

        Grid(BlockPos min, BlockPos max) {
            this.min = min;
            this.max = max;
            sx = max.getX() - min.getX() + 1;
            sy = max.getY() - min.getY() + 1;
            sz = max.getZ() - min.getZ() + 1;
            cur = new BlockState[sx * sy * sz];
            prev = new BlockState[sx * sy * sz];
        }

        int idx(int x, int y, int z) {
            return ((x * sy) + y) * sz + z;
        }

        void scan() {
            BlockPos.MutableBlockPos p = new BlockPos.MutableBlockPos();
            for (int x = 0; x < sx; x++) {
                for (int y = 0; y < sy; y++) {
                    for (int z = 0; z < sz; z++) {
                        BlockState s = level.getBlockState(p.set(min.getX() + x, min.getY() + y, min.getZ() + z));
                        cur[idx(x, y, z)] = s;
                        prev[idx(x, y, z)] = s;
                    }
                }
            }
        }

        JsonArray dump() {
            JsonArray a = new JsonArray();
            for (int x = 0; x < sx; x++) {
                for (int y = 0; y < sy; y++) {
                    for (int z = 0; z < sz; z++) {
                        BlockState s = cur[idx(x, y, z)];
                        if (!s.isAir()) {
                            JsonArray row = ints(min.getX() + x - ORIGIN.getX(),
                                    min.getY() + y - ORIGIN.getY(), min.getZ() + z - ORIGIN.getZ());
                            row.add(describe(s));
                            a.add(row);
                        }
                    }
                }
            }
            return a;
        }

        /** Changes since the last scan/diff: [x,y,z,from,to]. */
        JsonArray diff() {
            JsonArray a = new JsonArray();
            BlockPos.MutableBlockPos p = new BlockPos.MutableBlockPos();
            for (int x = 0; x < sx; x++) {
                for (int y = 0; y < sy; y++) {
                    for (int z = 0; z < sz; z++) {
                        BlockState s = level.getBlockState(p.set(min.getX() + x, min.getY() + y, min.getZ() + z));
                        int i = idx(x, y, z);
                        if (s != prev[i]) {
                            JsonArray row = ints(min.getX() + x - ORIGIN.getX(),
                                    min.getY() + y - ORIGIN.getY(), min.getZ() + z - ORIGIN.getZ());
                            row.add(describe(prev[i]));
                            row.add(describe(s));
                            a.add(row);
                            prev[i] = s;
                        }
                    }
                }
            }
            return a;
        }
    }

    private static JsonArray ints(int... v) {
        JsonArray a = new JsonArray();
        for (int i : v) {
            a.add(i);
        }
        return a;
    }

    /** `Block{minecraft:stone}[k=v]` -> `minecraft:stone[k=v]`, as TraceCapture does. */
    private static String describe(BlockState state) {
        String text = state.toString();
        int open = text.indexOf('{');
        int close = text.indexOf('}');
        if (open >= 0 && close > open) {
            return text.substring(open + 1, close) + text.substring(close + 1);
        }
        return text;
    }

    private static Map<BlockPos, String[]> containers(BlockPos min, BlockPos max) {
        Map<BlockPos, String[]> out = new HashMap<>();
        for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
            if (level.getBlockEntity(pos) instanceof BaseContainerBlockEntity c) {
                String[] slots = new String[c.getContainerSize()];
                for (int s = 0; s < slots.length; s++) {
                    ItemStack st = c.getItem(s);
                    slots[s] = st.isEmpty() ? ""
                            : st.getCount() + "x " + BuiltInRegistries.ITEM.getKey(st.getItem());
                }
                out.put(pos.immutable(), slots);
            }
        }
        return out;
    }

    private static JsonArray dumpContainers(Map<BlockPos, String[]> inv) {
        JsonArray a = new JsonArray();
        for (Map.Entry<BlockPos, String[]> e : inv.entrySet()) {
            JsonObject o = new JsonObject();
            o.add("pos", ints(e.getKey().getX() - ORIGIN.getX(), e.getKey().getY() - ORIGIN.getY(),
                    e.getKey().getZ() - ORIGIN.getZ()));
            JsonArray slots = new JsonArray();
            for (String s : e.getValue()) {
                slots.add(s);
            }
            o.add("slots", slots);
            a.add(o);
        }
        return a;
    }

    /** Right-click with an empty hand; GameTestHelper.useBlock verbatim (see TraceCapture). */
    private static void useBlock(BlockPos pos) {
        Player player = new Player(level, new GameProfile(UUID.randomUUID(), "oracle-mock-player")) {
            @Override
            public GameType gameMode() {
                return GameType.CREATIVE;
            }

            @Override
            public boolean isClientAuthoritative() {
                return false;
            }
        };
        BlockState state = level.getBlockState(pos);
        InteractionHand hand = InteractionHand.MAIN_HAND;
        BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(pos), Direction.NORTH, pos, true);
        InteractionResult result = state.useItemOn(player.getItemInHand(hand), level, player, hand, hit);
        if (result.consumesAction()) {
            return;
        }
        if (result instanceof InteractionResult.TryEmptyHandInteraction) {
            state.useWithoutItem(level, player, hit);
        }
    }

    private static void deleteRecursively(Path root) {
        if (!Files.exists(root)) {
            return;
        }
        try (var walk = Files.walk(root)) {
            walk.sorted(java.util.Comparator.reverseOrder()).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (Exception ignored) {
                    // best effort
                }
            });
        } catch (Exception ignored) {
            // likewise
        }
    }

    private static String arg(String[] args, String name, String fallback) {
        for (int i = 0; i < args.length - 1; i++) {
            if (args[i].equals(name)) {
                return args[i + 1];
            }
        }
        return fallback;
    }
}
