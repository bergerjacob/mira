#!/usr/bin/env python3
"""Bounded, deterministic local import sample; no download, export or behavioural claims."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def selected():
    groups = [('data/raw_schematics', '*.litematic', 13),
              ('data/external/litematicas-leroymilo', '*.litematic', 3),
              ('data/external/nucleation_src/artifacts', '*.schem', 2),
              ('data/external/minecraft-computers', '*.schem', 2),
              ('data/corpus/converted/claudestone/and', '*.litematic', 1),
              ('data/corpus/converted/rouge', '*.litematic', 1),
              ('data/external/nucleation_src/computational_schematics', '*.schem', 2)]
    return [p for directory, glob, limit in groups for p in sorted((ROOT / directory).rglob(glob))[:limit]]


def main():
    from mira_mcp.project import Project
    from simulation.imports import LIMITS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='mira-import-sample-'))
    rows = []
    for i, source in enumerate(selected()):
        start = time.monotonic()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        row = {'source': str(source.relative_to(ROOT)), 'bytes': source.stat().st_size, 'sha256': before}
        try:
            p, _ = Project.init(f'sample-{i}', work / str(i), from_litematic=str(source))
            info = p.import_provenance()
            result = p.build(timeout=30)
            row.update({'verdict': 'imported-and-built' if result.startswith('BUILD OK') else 'build-failed',
                        'source_data_version': info.get('source_data_version'), 'format': info['source_format'],
                        'selection_cells': info['selection_cells'], 'blocks': info['normalized_blocks'],
                        'regions': len(info['regions']), 'payload_counts': info['payload_counts'],
                        'result': result[:1500], 'archive_intact': (p.dir / info['archive']).read_bytes() == source.read_bytes()})
        except Exception as e:
            row.update({'verdict': 'refused', 'error': f'{type(e).__name__}: {e}'})
        row['source_unchanged'] = hashlib.sha256(source.read_bytes()).hexdigest() == before
        row['seconds'] = round(time.monotonic() - start, 3)
        rows.append(row)
        print(row['source'], row['verdict'], row.get('error', f"{row.get('blocks')} blocks"), flush=True)
    report = {'scope': '24-file convenience sample from existing local data; raw admission + normalized-content round trip + sandbox build only. No functional/paste/arbitrary-format/lossless-file claim.',
              'limits': LIMITS, 'workdir': str(work), 'files': rows,
              'summary': {'attempted': len(rows), 'imported_and_built': sum(r['verdict'] == 'imported-and-built' for r in rows),
                          'refused': sum(r['verdict'] == 'refused' for r in rows),
                          'build_failed': sum(r['verdict'] == 'build-failed' for r in rows),
                          'all_sources_unchanged': all(r['source_unchanged'] for r in rows)}}
    Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
