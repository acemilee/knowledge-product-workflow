"""核验已批准的固定 Ruff 基线；保留重复计数，拒绝新增问题。"""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / '.github/ruff-baseline.json'


def signature(issue):
    path = Path(issue['filename']).resolve()
    relative = path.relative_to(ROOT).as_posix()
    lines = path.read_text(encoding='utf-8').splitlines()
    row = issue['location']['row']
    return (relative, issue['code'], issue['message'], lines[row - 1].strip())


def new_findings(current, baseline):
    return Counter(current) - Counter(baseline)


def main():
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    expected = [tuple(item) for item in baseline['signatures']]
    if baseline['count'] != 19 or len(expected) != 19:
        raise ValueError('固定基线必须保持已批准的19项，不能自动刷新')
    result = subprocess.run(
        [sys.executable, '-m', 'ruff', 'check', 'src', 'tests', '--output-format', 'json'],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=120, check=False,
    )
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr)
        return result.returncode or 2
    issues = json.loads(result.stdout)
    current = [signature(issue) for issue in issues]
    added = new_findings(current, expected)
    print(f'Ruff：当前{len(current)}项；固定历史基线19项；新增{sum(added.values())}项')
    for item, count in sorted(added.items()):
        print(f'{item[0]}: {item[1]} {item[2]} (x{count})')
    return 1 if added else 0


if __name__ == '__main__':
    raise SystemExit(main())
