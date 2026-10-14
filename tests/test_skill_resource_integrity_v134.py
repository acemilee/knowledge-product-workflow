"""真正资源字节及全部镜像与清单一致，不能只验共享Markdown。"""
import hashlib
import json
import os
from pathlib import Path


def test_all_shared_and_skill_resources_match_manifest_bytes():
    base = Path(__file__).parents[1] / 'integrations/cc-switch'
    if candidate := os.environ.get('KP_TEST_CANDIDATE_RECORD'):
        base = Path(json.loads(Path(candidate).read_text(encoding='utf-8'))['skill_package'])
    manifest = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    errors = []
    for row in manifest['shared_resources']:
        actual = hashlib.sha256((base / row['path']).read_bytes()).hexdigest()
        if actual != row['sha256']:
            errors.append(row['path'])
    for skill in manifest['skills']:
        for row in skill.get('resources', []):
            actual = hashlib.sha256((base / 'skills' / skill['name'] / row['path']).read_bytes()).hexdigest()
            if actual != row['sha256']:
                errors.append(skill['name'] + '/' + row['path'])
    assert not errors, '资源清单哈希未匹配实际文件：' + '、'.join(errors)
