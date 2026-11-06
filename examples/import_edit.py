#!/usr/bin/env python3
"""Reproducible generated import -> ordinary Python edit -> exact-file handover. No downloads/API calls.
Run from repo root: .venv/bin/python examples/import_edit.py /tmp/mira-import-example
The destination must not exist. Outputs (including original bytes and receipts) stay there.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import nbtlib as N
    from env.task import load_task
    from env.runner import fixed_cells
    from mira_mcp.project import Project
    from simulation import export as X
    from tools.oracle.gametest import available

    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix='mira-import-example-'))
    if len(sys.argv) > 1:
        root.mkdir()  # deliberately no --force: never replace someone's work
    task = load_task('t0_not')
    world = dict(fixed_cells(task.contract))
    world.update({tuple(b[:3]): b[3] for b in task.solution})
    source = root / 'generated-inverter.litematic'
    X.save_litematic(world, str(source), name='generated NOT gate')
    raw = N.load(source)
    reg = raw['Regions'].pop('Main')
    raw['Regions']['inverter bay'] = reg
    # Positive world offset and an empty upper selection margin, neither inferred from non-air cells.
    reg['Position'] = N.Compound({k: N.Int(v) for k, v in zip('xyz', (10, 20, 30))})
    reg['Size']['y'] = N.Int(3)
    bits = max(2, (len(reg['BlockStatePalette']) - 1).bit_length())
    words = (8 * 3 * 3 * bits + 63) // 64
    reg['BlockStates'] = N.LongArray(list(reg['BlockStates']) + [0] * (words - len(reg['BlockStates'])))
    raw.save(source)
    original = source.read_bytes()
    p, text = Project.init('import-example', root / 'project', from_task='t0_not', from_litematic=str(source))
    print(text)
    info = p.import_provenance()
    assert (p.dir / info['archive']).read_bytes() == original
    print('ORIGINAL', info['archive'], 'sha256', info['sha256'])
    print(p.build())
    print(p.find('inverter bay'))
    print(p.view(layer=1, max_cols=40))
    # A normal file edit changes only the inert conducting support material, not the physics/kernel.
    program = p.dir / 'build.py'
    text = program.read_text()
    assert text.count('"stone"') == 1
    program.write_text(text.replace('"stone"', '"calcite"'))
    with (p.dir / 'NOTES.md').open('a') as f:
        f.write('\n- `label:inverter bay`: NOT gate; support at (2,1,1) changed stone -> calcite.\n'
                '- Finite truth-table contract only; no universal timing or version compatibility proof.\n')
    print('PYTHON EDIT: stone -> calcite at (2,1,1)')
    assert p.build().startswith('BUILD OK')
    result = p.test()
    print(result)
    assert ': PASS' in result
    # Independent negative expectation: the same kernel must reject an inverted requirement.
    spec = p.dir / 'spec.json'
    saved = spec.read_text()
    negative = json.loads(saved)
    negative['behaviour']['truth_table']['rows'][0]['expect']['Y'] = 0
    spec.write_text(json.dumps(negative))
    bad = p.test()
    assert ': PASS' not in bad
    print('NEGATIVE EXPECTATION: rejected')
    spec.write_text(saved)
    assert ': PASS' in p.test()
    print(p.spec_check())
    print(p.notes_check())
    assert 'NOTES STALE' not in p.notes_check()
    result = p.export()
    print(result)
    assert result.startswith('EXPORT rev')
    _, record = p._export_record()
    sha = record['artifact']['sha256']
    if available():
        behaviour = p.verify_vanilla(every=True, variants=1, workers=1)
        print(behaviour)
        assert 'PASS' in behaviour and 'FAIL' not in behaviour
        paste = p.paste_check()
        print(paste)
        assert 'PASS' in paste and 'FAIL' not in paste
        for rel in ('vanilla_receipt.json', 'paste_receipt.json'):
            receipt = json.loads((p.mira / rel).read_text())
            assert receipt['artifact']['sha256'] == sha
        print('EXACT-FILE RECEIPTS: same SHA256', sha)
    else:
        print('ORACLE UNAVAILABLE: no behaviour/paste certification claimed')
    print(p.status())
    assert source.read_bytes() == original
    assert hashlib.sha256((p.dir / info['archive']).read_bytes()).hexdigest() == info['sha256']
    print('EXAMPLE OK:', root)


if __name__ == '__main__':
    main()
