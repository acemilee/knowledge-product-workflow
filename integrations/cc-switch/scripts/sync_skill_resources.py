"""同步包内编排契约镜像与 manifest 哈希；不访问项目外安装目录。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


def main() -> None:
    package = Path(__file__).resolve().parents[1]
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = package / "references" / "orchestration-contract.md"
    content = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    source.write_text(content, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest["shared_resources"] = [{"path": "references/orchestration-contract.md", "sha256": digest}]
    humanizer_resources = []
    for name in ("humanizer-zh.md", "humanizer-zh.LICENSE"):
        relative = f"references/{name}"
        payload = (package / relative).read_bytes()
        resource = {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}
        humanizer_resources.append(resource)
        manifest["shared_resources"].append(resource)
    illustration_source = package.parents[1] / "src" / "knowledge_product_workflow" / "templates" / "illustrations"
    illustration_resources = []
    for name in ("style.json", "layouts.json", "style-anchor.png", "style-v2.json", "palettes-v2.json", "style-anchor-neutral.png", "process-overview-v2.json"):
        relative = f"assets/illustrations/{name}"
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".png"):
            shutil.copyfile(illustration_source / name, destination)
        else:
            destination.write_text((illustration_source / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        payload = destination.read_bytes()
        resource = {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}
        illustration_resources.append(resource)
        manifest["shared_resources"].append(resource)
    for entry in manifest["skills"]:
        directory = package / "skills" / entry["name"]
        mirror = directory / "references" / "orchestration-contract.md"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(content, encoding="utf-8", newline="\n")
        skill_path = directory / "SKILL.md"
        skill = skill_path.read_text(encoding="utf-8").replace("../../references/orchestration-contract.md", "references/orchestration-contract.md")
        skill = re.sub(r'(?m)^  version: "[^"]+"$', f'  version: "{manifest["version"]}"', skill)
        skill_path.write_text(skill, encoding="utf-8", newline="\n")
        entry["resources"] = [{"path": "references/orchestration-contract.md", "sha256": digest}]
        if entry["name"] == "kp-chinese-humanize-editor":
            for resource in humanizer_resources:
                destination = directory / resource["path"]
                shutil.copyfile(package / resource["path"], destination)
                entry["resources"].append(resource)
        if entry["name"] == "kp-diagram-designer":
            for resource in illustration_resources:
                destination = directory / resource["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package / resource["path"], destination)
                entry["resources"].append(resource)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"已同步 {len(manifest['skills'])} 个独立 Skill 包的编排契约。")


if __name__ == "__main__":
    main()
