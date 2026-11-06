"""Bounded raw schematic admission and normalized-content import provenance.

Not a file-format converter: originals are retained, selection/air semantics are sidecars.
Only litematic and Sponge schematic v2/v3 are admitted. No scheduled-tick conversion.
"""
import gzip
import hashlib
import io
import json
import math
import os
import stat
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

MAX_COMPRESSED = 16 << 20
MAX_DECOMPRESSED = 64 << 20
MAX_BLOCKS = 250_000  # declared selection volume, including empty margins
TIMEOUT = 45
LIMITS = {"compressed_bytes": MAX_COMPRESSED, "decompressed_bytes": MAX_DECOMPRESSED,
          "selection_cells": MAX_BLOCKS, "worker_seconds": TIMEOUT,
          "scheduled_ticks": False, "certification_payloads": False,
          "selection_air_semantics": "sidecar only; normalized build stores non-air cells"}


class ImportError(ValueError):
    """An explicit admission/conversion failure (not Python's module ImportError)."""


def raw_tree(data):
    """Strict bounded NBT reader; reject duplicate keys, truncated/negative lengths and depth bombs."""
    from simulation.export import T
    names = {1: 'byte', 2: 'short', 3: 'int', 4: 'long', 5: 'float', 6: 'double',
             7: 'byte_array', 8: 'string', 9: 'list', 10: 'compound', 11: 'int_array', 12: 'long_array'}
    stream = io.BytesIO(data)
    nodes = 0

    def take(n):
        if n < 0 or n > len(data) - stream.tell():
            raise ImportError('malformed NBT: truncated or invalid length')
        return stream.read(n)

    def number(fmt):
        return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]

    def text():
        return take(number('>H')).decode('utf8')

    def read(tag, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 2_000_000:
            raise ImportError('NBT resource limit: depth/nodes')
        if tag not in names:
            raise ImportError(f'malformed NBT tag {tag}')
        if tag in range(1, 7):
            v = number({1: '>b', 2: '>h', 3: '>i', 4: '>q', 5: '>f', 6: '>d'}[tag])
        elif tag == 8:
            v = text()
        elif tag in (7, 11, 12):
            n = number('>i')
            fmt = {7: 'b', 11: 'i', 12: 'q'}[tag]
            buf = take(n * struct.calcsize(fmt)) if n >= 0 else take(-1)
            v = [x[0] for x in struct.iter_unpack('>' + fmt, buf)]
        elif tag == 9:
            et, n = number('>B'), number('>i')
            if n < 0 or n > 2_000_000 or (n and et == 0):
                raise ImportError('NBT resource limit: list length/type')
            v = [read(et, depth + 1) for _ in range(n)]
        else:
            v = {}
            while True:
                et = number('>B')
                if not et:
                    break
                key = text()
                if key in v:
                    raise ImportError(f'duplicate NBT key/region label: {key!r}')
                v[key] = read(et, depth + 1)
        return T(names[tag], v)

    if number('>B') != 10:
        raise ImportError('NBT root is not a compound')
    text()
    root = read(10)
    if stream.read(1):
        raise ImportError('malformed NBT: trailing bytes')
    return root


def plain(t):
    if t.tag == 'compound':
        return {k: plain(v) for k, v in t.v.items()}
    if t.tag == 'list':
        return [plain(v) for v in t.v]
    return t.v


def read_raw(data):
    if len(data) > MAX_COMPRESSED:
        raise ImportError(f'compressed input exceeds {MAX_COMPRESSED} bytes')
    if data[:2] == b'\x1f\x8b':
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            buf = f.read(MAX_DECOMPRESSED + 1)
    else:
        buf = data
    if len(buf) > MAX_DECOMPRESSED:
        raise ImportError(f'decompressed input exceeds {MAX_DECOMPRESSED} bytes')
    return raw_tree(buf), len(buf)


def _state(row):
    from simulation.blockstates import normalize
    props = row.get('Properties', row.get('properties', {}))
    return normalize(row.get('Name', row.get('id', '')) + ('[' + ','.join(f'{k}={v}' for k, v in props.items()) + ']' if props else ''), strict=True)


def preflight(data):
    """Inspect raw, pre-loader content. Returns metadata and raw payloads for post-loader comparison."""
    root, size = read_raw(data)
    tree = plain(root)
    regions, payloads, volume = [], [], 0
    raw_cells = {}
    if 'Regions' in tree:
        fmt, dv = 'litematic', tree.get('MinecraftDataVersion')
        if tree.get('Version') not in (5, 6, 7):
            raise ImportError('unsupported litematic version (supported: 5, 6, 7)')
        if len(tree['Regions']) > 1024:
            raise ImportError('region count exceeds 1024')
        if set(tree) - {'Version', 'SubVersion', 'MinecraftDataVersion', 'Metadata', 'Regions'}:
            raise ImportError('unsupported litematic root fields: ' + ', '.join(sorted(set(tree) - {'Version', 'SubVersion', 'MinecraftDataVersion', 'Metadata', 'Regions'})))
        for name, r in tree['Regions'].items():
            allowed = {'Position', 'Size', 'BlockStatePalette', 'BlockStates', 'Entities', 'TileEntities', 'PendingBlockTicks', 'PendingFluidTicks'}
            if set(r) - allowed:
                raise ImportError(f'unsupported region payload fields in {name}: {sorted(set(r) - allowed)}')
            for key in ('PendingBlockTicks', 'PendingFluidTicks'):
                if r.get(key):
                    raise ImportError(f'unsupported scheduled ticks: {name}/{key} ({len(r[key])})')
            pos, dims = [r['Position'][k] for k in 'xyz'], [r['Size'][k] for k in 'xyz']
            if not all(isinstance(v, int) for v in pos + dims):
                raise ImportError('region position/size must be integer vectors')
            if not all(dims):
                raise ImportError(f'zero-sized region {name!r}')
            lo = [p + min(0, s + 1) for p, s in zip(pos, dims)]
            hi = [p + max(0, s - 1) for p, s in zip(pos, dims)]
            volume += math.prod(abs(s) for s in dims)
            if volume > MAX_BLOCKS:
                raise ImportError(f'selection volume {volume} exceeds {MAX_BLOCKS} cells')
            if not name or len(name) > 256:
                raise ImportError('region name must contain 1..256 characters')
            regions.append({'name': name, 'position': pos, 'signed_size': dims, 'min': lo, 'max': hi})
            palette = [_state(state) for state in r['BlockStatePalette']]
            if not palette:
                raise ImportError('empty block palette')
            bits = max(2, (len(palette) - 1).bit_length())
            count = math.prod(abs(s) for s in dims)
            if root.v['Regions'].v[name].v['BlockStates'].tag != 'long_array':
                raise ImportError('packed BlockStates must be an NBT long array')
            words = [int(w) & ((1 << 64) - 1) for w in r['BlockStates']]
            if len(words) != (count * bits + 63) // 64:
                raise ImportError('malformed packed block array length')
            for i in range(count):
                word, bit = divmod(i * bits, 64)
                idx = words[word] >> bit
                if bit + bits > 64:
                    idx |= words[word + 1] << (64 - bit)
                idx &= (1 << bits) - 1
                if idx >= len(palette):
                    raise ImportError('invalid packed palette index')
                state = palette[idx]
                if state.split('[')[0] in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'):
                    continue
                x = i % abs(dims[0])
                z = i // abs(dims[0]) % abs(dims[2])
                y = i // abs(dims[0] * dims[2])
                p = tuple(a + b for a, b in zip(lo, (x, y, z)))
                if p in raw_cells:
                    raise ImportError('unsupported overlapping non-air regions (ambiguous overwrite)')
                raw_cells[p] = state
            raw = root.v['Regions'].v[name].v
            payloads += [('block', x, lo) for x in raw.get('TileEntities', _empty()).v]
            payloads += [('entity', x, lo) for x in raw.get('Entities', _empty()).v]
    else:
        nested = tree.get('Schematic', tree)
        raw = root.v.get('Schematic', root)
        if 'Schematic' in tree and set(tree) != {'Schematic'}:
            raise ImportError('unsupported Sponge wrapper fields outside Schematic')
        version = nested.get('Version')
        if version not in (2, 3):
            raise ImportError('unsupported format: expected litematic or Sponge schematic v2/v3')
        common = {'Version', 'DataVersion', 'Metadata', 'Width', 'Height', 'Length', 'Offset', 'Entities'}
        allowed = common | ({'Palette', 'PaletteMax', 'BlockData', 'BlockEntities', 'BiomeData', 'BiomePalette'}
                            if version == 2 else {'Blocks', 'Biomes'})
        if set(nested) - allowed:
            raise ImportError(f'unsupported Sponge v{version} root fields/layout: {sorted(set(nested) - allowed)}')
        if any(nested.get(k) for k in ('Biomes', 'BiomeData', 'BiomePalette')):
            raise ImportError('unsupported biome conversion')
        blocks = nested if version == 2 else nested['Blocks']
        if version == 3 and set(blocks) - {'Palette', 'Data', 'BlockEntities'}:
            raise ImportError('unsupported Sponge Blocks payload')
        fmt, dv = 'sponge', nested.get('DataVersion')
        dims = [nested[k] for k in ('Width', 'Height', 'Length')]
        if not all(isinstance(v, int) for v in dims) or min(dims) <= 0:
            raise ImportError('invalid Sponge dimensions')
        pos = list(nested.get('Offset', [0, 0, 0]))
        if len(pos) != 3 or not all(isinstance(v, int) for v in pos):
            raise ImportError('Sponge Offset must be an integer vector')
        regions.append({'name': 'Schematic', 'position': pos, 'signed_size': dims, 'min': pos,
                        'max': [p + s - 1 for p, s in zip(pos, dims)]})
        volume = math.prod(dims)
        from simulation.blockstates import normalize
        if volume > MAX_BLOCKS:
            raise ImportError(f'selection volume {volume} exceeds {MAX_BLOCKS} cells')
        palette = {}
        for state, idx in blocks['Palette'].items():
            if idx in palette or idx < 0:
                raise ImportError('invalid/duplicate Sponge palette index')
            palette[idx] = normalize(state, strict=True)
        packed = blocks.get('Data', blocks.get('BlockData', []))
        indices, value, shift = [], 0, 0
        for byte in packed:
            byte &= 255
            value |= (byte & 127) << shift
            shift += 7
            if shift > 35:
                raise ImportError('malformed Sponge varint')
            if not byte & 128:
                indices.append(value)
                value, shift = 0, 0
                if len(indices) > volume:
                    raise ImportError('Sponge block data exceeds selection volume')
        if shift or len(indices) != volume:
            raise ImportError('malformed Sponge block data length')
        for i, idx in enumerate(indices):
            if idx not in palette:
                raise ImportError('invalid Sponge palette index')
            state = palette[idx]
            if state.split('[')[0] not in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'):
                raw_cells[(pos[0] + i % dims[0], pos[1] + i // (dims[0] * dims[2]),
                           pos[2] + i // dims[0] % dims[2])] = state
        # Sponge payload layouts differ from litematic. Admit only those with a demonstrably equivalent conversion.
        raw_blocks = raw if version == 2 else raw.v['Blocks']
        sponge_payloads = [('sponge_block', x, pos) for x in raw_blocks.v.get('BlockEntities', _empty()).v]
        sponge_payloads += [('sponge_entity', x, pos) for x in raw.v.get('Entities', _empty()).v]
        for kind, entry, _ in sponge_payloads:
            fields = entry.v
            if version == 3:
                if set(fields) != {'Id', 'Pos', 'Data'} or fields['Data'].tag != 'compound':
                    raise ImportError('unsupported Sponge v3 payload wrapper fields/layout; expected Id, Pos, Data only')
                reserved = {'id', 'Id', 'Pos'} | ({'x', 'y', 'z'} if kind == 'sponge_block' else set())
                if reserved & set(fields['Data'].v):
                    raise ImportError('unsupported Sponge payload Data conflicts with wrapper position/id')
            elif 'Data' in fields or {'Id', 'id'} <= set(fields):
                raise ImportError('unsupported Sponge v2 payload wrapper fields/layout')
            position = fields.get('Pos')
            if position is None or position.tag not in ('list', 'int_array') or len(position.v) != 3:
                raise ImportError('unsupported Sponge payload position: expected exactly three components')
        payloads += sponge_payloads
    if volume > MAX_BLOCKS:
        raise ImportError(f'selection volume {volume} exceeds {MAX_BLOCKS} cells')
    if not regions:
        raise ImportError('schematic has no regions')
    return {'source_format': fmt, 'source_data_version': dv, 'regions': regions, 'selection_cells': volume,
            'decompressed_bytes': size, 'payload_counts': {'entities': sum('entity' in k for k, _, _ in payloads),
                                                         'block_entities': sum('block' in k for k, _, _ in payloads)},
            'capability_limits': LIMITS, '_raw_cells': raw_cells}, payloads


def source_offset(meta, loader_offset):
    """Sponge Offset is selection provenance; Nucleation exposes Sponge cells in local coordinates."""
    cells = meta['_raw_cells']
    if cells:
        return tuple(min(p[i] for p in cells) for i in range(3))
    if meta['source_format'] == 'sponge':
        return tuple(a + b for a, b in zip(loader_offset, meta['regions'][0]['position']))
    return tuple(loader_offset)


def _check_cells(raw_cells, blocks, offset):
    if raw_cells is None:
        return
    expected = {tuple(p[i] - offset[i] for i in range(3)): state for p, state in raw_cells.items()}
    if expected != {p: state for p, (state, _) in blocks.items()}:
        raise ImportError('unsupported block conversion: raw states/positions differ from normalized loader content')


def _empty():
    from simulation.export import T
    return T('list', [])


def _check_payloads(payloads, blocks, entities, offset):
    """Compare raw typed payloads, not just loader output against itself. Never silently drop orphans."""
    from simulation.export import T, from_snbt
    expected_blocks, expected_entities = {}, []
    for kind, raw, origin in payloads:
        v = dict(raw.v)
        if kind.startswith('sponge'):
            # Sponge v3 stores arbitrary NBT under Data; v2 may store it inline.
            pos = v.pop('Pos', None)
            eid = v.pop('Id', v.pop('id', None))
            if 'Data' in v:
                v = dict(v['Data'].v)
            if pos is not None:
                if 'block' in kind:
                    for k, n in zip('xyz', pos.v):
                        v[k] = T('int', n.v if isinstance(n, T) else n)
                else:
                    v['Pos'] = pos
            if eid is not None:
                v['id'] = eid
        if 'block' in kind:
            try:
                p = tuple(int(v.pop(k).v) + a - o for k, a, o in zip('xyz', origin, offset))
            except (KeyError, TypeError, ValueError):
                raise ImportError('unsupported block entity position') from None
            if p in expected_blocks:
                raise ImportError('overlapping/duplicate block entity payloads')
            expected_blocks[p] = T('compound', v)
        else:
            try:
                pos = v.pop('Pos').v
                if len(pos) != 3:
                    raise ValueError('expected exactly three position components')
                p = tuple(float(n.v) + a - o for n, a, o in zip(pos, origin, offset))
                eid = v.pop('id').v
                if len(p) != 3 or not all(math.isfinite(n) for n in p):
                    raise ValueError('invalid position')
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ImportError('unsupported entity id/position') from None
            expected_entities.append((p, eid, T('compound', v)))
    got_blocks = {p: from_snbt(nbt) for p, (_, nbt) in blocks.items() if nbt}
    got_entities = [(tuple(e[:3]), e[3], from_snbt(e[4]) if e[4] else T('compound', {})) for e in entities]
    def typed_key(t):
        if t.tag == 'compound':
            return t.tag, tuple((k, typed_key(v)) for k, v in sorted(t.v.items()))
        if t.tag == 'list':
            return t.tag, tuple(typed_key(v) for v in t.v)
        return t.tag, tuple(t.v) if t.tag.endswith('_array') else t.v

    def entity_key(e):
        return e[0], e[1], typed_key(e[2])

    if expected_blocks != got_blocks or sorted(expected_entities, key=entity_key) != sorted(got_entities, key=entity_key):
        raise ImportError('unsupported payload conversion: raw block-entity/entity NBT differs from normalized loader content')


def _prepare_local(path):
    from simulation import builder as B
    data = Path(path).read_bytes()
    meta, payloads = preflight(data)
    import nucleation
    sc = nucleation.Schematic.load_from_file(str(path))
    blocks, entities, loader_offset = B.load_normalized(sc, strict_states=True)
    offset = source_offset(meta, loader_offset)
    _check_payloads(payloads, blocks, entities, offset)
    _check_cells(meta.pop('_raw_cells'), blocks, offset)
    meta['loader_normalization_offset'] = list(loader_offset)
    text = B._print_program(None, blocks, entities, loader_offset, 3)  # bounded flat printer; no expensive motif search
    if len(text.encode('utf8')) > MAX_DECOMPRESSED:
        raise ImportError('generated Python exceeds 64 MiB')
    B._verify_roundtrip(text, blocks, entities)
    for r in meta['regions']:
        r['normalized_min'] = [p - o for p, o in zip(r['min'], offset)]
        r['normalized_max'] = [p - o for p, o in zip(r['max'], offset)]
    # Region names are exact Python strings, not identifiers. Empty margins are queryable, never inferred from blocks.
    text += '\n' + '\n'.join(f'# source region {r["name"]!r}: {r["min"]}..{r["max"]}' for r in meta['regions']) + '\n'
    # Add inside build(), before the trailing comments.
    lines = text.splitlines()
    idx = next(i for i, line in enumerate(lines) if line == 'def build(b):') + 1
    lines[idx:idx] = [f'    b.label({r["name"]!r}, {tuple(r["normalized_min"])!r}, {tuple(r["normalized_max"])!r})' for r in meta['regions']]
    meta.update({'sha256': hashlib.sha256(data).hexdigest(), 'source_name': Path(path).name,
                 'compressed_bytes': len(data), 'normalization_offset': list(offset),
                 'normalized_blocks': len(blocks), 'comparison': 'normalized non-air states, typed NBT and entities; not original bytes/air/selections/ticks/metadata'})
    return {'program': '\n'.join(lines) + '\n', 'metadata': meta}


def prepare(path):
    """Snapshot input bytes, then preflight/load/print/verify in one memory/CPU/wall-time bounded worker."""
    path = Path(path)
    # Reject pipes/devices; input acquisition itself must not wait on an untrusted stream.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ImportError('unsupported input: expected a regular file, not a pipe/device')
        if info.st_size > MAX_COMPRESSED:
            raise ImportError(f'compressed input exceeds {MAX_COMPRESSED} bytes')
        data = f.read(MAX_COMPRESSED + 1)
    if len(data) > MAX_COMPRESSED:
        raise ImportError(f'compressed input exceeds {MAX_COMPRESSED} bytes')
    with tempfile.TemporaryDirectory(prefix='mira_import_') as tmp:
        snap = Path(tmp) / path.name
        snap.write_bytes(data)
        boot = 'import sys; sys.path.insert(0, ' + repr(str(Path(__file__).resolve().parents[1])) + '); from simulation.imports import _worker; _worker()'
        try:
            r = subprocess.run([sys.executable, '-s', '-P', '-c', boot, str(snap)], capture_output=True,
                               text=True, timeout=TIMEOUT + 3, cwd=tmp,
                               env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
        except subprocess.TimeoutExpired:
            raise ImportError(f'import resource limit: worker exceeded {TIMEOUT}s') from None
        if r.returncode or not r.stdout:
            raise ImportError(f'import worker resource/fatal failure (exit {r.returncode}): {r.stderr[-300:]}')
        try:
            result = json.loads(r.stdout)
        except ValueError:
            raise ImportError('invalid import worker output: ' + r.stdout[-300:]) from None
        if 'error' in result:
            raise ImportError(result['error'])
    result['original'] = data
    result['metadata']['source_name'] = path.name
    return result


def _worker():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    try:
        result = _prepare_local(sys.argv[1])
    except Exception as e:
        result = {'error': f'{type(e).__name__}: {e}'}
    print(json.dumps(result))
