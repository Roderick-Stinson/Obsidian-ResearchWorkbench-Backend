"""启动时把旧版本存下的数据改成现在的格式（每项都可重复执行）。"""
from . import profile


def run(store):
    _map_hotspots_list(store)


def _map_hotspots_list(store):
    """领域发展史的 hotspots 以前是一个对象（只有一个论文流），现在是按论文流的列表。"""
    doc = store.get_doc("map")
    if not doc or not isinstance(doc.get("hotspots"), dict):
        return
    try:
        first = profile.streams(store)[0]
    except (FileNotFoundError, ValueError, IndexError):
        first = {"key": "main", "name": "arXiv"}
    store.put_doc({**doc, "hotspots": [{"stream": first["key"], "name": first["name"], **doc["hotspots"]}]})
