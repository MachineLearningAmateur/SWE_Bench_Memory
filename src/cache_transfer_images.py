"""Pull and tag the exact official task images recorded for this experiment."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check existing images without pulling or tagging')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / 'results/failure50_verified10_v1/image_identities.json'
    for row in json.loads(path.read_text(encoding='utf-8')):
        if not args.check:
            reference = row['repo_digests'][0]
            subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', reference], check=True)
        else:
            reference = row['image']
        identity = subprocess.check_output(['docker', 'image', 'inspect', reference, '--format', '{{.Id}}'], text=True).strip()
        if identity != row['id']:
            raise ValueError(f'Image identity mismatch: {row["image"]}')
        if not args.check:
            subprocess.run(['docker', 'tag', reference, row['image']], check=True)
        print(f'Verified {row["image"]}', flush=True)


if __name__ == '__main__':
    main()
