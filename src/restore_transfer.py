"""Restore checked migration archives using only Python's standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def restore(root=ROOT, *, history=False):
    root = Path(root).resolve()
    manifest = json.loads((root / 'audit/failure50_v1/transfer_manifest.json').read_text(encoding='utf-8'))
    names = ['failure50_transfer.zip']
    if history:
        names.append('historical_experiments.zip')
    for name in names:
        spec = manifest['archives'][name]
        package = root / 'memory_packages' / name
        if sha(package.read_bytes()) != spec['sha256']:
            raise ValueError(f'Archive checksum mismatch: {name}')
        with zipfile.ZipFile(package) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(spec['files']):
                raise ValueError(f'Archive member mismatch: {name}')
            # Validate every member and existing destination before writing.
            for rel, expected in spec['files'].items():
                target = (root / rel).resolve()
                if not target.is_relative_to(root) or Path(rel).parts[0] not in {'memory', 'results'}:
                    raise ValueError(f'Unsafe archive path: {rel}')
                data = archive.read(rel)
                if sha(data) != expected:
                    raise ValueError(f'Member checksum mismatch: {rel}')
                if target.exists() and (not target.is_file() or sha(target.read_bytes()) != expected):
                    raise FileExistsError(f'Refusing to overwrite local data: {rel}')
            if name == 'failure50_transfer.zip':
                frozen = json.loads(archive.read('results/failure50_verified10_v1/manifest.json'))
                for rel, expected in frozen['source_sha256'].items():
                    if sha((root / rel).read_bytes()) != expected:
                        raise ValueError(f'Checkout differs from frozen experiment: {rel}')
            for rel in spec['files']:
                target = root / rel
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:
                        stream.write(archive.read(rel))
        print(f'Restored {name}: {len(spec["files"])} verified files')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true', help='Also restore previous experiments and the legacy corpus')
    args = parser.parse_args()
    restore(history=args.history)


if __name__ == '__main__':
    main()
