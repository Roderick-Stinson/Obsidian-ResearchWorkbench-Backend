"""按 contract/schemas 校验数据。后端写入流水线数据、接收请求体时都走这里。"""
import json
from functools import lru_cache

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .config import CONTRACT_DIR


@lru_cache(maxsize=None)
def _validators():
    schemas, resources = {}, []
    for path in (CONTRACT_DIR / "schemas").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name[:-len(".schema.json")]] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    return {name: Draft202012Validator(s, registry=registry) for name, s in schemas.items()}


def errors(name, data):
    validator = _validators().get(name)
    if validator is None:
        raise KeyError("约定里没有这个 schema：" + name)
    return ["{}：{}".format("/".join(map(str, e.absolute_path)) or "(根)", e.message)
            for e in validator.iter_errors(data)]


class ContractError(ValueError):
    def __init__(self, name, errs):
        super().__init__("不符合约定 {}：{}".format(name, "；".join(errs[:5])))
        self.errors = errs


def check(name, data):
    errs = errors(name, data)
    if errs:
        raise ContractError(name, errs)
    return data
