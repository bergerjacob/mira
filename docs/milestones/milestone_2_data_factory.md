# Milestone 2: Data Factory & Replicator (Retrospective)

## Overview
Milestone 2 focused on the "Hardware Abstraction Layer" of MIRA. The challenge was building a system that could translate abstract schematic data into reliable Minecraft block placements via RCON without triggering anti-spam or crashing the server.

## Engineering Challenges & Solutions

### 1. The RCON Packet Limit
**Problem:** Minecraft's RCON has a packet size limit. When trying to place a chest filled with 27 stacks of items, the serialized NBT string would often exceed this limit, causing the command to fail silently.
**Solution:** Implemented **NBT Splitting** in `simulation/replicator.py`. The system now places the container first, then sends separate `data modify` commands to append items one by one.

### 2. Physics & "Ghost" Updates
**Problem:** Placing redstone components in a live world causes block updates that can break the circuit before it's even finished building (e.g., sand falling, water flowing, or torches popping off).
**Solution:** Integrated Carpet Mod's `/tick freeze` and `/carpet fillUpdates false` into the build loop. This "locks" the world physics during the replication process.

### 3. Coordinate Fidelity
**Problem:** Litematica files often use negative region dimensions and internal offsets.
**Solution:** Refined the `SchematicParser` to normalize all coordinates to a local `(0,0,0)` origin relative to the build anchor, ensuring consistent placement regardless of how the schematic was originally saved.

## Current Status
The Replicator is now the most stable part of the project. It handles Blocks, NBT, and Entities with 100% fidelity compared to the source `.litematic`.
