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
import net.minecraft.world.level.levelgen.structure.BoundingBox;
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
 * job:   {id, snbt, ticks, preroll, settle: place|known_shape, seed, ops: [{tick, op: use|set, pos, block}], origin?: [x,y,z]}
 * Positions are structure-local; the structure's min corner is placed at the job's `origin` (absolute world position;
 * default: spawn x/z, y=100). Jobs with different origins may share a JVM: every origin's region is warmed before the first job.
 */
public final class OracleBatch {
    private OracleBatch() {}

    private static final int MARGIN = 4;
    private static final int MAX_WARMUP = 600;
    private static BlockPos SPAWN_ORIGIN;
    private static BlockPos ORIGIN;     // origin of the job being run (set by runJob)
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
        SPAWN_ORIGIN = new BlockPos(spawn.getX(), 100, spawn.getZ());
        ORIGIN = SPAWN_ORIGIN;
        double bootMs = (System.nanoTime() - t0) / 1e6;
        System.out.printf("boot %.0f ms, default origin %s%n", bootMs, SPAWN_ORIGIN);

        Files.createDirectories(outDir);
        JsonArray jobs = JsonParser.parseString(Files.readString(jobsPath)).getAsJsonArray();
        // Chunk loading must be done for the UNION of all job regions (one union per origin) before the first job runs
        // (see warmChunks).
        Map<BlockPos, BlockPos[]> unions = new java.util.LinkedHashMap<>();
        for (JsonElement el : jobs) {
            try {
                JsonObject job = el.getAsJsonObject();
                BlockPos[] r = region(job);
                unions.merge(jobOrigin(job), r, (a, b) -> new BlockPos[] {min(a[0], b[0]), max(a[1], b[1])});
            } catch (Throwable e) {
                // a malformed job fails on its own below
            }
        }
        if (!unions.isEmpty()) {
            try {
                System.out.println("warm " + warmChunks(new ArrayList<>(unions.values())) + " ticks");
            } catch (Throwable e) {
                System.out.println("warm-up failed: " + e);
            }
        }
        Map<BlockPos, BlockPos[]> dirty = new java.util.LinkedHashMap<>();   // origin -> dirty box
        for (JsonElement el : jobs) {
            JsonObject job = el.getAsJsonObject();
            String id = job.get("id").getAsString();
            long j0 = System.nanoTime();
            JsonObject result;
            BlockPos[] box = null;
            try {
                if (!dirty.isEmpty()) {
                    for (BlockPos[] d : dirty.values()) {
                        wipe(d[0], d[1], 0);
                    }
                    for (int i = 0; i < idle; i++) {
                        tick();
                    }
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
                dirty.merge(ORIGIN, box, (a, b) -> new BlockPos[] {min(a[0], b[0]), max(a[1], b[1])});
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


    private static BlockPos jobOrigin(JsonObject job) {
        if (!job.has("origin") || job.get("origin").isJsonNull()) {
            return SPAWN_ORIGIN;
        }
        JsonArray o = job.getAsJsonArray("origin");
        return new BlockPos(o.get(0).getAsInt(), o.get(1).getAsInt(), o.get(2).getAsInt());
    }

    /** Region of a job: [origin - MARGIN, origin + size + MARGIN], as runJob uses it. */
    private static BlockPos[] region(JsonObject job) throws Exception {
        CompoundTag tag = TagParser.parseCompoundFully(job.get("snbt").getAsString());
        net.minecraft.core.Vec3i size = readTemplate(tag).getSize();
        BlockPos o = jobOrigin(job);
        return new BlockPos[] {o.offset(-MARGIN, -MARGIN, -MARGIN),
                o.offset(size.getX() + MARGIN, size.getY() + MARGIN, size.getZ() + MARGIN)};
    }

    private static List<ChunkPos> chunksOf(BlockPos min, BlockPos max) {
        List<ChunkPos> chunks = new ArrayList<>();
        for (int cx = min.getX() >> 4; cx <= (max.getX() >> 4); cx++) {
            for (int cz = min.getZ() >> 4; cz <= (max.getZ() >> 4); cz++) {
                chunks.add(new ChunkPos(cx, cz));
            }
        }
        return chunks;
    }

    private static boolean chunksTicking(BlockPos min, BlockPos max) {
        return chunksOf(min, max).stream().allMatch(c -> level.isPositionTickingWithEntitiesLoaded(c.pack()));
    }

    /**
     * Ticket, force-load and tick until every chunk of the box simulates; returns the ticks it took and throws if
     * they never do.
     *
     * Root cause of the batch contamination (round-2 audit M1): the old code did this per job. Chunks that an
     * earlier job had not needed and that were ticketed after the server had been ticking never reached ticking in
     * a long-running JVM (chunk tasks stayed pending: 57-636 of them for 5000 ticks, also with real-time pacing, a
     * synchronous getChunk, or no wipe; only the first batch of tickets, added right after boot, ever loaded), and
     * the 600-tick warm-up cap then expired SILENTLY, so the job traced a world whose redstone never ran
     * (t1_xor then t3_adder4: 53 failed checks instead of 8). Why the chunk scheduler stalls was not pinned down;
     * ticketing the union of all job regions once before the first job avoids it for every job order, and a
     * region that still does not tick is now an error, not a wrong trace.
     */
    private static int warmChunks(List<BlockPos[]> boxes) throws Exception {
        for (BlockPos[] b : boxes) {
            BlockPos min = b[0], max = b[1];
            for (int cx = (min.getX() >> 4) - 1; cx <= (max.getX() >> 4) + 1; cx++) {
                for (int cz = (min.getZ() >> 4) - 1; cz <= (max.getZ() >> 4) + 1; cz++) {
                    ChunkPos chunk = new ChunkPos(cx, cz);
                    level.getChunkSource().addTicketWithRadius(
                            net.minecraft.server.level.TicketType.PLAYER_SIMULATION, chunk, 2);
                    level.getChunkSource().addTicketWithRadius(
                            net.minecraft.server.level.TicketType.FORCED, chunk, 2);
                    level.setChunkForced(cx, cz, true);
                }
            }
        }
        int warmup = 0;
        while (warmup < MAX_WARMUP && !boxes.stream().allMatch(b -> chunksTicking(b[0], b[1]))) {
            tick();
            warmup++;
        }
        if (warmup >= MAX_WARMUP) {
            throw new IllegalStateException("chunks of " + boxes.size() + " regions not ticking after " + warmup + " ticks");
        }
        return warmup;
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
        // Scheduled ticks (buttons wait 20-30 ticks, longer than --idle) and queued piston events fire on whatever
        // block the next job puts at the same position, so drop them as /clone does.
        BoundingBox area = BoundingBox.fromCorners(min, max);
        level.getBlockTicks().clearArea(area);
        level.getFluidTicks().clearArea(area);
        level.clearBlockEvents(area);
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
        ORIGIN = jobOrigin(job);
        BlockPos min = ORIGIN.offset(-MARGIN, -MARGIN, -MARGIN);
        BlockPos max = ORIGIN.offset(size.getX() + MARGIN, size.getY() + MARGIN, size.getZ() + MARGIN);

        // Region must simulate, not merely load. warmChunks() normally ran for the union of all jobs already; if this
        // job is not covered (it cannot be, unless the prepass failed) this throws instead of tracing a dead world.
        int warmup = chunksTicking(min, max) ? 0 : warmChunks(java.util.Collections.singletonList(new BlockPos[] {min, max}));
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
