"""Focused independent-review regressions: reject unconsumed content and retain init integrity."""
from pathlib import Path
import shutil

import nbtlib as N
import pytest

from mira_mcp.project import Project, ProjectError
from simulation import imports as I
from tests.project.test_import import schematic, snapshot


def sponge_v3():
    payload = N.Compound({'Id': N.String('minecraft:armor_stand'),
                          'Pos': N.List[N.Double]([0.5, 1.0, 0.5]),
                          'Data': N.Compound({'Invisible': N.Byte(1)})})
    block = N.Compound({'Id': N.String('minecraft:chest'), 'Pos': N.IntArray([0, 0, 0]),
                        'Data': N.Compound({'CustomName': N.String('retain or refuse')})})
    root = N.Compound({'Version': N.Int(3), 'DataVersion': N.Int(3953),
                       'Width': N.Short(1), 'Height': N.Short(1), 'Length': N.Short(1),
                       'Offset': N.IntArray([0, 0, 0]),
                       'Blocks': N.Compound({'Palette': N.Compound({'minecraft:chest': N.Int(0)}),
                                             'Data': N.ByteArray([0]), 'BlockEntities': N.List[N.Compound]([])}),
                       'Entities': N.List[N.Compound]([])})
    return root, payload, block


@pytest.mark.parametrize('case', ['misplaced_block', 'extra_entity', 'extra_block', 'wrapper_sibling',
                                  'v3_flat_palette', 'v2_nested_blocks', 'entity_four_pos', 'block_four_pos',
                                  'entity_data_id', 'entity_data_pos', 'block_data_xyz'])
def test_sponge_unconsumed_layout_or_payload_rejected_before_loader(tmp_path, monkeypatch, case):
    root, entity, block = sponge_v3()
    wrapper = {'Schematic': root}
    if case == 'misplaced_block':
        root['BlockEntities'] = N.List[N.Compound]([block])
    elif case == 'wrapper_sibling':
        wrapper['BlockEntities'] = N.List[N.Compound]([block])
    elif case == 'v3_flat_palette':
        root['Palette'] = N.Compound({'minecraft:chest': N.Int(0)})
    elif case == 'v2_nested_blocks':
        root['Version'] = N.Int(2)
    elif case.startswith('entity') or case == 'extra_entity':
        root['Entities'].append(entity)
        if case == 'extra_entity':
            entity['CustomName'] = N.String('must not disappear')
        elif case == 'entity_four_pos':
            entity['Pos'].append(N.Double(4.0))
        elif case == 'entity_data_id':
            entity['Data']['id'] = N.String('minecraft:pig')
        else:
            entity['Data']['Pos'] = N.List[N.Double]([4.0, 5.0, 6.0])
    else:
        root['Blocks']['BlockEntities'].append(block)
        if case == 'extra_block':
            block['CustomName'] = N.String('must not disappear')
        elif case == 'block_four_pos':
            block['Pos'] = N.IntArray([0, 0, 0, 4])
        else:
            block['Data']['x'] = N.Int(99)
    source = tmp_path / f'{case}.schem'
    N.File(wrapper, gzipped=True).save(source)
    import nucleation
    monkeypatch.setattr(nucleation.Schematic, 'load_from_file', lambda *_: pytest.fail('loader reached'))
    with pytest.raises(I.ImportError, match='unsupported Sponge'):
        I._prepare_local(source)


@pytest.mark.parametrize('task', [None, 't0_not'])
def test_force_retaining_imported_build_preserves_and_validates_provenance(tmp_path, task):
    source = schematic(tmp_path)
    p, _ = Project.init('old', tmp_path / 'project', from_litematic=str(source))
    build = (p.dir / 'build.py').read_bytes()
    binding = p.meta['import']
    q, _ = Project.init('new', p.dir, force=True, from_task=task)
    assert q.meta['import'] == binding
    assert q.meta['lenient'] is True
    assert (q.dir / 'build.py').read_bytes() == build
    info = q.import_provenance()
    archive = q.dir / info['archive']
    archive.chmod(0o644)
    archive.write_bytes(b'corrupted after forced init')
    assert 'TAMPERED' in q.status()
    with pytest.raises(ProjectError, match='TAMPERED'):
        q.build()
    before = snapshot(q.dir)
    with pytest.raises(ProjectError, match='TAMPERED'):
        Project.init('again', q.dir, force=True, from_task=task)
    assert snapshot(q.dir) == before


@pytest.mark.parametrize('simulate_cleanup_failure', [False, True])
def test_published_init_is_success_even_when_readonly_backup_cleanup_fails(tmp_path, monkeypatch,
                                                                         simulate_cleanup_failure):
    p, _ = Project.init('old', tmp_path / 'project', from_task='t0_and')
    keep = p.dir / 'unrelated'
    keep.mkdir()
    (keep / 'precious.txt').write_text('do not lose this')
    keep.chmod(0o555)
    real_rmtree = shutil.rmtree
    if simulate_cleanup_failure:
        def fail_backup_cleanup(path, *args, **kwargs):
            path = Path(path)
            if path.name.startswith('.mira-init-') and (path / 'previous').exists():
                raise PermissionError('simulated post-publication cleanup failure')
            return real_rmtree(path, *args, **kwargs)
        monkeypatch.setattr(shutil, 'rmtree', fail_backup_cleanup)
    try:
        q, output = Project.init('new', p.dir, force=True, from_task='t0_not')
        assert q.meta['name'] == 'new' and q.meta['task'] == 't0_not'
        assert (q.dir / 'unrelated/precious.txt').read_text() == 'do not lose this'
        assert (q.dir / 'unrelated').stat().st_mode & 0o777 == 0o555
        if simulate_cleanup_failure:
            assert 'WARNING init published successfully; cleanup incomplete at ' in output
            leftovers = list(tmp_path.glob('.mira-init-*'))
            assert len(leftovers) == 1
            assert (leftovers[0] / 'previous/unrelated/precious.txt').read_text() == 'do not lose this'
    finally:
        # Only test-owned directories, including intentionally retained backup leftovers.
        for directory in tmp_path.rglob('unrelated'):
            directory.chmod(0o755)
        for leftover in tmp_path.glob('.mira-init-*'):
            real_rmtree(leftover)


def test_notes_backticked_qualified_reference_names_exact_spaced_label(tmp_path):
    p, _ = Project.init('memory', tmp_path / 'project', from_litematic=str(schematic(tmp_path)))
    assert p.build().startswith('BUILD OK')
    (p.dir / 'NOTES.md').write_text('Imported selection: `label:control bay`.\n')
    assert 'NOTES OK' in p.notes_check()
    assert '1 label checked' in p.notes_check()
    program = p.dir / 'build.py'
    program.write_text(program.read_text().replace("b.label('control bay',", "b.label('renamed bay',"))
    assert p.build().startswith('BUILD OK')
    stale = p.notes_check()
    assert 'NOTES STALE' in stale and '`label:control bay`' in stale
    assert 'it existed in an earlier revision' in stale
    assert '`label:control`' not in stale


def test_litematic_entity_position_cannot_silently_discard_fourth_component(tmp_path):
    source = schematic(tmp_path)
    data = N.load(source)
    data['Regions']['control bay']['Entities'].append(N.Compound({
        'id': N.String('minecraft:armor_stand'), 'Pos': N.List[N.Double]([0.5, 1.0, 0.5, 4.0])}))
    data.save(source)
    with pytest.raises(I.ImportError, match='unsupported entity id/position'):
        I.prepare(source)
