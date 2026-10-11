import hashlib
import json
import zipfile

import pytest

from src.restore_transfer import restore


def fixture_package(tmp_path, *, member='memory/example.txt'):
    (tmp_path / 'memory_packages').mkdir()
    (tmp_path / 'audit/failure50_v1').mkdir(parents=True)
    (tmp_path / 'source.py').write_bytes(b'frozen\n')
    frozen = {'source_sha256': {'source.py': hashlib.sha256(b'frozen\n').hexdigest()}}
    entries = {member: b'evidence', 'results/failure50_verified10_v1/manifest.json': json.dumps(frozen).encode()}
    package = tmp_path / 'memory_packages/failure50_transfer.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    spec = {'sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}}
    (tmp_path / 'audit/failure50_v1/transfer_manifest.json').write_text(json.dumps({'archives': {package.name: spec}}))
    return package


def test_restore_is_verified_idempotent_and_refuses_changed_destination(tmp_path):
    fixture_package(tmp_path)
    restore(tmp_path)
    restore(tmp_path)
    target = tmp_path / 'memory/example.txt'
    assert target.read_bytes() == b'evidence'
    target.write_bytes(b'local change')
    with pytest.raises(FileExistsError):
        restore(tmp_path)
    assert target.read_bytes() == b'local change'


def test_corrupted_package_is_rejected_before_extraction(tmp_path):
    package = fixture_package(tmp_path)
    package.write_bytes(package.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='checksum'):
        restore(tmp_path)
    assert not (tmp_path / 'memory').exists()


def test_path_escape_and_changed_frozen_code_are_rejected(tmp_path):
    fixture_package(tmp_path, member='../outside.txt')
    with pytest.raises(ValueError, match='Unsafe'):
        restore(tmp_path)


def test_changed_frozen_code_is_rejected_before_extraction(tmp_path):
    fixture_package(tmp_path)
    (tmp_path / 'source.py').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Checkout differs'):
        restore(tmp_path)
    assert not (tmp_path / 'memory').exists()
