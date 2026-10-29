"""按《前后端接口约定》校验返回体和请求体。

用法：python validate.py <文件或目录>...
- 返回体按其中的 kind 字段选择 schema。
- 请求体没有 kind，按文件名选择：paper-user-patch.json、paper-user-patch.清空评分.json 都用 paper-user-patch。
- 目录会递归检查其中所有 .json 文件。
- 除 schema 外，还检查几条 schema 写不出来的规则（见 extra_checks）。
依赖：pip install jsonschema
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
CATEGORY_ORDER = ["core", "adjacent", "trending"]


def load_registry():
    schemas, resources = {}, []
    for path in SCHEMA_DIR.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name[:-len(".schema.json")]] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return schemas, Registry().with_resources(resources)


def extra_checks(name, data):
    errors = []
    if name == "recommendations-day":
        papers = data.get("papers", [])
        for category in CATEGORY_ORDER:
            n = sum(p.get("category") == category for p in papers)
            if data.get("counts", {}).get(category) != n:
                errors.append(f"counts.{category} 应为 {n}")
        keys = [(CATEGORY_ORDER.index(p["category"]), -p["score"]) for p in papers]
        if keys != sorted(keys):
            errors.append("papers 应先按 core → adjacent → trending 分组，组内按 score 从高到低")
    if name == "interests":
        ids, count = set(), 0

        def visit(nodes, trail):
            nonlocal count
            labels = set()
            for node in nodes:
                count += 1
                if node["id"] in ids:
                    errors.append("关键词 id 重复：" + node["id"])
                ids.add(node["id"])
                label = node["label"].strip().lower()
                if label in labels:
                    errors.append("同一级出现重复关键词：" + trail + node["label"])
                labels.add(label)
                visit(node.get("children", []), trail + node["label"] + " / ")

        visit(data.get("domains", []), "")
        if count > 300:
            errors.append(f"关键词节点 {count} 个，超过 300")
        for c in data.get("candidates", []):
            if c["parentId"] not in ids:
                errors.append("候选关键词的 parentId 不存在：" + c["parentId"])
    return errors


def check(path, schemas, registry):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"无法读取 JSON：{e}"]
    name = data.get("kind") if isinstance(data, dict) and "kind" in data else path.name.split(".")[0]
    if name not in schemas:
        return [f"无法确定 schema（{name!r}）"]
    validator = Draft202012Validator(schemas[name], registry=registry)
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(根)'}：{e.message}"
              for e in sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))]
    return errors or extra_checks(name, data)


def main(args):
    if not args:
        print(__doc__)
        return 2
    schemas, registry = load_registry()
    failed = 0
    for arg in args:
        p = Path(arg)
        for path in sorted(p.rglob("*.json")) if p.is_dir() else [p]:
            errors = check(path, schemas, registry)
            print(("✗ " if errors else "✓ ") + str(path))
            for e in errors:
                print("    " + e)
            failed += bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
