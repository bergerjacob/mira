"""Generated tiny raw-NBT regressions: admission is before normalization, not loader self-comparison."""
import gzip
import hashlib
import json

import nbtlib as N
import pytest

from mira_mcp.project import Project, ProjectError
from simulation import builder as B
from simulation import imports as I
from simulation import export as X


def region(pos=(10, 20, 30), size=(3, 1, 1), state='minecraft:stone'):
    # Three cells, only the middle one occupied; both selection margins must survive in metadata.
    return N.Compound({
        'Position': N.Compound({k: N.Int(v) for k, v in zip('xyz', pos)}),
        'Size': N.Compound({k: N.Int(v) for k, v in zip('xyz', size)}),
        'BlockStatePalette': N.List[N.Compound]([N.Compound({'Name': N.String('minecraft:air')}),
                                               N.Compound({'Name': N.String(state)})]),
        'BlockStates': N.LongArray([4]), 'Entities': N.List[N.Compound]([]),
        'TileEntities': N.List[N.Compound]([]), 'PendingBlockTicks': N.List[N.Compound]([]),
        'PendingFluidTicks': N.List[N.Compound]([])})


def schematic(tmp_path, regions=None, filename='input.litematic'):
    p = tmp_path / filename
    N.File({'Version': N.Int(6), 'SubVersion': N.Int(1), 'MinecraftDataVersion': N.Int(3953),
            'Metadata': N.Compound({'Name': N.String('generated import fixture')}),
            'Regions': N.Compound(regions or {'control bay': region()})}, gzipped=True).save(p)
    return p


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode) for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('key', ['PendingBlockTicks', 'PendingFluidTicks'])
def test_ticks_rejected_before_loader_or_normalized_comparison(tmp_path, monkeypatch, key):
    r = region()
    r[key] = N.List[N.Compound]([N.Compound({'i': N.String('minecraft:water'), 't': N.Int(7)})])
    p = schematic(tmp_path, {'scheduled': r})
    import nucleation
    monkeypatch.setattr(nucleation.Schematic, 'load_from_file', lambda *_: pytest.fail('loader reached'))
    with pytest.raises(I.ImportError, match='unsupported scheduled ticks'):
        I._prepare_local(p)
    with pytest.raises(B.DecompileError, match='unsupported scheduled ticks'):
        B.decompile(str(p))


def test_multiregion_negative_sizes_offsets_and_empty_margins_are_queryable(tmp_path):
    source = schematic(tmp_path, {'control bay': region(), 'negative bank': region((20, -4, 8), (-3, 1, 1)),
                                  'empty reserve': region((30, 0, 0))})
    d = N.load(source)
    d['Regions']['empty reserve']['BlockStates'] = N.LongArray([0])
    d.save(source)
    p, _ = Project.init('regions', tmp_path / 'project', from_litematic=str(source))
    assert p.build().startswith('BUILD OK')
    info = p.import_provenance()
    assert info['normalization_offset'] == [11, -4, 8]
    assert info['source_data_version'] == 3953
    assert info['regions'][1]['signed_size'] == [-3, 1, 1]
    assert info['regions'][1]['min'] == [18, -4, 8]
    labels = p.rev().data['labels']
    assert labels['control bay'] == {'min': [-1, 24, 22], 'max': [1, 24, 22]}
    assert labels['negative bank'] == {'min': [7, 0, 0], 'max': [9, 0, 0]}
    assert labels['empty reserve'] == {'min': [19, 4, -8], 'max': [21, 4, -8]}
    assert 'label negative bank' in p.find('negative bank')
    assert 'control bay' in p.overview()
    assert source.read_bytes() == (p.dir / info['archive']).read_bytes()


@pytest.mark.parametrize('state', ['minecraft:not_a_real_block', 'minecraft:stone[bogus=true]',
                                  'minecraft:repeater[delay=9]'])
def test_unknown_and_illegal_states_refused(tmp_path, state):
    source = schematic(tmp_path, {'invalid': region(state=state)})
    with pytest.raises(I.ImportError, match='StateError'):
        I.prepare(source)


def test_packed_state_indices_and_overlapping_cells_refused(tmp_path):
    r = region()
    r['BlockStates'] = N.LongArray([12])  # palette index 3 does not exist
    with pytest.raises(I.ImportError, match='palette index'):
        I.prepare(schematic(tmp_path, {'bad index': r}))
    with pytest.raises(I.ImportError, match='overlapping non-air'):
        I.prepare(schematic(tmp_path, {'one': region(), 'two': region()}))


@pytest.mark.parametrize('payload', ['entity', 'block'])
def test_raw_payloads_that_loader_would_drop_fail_explicitly(tmp_path, payload):
    r = region()
    if payload == 'entity':
        r['Entities'].append(N.Compound({'CustomName': N.String('missing id and position')}))
    else:
        r['TileEntities'].append(N.Compound({'id': N.String('minecraft:chest'),
                                            'x': N.Int(100), 'y': N.Int(0), 'z': N.Int(0)}))
    with pytest.raises(I.ImportError, match='unsupported'):
        I.prepare(schematic(tmp_path, {'payload': r}))


def test_supported_nbt_and_entities_survive_edit_but_cannot_be_certified(tmp_path):
    # Include positive source offset: payload positions are region-local, not global.
    b = B.compile('from simulation.builder import *\ndef build(b):\n'
                  ' b.set((0,0,0),chest(),nbt=items(("minecraft:stone",3)))\n'
                  ' b.entity("minecraft:armor_stand",(1.25,1.0,0.5),\'{CustomName:"kept",Invisible:1b}\')\n',
                  sandbox=False, validate=False)
    source = tmp_path / 'payload.litematic'
    b.save(str(source))
    raw = N.load(source)
    raw['Regions']['Main']['Position'] = N.Compound({k: N.Int(v) for k, v in zip('xyz', (10, 20, 30))})
    raw.save(source)
    p, _ = Project.init('payload', tmp_path / 'project', from_litematic=str(source))
    assert p.build().startswith('BUILD OK')
    before = p.rev().data
    program = p.dir / 'build.py'
    program.write_text(program.read_text().replace('def build(b):', 'def build(b):\n    b.set((3,0,0),"stone")'))
    assert p.build().startswith('BUILD OK')
    after = p.rev().data
    assert before['entities'] == after['entities']
    assert [row for row in before['blocks'] if row[4]] == [row for row in after['blocks'] if row[4]]
    assert p.export().startswith('EXPORT REFUSED')
    assert 'NBT-free' in p.export()
    assert not (p.mira / 'exports/last_export.json').exists()


@pytest.mark.parametrize('kind', ['missing', 'malformed', 'ticks', 'unknown', 'missing-program', 'invalid-task'])
def test_failed_force_init_is_byte_for_byte_atomic(tmp_path, kind):
    p, _ = Project.init('original', tmp_path / 'project', from_task='t0_and')
    (p.dir / 'NOTES.md').write_text('precious memory')
    (p.dir / 'modules/precious.py').write_text('# retain me')
    old = snapshot(p.dir)
    source = schematic(tmp_path)
    if kind == 'missing':
        source.unlink()
    elif kind == 'malformed':
        source.write_bytes(b'not an NBT file')
    elif kind == 'ticks':
        d = N.load(source)
        d['Regions']['control bay']['PendingFluidTicks'].append(N.Compound({'t': N.Int(1)}))
        d.save(source)
    elif kind == 'unknown':
        source = schematic(tmp_path, {'bad': region(state='minecraft:unknown')})
    kwargs = {'from_task': 't0_not', 'from_litematic': str(source)}
    if kind == 'missing-program':
        kwargs = {'from_task': 't0_not', 'program': str(tmp_path / 'missing.py')}
    if kind == 'invalid-task':
        kwargs = {'from_task': 'not_a_task', 'from_litematic': str(source)}
    with pytest.raises((ValueError, OSError, ProjectError)):
        Project.init('replacement', p.dir, force=True, **kwargs)
    assert snapshot(p.dir) == old
    assert not list(tmp_path.glob('.mira-init-*'))


def test_original_is_snapshotted_and_tampering_is_detected(tmp_path):
    source = schematic(tmp_path)
    original = source.read_bytes()
    p, _ = Project.init('archive', tmp_path / 'project', from_litematic=str(source))
    info = p.import_provenance()
    archive = p.dir / info['archive']
    assert archive.read_bytes() == original
    assert info['sha256'] == hashlib.sha256(original).hexdigest()
    assert not archive.stat().st_mode & 0o222
    source.write_bytes(b'source changed after admission')
    assert p.build().startswith('BUILD OK')
    assert archive.read_bytes() == original
    archive.chmod(0o644)
    archive.write_bytes(b'tampered archive')
    assert 'TAMPERED' in p.status()
    with pytest.raises(ProjectError, match='TAMPERED'):
        p.build()
    with pytest.raises(ProjectError, match='TAMPERED'):
        p.export()


def test_sidecar_tampering_is_detected_and_moving_project_works(tmp_path):
    source = schematic(tmp_path)
    p, _ = Project.init('portable', tmp_path / 'project', from_litematic=str(source))
    moved = tmp_path / 'moved'
    p.dir.rename(moved)
    p = Project(moved)
    assert p.import_provenance()['regions'][0]['name'] == 'control bay'
    path = p.mira / 'import.json'
    d = json.loads(path.read_text())
    d['source_data_version'] = 999
    path.write_text(json.dumps(d))
    assert 'TAMPERED' in p.status()


def test_actual_versions_and_mismatch_are_recorded_not_relabelled(tmp_path):
    source = schematic(tmp_path)
    p, _ = Project.init('versions', tmp_path / 'project', from_litematic=str(source))
    assert p.build().startswith('BUILD OK')
    output = p.export(nbt=str(tmp_path / 'structure.nbt'))
    assert output.startswith('EXPORT rev') and 'DataVersion mismatch' in output
    rec = json.loads((p.mira / 'exports/last_export.json').read_text())
    assert rec['data_versions']['source'] == 3953
    assert rec['data_version_mismatch'] is True
    assert rec['data_versions']['litematic'] == X.read_nbt(rec['litematic'])['MinecraftDataVersion']
    assert rec['data_versions']['structure'] == X.read_nbt(rec['nbt'])['DataVersion']
    assert rec['data_versions']['oracle'] == 4903
    assert 'MISMATCH' in p.status()
    assert N.load(source)['MinecraftDataVersion'] == 3953


@pytest.mark.parametrize('src', [
    'def build(b):\n b.label("same",(0,0,0))\n b.label("same",(1,0,0))\n',
    '@part\ndef child(p):\n p.label("same",(0,0,0))\ndef build(b):\n b.label("inst.same",(0,0,0))\n b.place(child,name="inst")\n'])
def test_duplicate_authored_or_instanced_labels_never_overwrite(src):
    b = B.compile('from simulation.builder import *\n' + src, sandbox=False)
    assert not b.ok and 'duplicate label' in str(b.errors)


def test_duplicate_raw_region_labels_refused(tmp_path):
    source = schematic(tmp_path, {'first': region(), 'other': region((20, 20, 30))})
    raw = gzip.decompress(source.read_bytes()).replace(b'other', b'first')
    source.write_bytes(gzip.compress(raw))
    with pytest.raises(I.ImportError, match='duplicate NBT key/region label'):
        I.prepare(source)


def test_input_resource_limits_are_explicit(tmp_path, monkeypatch):
    source = tmp_path / 'large.litematic'
    source.write_bytes(b'x' * 100)
    compressed, decompressed = I.MAX_COMPRESSED, I.MAX_DECOMPRESSED
    monkeypatch.setattr(I, 'MAX_COMPRESSED', 99)
    with pytest.raises(I.ImportError, match='compressed input exceeds'):
        I.prepare(source)
    monkeypatch.setattr(I, 'MAX_DECOMPRESSED', 99)
    with pytest.raises(I.ImportError, match='decompressed input exceeds'):
        I.read_raw(gzip.compress(b'x' * 100))
    monkeypatch.setattr(I, 'MAX_COMPRESSED', compressed)
    monkeypatch.setattr(I, 'MAX_DECOMPRESSED', decompressed)
    r = region(size=(I.MAX_BLOCKS + 1, 1, 1))
    with pytest.raises(I.ImportError, match='selection volume'):
        I._prepare_local(schematic(tmp_path, {'enormous': r}))
    # Tiny malicious declarations are bounded before list allocation.
    with pytest.raises(I.ImportError, match='list length'):
        I.raw_tree(b'\x0a\x00\x00\x09\x00\x01x\x0a\x7f\xff\xff\xff\x00')


@pytest.mark.parametrize('version', [2, 3])
def test_sponge_state_only_fixture_and_source_offset(tmp_path, version):
    source = tmp_path / 'tiny.schem'
    root = {'Version': N.Int(version), 'DataVersion': N.Int(3953), 'Width': N.Short(3), 'Height': N.Short(1),
            'Length': N.Short(1), 'Offset': N.IntArray([10, 20, 30]),
            'Palette': N.Compound({'minecraft:air': N.Int(0), 'minecraft:stone': N.Int(1)}),
            'PaletteMax': N.Int(2), 'BlockData': N.ByteArray([0, 1, 0]),
            'BlockEntities': N.List[N.Compound]([])}
    if version == 3:
        root['Blocks'] = N.Compound({'Palette': root.pop('Palette'), 'Data': root.pop('BlockData'),
                                     'BlockEntities': root.pop('BlockEntities')})
        root.pop('PaletteMax')
        root = {'Schematic': N.Compound(root)}
    N.File(root, gzipped=True).save(source)
    p, _ = Project.init('sponge', tmp_path / 'project', from_litematic=str(source))
    assert p.import_provenance()['source_format'] == 'sponge'
    assert p.import_provenance()['normalization_offset'] == [11, 20, 30]
    assert p.import_provenance()['loader_normalization_offset'] == [1, 0, 0]
    assert p.build().startswith('BUILD OK')
    assert p.rev().data['labels']['Schematic'] == {'min': [-1, 0, 0], 'max': [1, 0, 0]}


def test_worker_timeout_is_explicit_and_does_not_publish(tmp_path, monkeypatch):
    import subprocess
    source = schematic(tmp_path)

    def expired(*args, **kwargs):
        assert kwargs['timeout'] == I.TIMEOUT + 3
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(I.subprocess, 'run', expired)
    with pytest.raises(I.ImportError, match='worker exceeded'):
        Project.init('timeout', tmp_path / 'project', from_litematic=str(source))
    assert not (tmp_path / 'project').exists()


def test_nonregular_stream_and_unknown_payload_field_refused(tmp_path):
    import os
    fifo = tmp_path / 'pipe.litematic'
    os.mkfifo(fifo)
    with pytest.raises(I.ImportError, match='regular file'):
        I.prepare(fifo)
    r = region()
    r['FuturePayload'] = N.List[N.Compound]([N.Compound({'data': N.Int(1)})])
    with pytest.raises(I.ImportError, match='unsupported region payload fields'):
        I.prepare(schematic(tmp_path, {'future payload': r}))
