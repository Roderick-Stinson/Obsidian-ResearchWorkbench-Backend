"""阅读笔记：同步、要追的论文、阅读要点和候选关键词、推荐里怎么用。大模型和外部数据源都用假的。"""
from pwb import daily, llm, notes

from .conftest import API, assert_contract
from .test_daily import DATE, setup

MAAS = "arxiv-2502.04180"


def note(pid, path, sections, status="精读", rating=None, mtime=1000):
    return {"paperId": pid, "path": "论文阅读/" + path + ".md", "title": path, "status": status, "rating": rating, "mtime": mtime,
            "sections": [{"heading": h, "level": 2, "text": t} for h, t in sections]}


def paper(store, pid, title, origin=None):
    store.upsert_paper({"id": pid, "title": title, "authors": [], "publicationStatus": "预印本", "summary": "",
                        **({"origin": origin} if origin else {})})


def test_section_matches_numbered_headings():
    n = {"sections": [{"heading": "6. Idea 实现细节", "level": 2, "text": "框架"}, {"heading": "公式 (1)", "level": 3, "text": "x=y"},
                      {"heading": "7. Claims", "level": 2, "text": "C1"}]}
    assert notes.section(n, "Idea 实现细节") == "框架\n### 公式 (1)\nx=y", "下级小节算在这一节里"
    assert notes.section(n, "claims") == "C1" and notes.section(n, "没有这一节") == ""


def test_refs_in_text():
    text = ("- https://arxiv.org/abs/2410.10762v2\n- arXiv:2502.04180\n- https://openreview.net/forum?id=abc_1\n"
            "- 见 acl-2024.emnlp-main.782 和 multi-agent 系统\n- 2410.10762（重复）\n- openreview-Xyz9")
    assert notes.refs_in(text) == [("arxiv-2410.10762", "https://arxiv.org/abs/2410.10762"),
                                   ("arxiv-2502.04180", "https://arxiv.org/abs/2502.04180"),
                                   ("openreview-abc_1", "https://openreview.net/forum?id=abc_1"),
                                   ("acl-2024.emnlp-main.782", None), ("multi-agent", None),
                                   ("openreview-Xyz9", "https://openreview.net/forum?id=Xyz9")]
    assert notes.refs_in("openreview-imcyVlzpXh") == [("openreview-imcyVlzpXh", "https://openreview.net/forum?id=imcyVlzpXh")], \
        "面板改写回笔记的 OpenReview ID 也要认"


def test_sync_resolves_follows_and_library(client, store):
    paper(store, MAAS, "MaAS")
    paper(store, "arxiv-2610.00009", "A Recommended Paper", origin="recommendation")
    assert client.get(API + "/papers").json()["total"] == 1, "推荐来的论文不在论文库"
    follow = "- https://arxiv.org/abs/2612.00002\n- 2610.00009（已经写了笔记，不加）\n- multi-agent"
    body = {"notes": [
        note(MAAS, "MaAS 笔记", [("和我的研究的关系", "按查询采样。"), ("要追的论文", follow)], mtime=3000),
        note("https://arxiv.org/abs/2610.00009", "推荐论文的笔记", [("想法与疑问", "能不能复用技能？")], status="粗读", rating=4, mtime=2000),
        note(MAAS, "MaAS 旧笔记", [], mtime=1000),
        note("https://arxiv.org/abs/2611.00001v1", "新论文的笔记", [], status="待读"),
        note("还没填", "没填 ID 的笔记", [])]}
    res = assert_contract(client.put(API + "/me/notes", json=body).json(), "notes")
    assert {n["paperId"]: n["seed"] for n in res["items"]} == {MAAS: True, "arxiv-2610.00009": True, "arxiv-2611.00001": False}, \
        "精读的、评分 4 的算精读过"
    assert sorted(s["path"] for s in res["skipped"]) == ["论文阅读/MaAS 旧笔记.md", "论文阅读/没填 ID 的笔记.md"]
    assert res["sync"]["ids"]["https://arxiv.org/abs/2610.00009"] == "arxiv-2610.00009"
    assert res["sync"]["ids"]["https://arxiv.org/abs/2611.00001v1"] == "arxiv-2611.00001", "按链接收录"
    assert [p["id"] for p in res["sync"]["queued"]] == ["arxiv-2612.00002"]
    assert client.get(API + "/me/queue").json()["paperIds"] == ["arxiv-2612.00002"]
    ids = {p["id"] for p in client.get(API + "/papers").json()["items"]}
    assert ids == {MAAS, "arxiv-2610.00009", "arxiv-2611.00001"}, "写了笔记的进论文库；要追的论文只进队列"
    assert assert_contract(client.get(API + "/me/notes").json(), "notes")["items"] == res["items"]

    # 从队列里删掉，再同步也不会加回来。
    client.put(API + "/me/queue", json={"paperIds": []})
    again = client.put(API + "/me/notes", json=body).json()
    assert again["sync"]["queued"] == [] and client.get(API + "/me/queue").json()["paperIds"] == []
    # vault 里删光笔记：后端也删掉，推荐来的论文又不算进论文库。
    empty = assert_contract(client.put(API + "/me/notes", json={"notes": []}).json(), "notes")
    assert empty["items"] == [] and empty["skipped"] == []
    assert "arxiv-2610.00009" not in {p["id"] for p in client.get(API + "/papers").json()["items"]}
    assert client.put(API + "/me/notes", json={"notes": [{"paperId": MAAS}]}).status_code == 400


def test_notes_follow_paper_rename(store):
    paper(store, MAAS, "MaAS")
    notes.sync(store, [note(MAAS, "MaAS 笔记", [])], lambda url, origin=None: None, daily.get_settings(store)["notes"])
    store.rename_paper(MAAS, "openreview-imcyVlzpXh")
    assert [n["paperId"] for n in store.get_notes()] == ["openreview-imcyVlzpXh"]


def tree():
    return {"generatedAt": "2026-10-01T00:00:00+08:00", "overview": "", "stale": False, "basis": {"ownPapers": 0, "watchedPapers": 0, "relevantPapers": 0},
            "candidates": [{"id": "cand-old", "parentId": "kw-1", "level": 2, "label": "增长快的方向", "reason": "趋势",
                            "growth": {"paperCount": 9, "windowDays": 30, "changePct": 50}, "examples": [], "source": "trend"}],
            "domains": [{"id": "kw-1", "label": "架构搜索", "summary": "", "origin": "user", "pinned": False, "evidence": [],
                         "children": [{"id": "kw-2", "label": "已有方向", "summary": "", "origin": "user", "pinned": False, "evidence": [], "children": []}]}]}


def test_step_notes_profile_and_candidates(store, monkeypatch):
    paper(store, MAAS, "MaAS")
    store.set_kv("interests", tree())
    store.set_kv("rejected-candidates", [{"label": "忽略过的", "parentId": "kw-1", "rejectedAt": "2026-10-01T00:00:00+08:00"}])
    calls = []

    def fake(settings, config, system, prompt, schema):
        assert schema is notes.PROFILE_SCHEMA
        calls.append(prompt)
        return {"summary": "你关心按查询采样。", "points": [
            {"kind": "interest", "text": "按查询采样架构", "notes": [0]}, {"kind": "doubt", "text": "自演化增益", "notes": [0, 7]},
            {"kind": "interest", "text": "  ", "notes": [0]}],
            "candidates": [{"parentId": "kw-1", "label": "技能复用", "reason": "笔记里想追", "notes": [0]},
                           {"parentId": "kw-1", "label": "忽略过的", "reason": "", "notes": [0]},
                           {"parentId": "kw-1", "label": "已有方向", "reason": "", "notes": [0]},
                           {"parentId": "kw-nope", "label": "挂不上", "reason": "", "notes": [0]},
                           {"parentId": "kw-1", "label": "没出处", "reason": "", "notes": []}]}
    monkeypatch.setattr(llm, "complete_json", fake)
    cfg = daily.get_settings(store)
    ensure = lambda url, origin=None: None  # noqa: E731
    sections = [("1. 研究动机", "不参与"), ("和我的研究的关系", "按查询采样。"), ("想法与疑问", "自演化增益可疑。")]
    notes.sync(store, [note(MAAS, "MaAS 笔记", sections, rating=5)], ensure, cfg["notes"])
    msg, _ = notes.step_notes(store, cfg, DATE, print, {})
    assert "关心的问题 1 条、存疑的思路 1 条，候选关键词 1 个" in msg, msg
    assert "按查询采样。" in calls[0] and "不参与" not in calls[0] and "《MaAS》（精读，评分 5）" in calls[0]
    assert "架构搜索 [kw-1]" in calls[0]
    prof = store.get_kv("reading-profile")
    assert [(p["kind"], p["text"], [x["id"] for x in p["papers"]]) for p in prof["points"]] == \
        [("interest", "按查询采样架构", [MAAS]), ("doubt", "自演化增益", [MAAS])], "空要点去掉，编号越界的出处去掉"
    cands = store.get_kv("interests")["candidates"]
    assert [(c["label"], c.get("source")) for c in cands] == [("增长快的方向", "trend"), ("技能复用", "notes")]
    assert cands[1]["level"] == 2 and cands[1]["examples"] == [{"id": MAAS, "title": "MaAS"}]

    msg, _ = notes.step_notes(store, cfg, DATE, print, {})
    assert len(calls) == 1 and "沿用" in msg, "笔记没变就不重新整理"
    notes.sync(store, [note(MAAS, "MaAS 笔记", sections[:2] + [("想法与疑问", "改了")], mtime=2000)], ensure, cfg["notes"])
    notes.step_notes(store, cfg, DATE, print, {})
    assert len(calls) == 2 and [c["label"] for c in store.get_kv("interests")["candidates"]] == ["增长快的方向", "技能复用"], \
        "笔记提出的候选整体替换，不重复"

    notes.sync(store, [note(MAAS, "MaAS 笔记", sections[:1], mtime=3000)], ensure, cfg["notes"])
    msg, _ = notes.step_notes(store, cfg, DATE, print, {})
    assert "还没有写了" in msg and store.get_kv("reading-profile") is None
    assert [c["label"] for c in store.get_kv("interests")["candidates"]] == ["增长快的方向"]
    off = daily.put_settings(store, {"notes": {"screening": False, "candidates": False}})
    assert "关闭" in notes.step_notes(store, off, DATE, print, {})[0] and len(calls) == 2


def test_daily_run_uses_notes(client, store, monkeypatch):
    fetched, prompts = setup(store, monkeypatch)
    base = llm.complete_json

    def fake(settings, config, system, prompt, schema):
        if schema is notes.PROFILE_SCHEMA:
            prompts.append(prompt)
            return {"summary": "你想知道机器人能不能用上架构搜索。", "candidates": [],
                    "points": [{"kind": "interest", "text": "机器人上的架构搜索", "notes": [0]},
                               {"kind": "doubt", "text": "靠点赞火起来的方法", "notes": [0]}]}
        out = base(settings, config, system, prompt, schema)
        item = schema["properties"].get("items", {}).get("items", {})
        if "points" in item.get("properties", {}):
            titles = {int(l[4:].split(". ", 1)[0]): l[4:].split(". ", 1)[1]
                      for l in prompt.split("<论文列表>")[1].splitlines() if l.startswith("### ")}
            for x in out["items"]:
                t = titles[x["i"]]
                x["points"] = [0] if t.startswith("Unrelated Robot") else [1] if t.startswith("Viral") else []
                if t.startswith("Unrelated Robot"):
                    x["relevant"] = True
        return out
    monkeypatch.setattr(llm, "complete_json", fake)
    daily.put_settings(store, {"adjacent": {"seeds": []}})  # 只留笔记这一种种子
    client.put(API + "/me/notes", json={"notes": [note(MAAS, "MaAS 笔记", [("和我的研究的关系", "想用到机器人上。")])]})

    daily.run(store, llm.get_config(store), lambda *a: None, date=DATE)
    classify = next(p for p in prompts if "<论文列表>" in p and "relevant" in p)
    assert "<我阅读笔记里的要点" in classify and "0. (interest) 机器人上的架构搜索" in classify
    assert any("我最近读论文的笔记要点：你想知道机器人" in p for p in prompts), "简报参考阅读要点"
    day = assert_contract(client.get(API + "/recommendations/" + DATE).json(), "recommendations-day")
    by_id = {p["id"]: p for p in day["papers"]}
    robot = by_id["arxiv-2610.00003"]
    assert robot["category"] == "core" and robot["matchedKeywords"] == [], "没命中关键词，回应了笔记里关心的问题也算核心"
    assert "回应了你在《Multi-agent Architecture Search" in robot["reason"] and "机器人上的架构搜索" in robot["reason"]
    assert "你精读过的《Multi-agent Architecture Search" in by_id["arxiv-2610.00002"]["reason"], "精读过的论文当种子"
    viral = by_id["arxiv-2610.00005"]
    assert viral["category"] == "trending" and "存疑的思路“靠点赞火起来的方法”" in viral["reason"]
    cand = next(c for c in daily._rows(store, "published") if c["id"] == "2610.00005")
    weights = daily.get_settings(store)["pick"]["weights"]
    plain = {**cand, "result": {**cand["result"], "doubt": False}}
    assert viral["score"] < daily._score(plain, weights), "用了存疑思路的精选分打折"
    steps = {s["key"]: s["status"] for s in client.get(API + "/pipeline").json()["latestRun"]["steps"]}
    assert steps["notes"] == "ok"


def test_old_run_doc_gets_new_steps(store, monkeypatch):
    setup(store, monkeypatch)
    doc, run = daily._run_doc(store, DATE)
    run["steps"] = [s for s in run["steps"] if s["key"] != "notes"]
    daily._put_run(store, doc, run, daily.get_settings(store))
    _, run = daily._run_doc(store, DATE)
    assert [s["key"] for s in run["steps"]] == [k for k, _ in daily.STEPS], "旧版本跑的今天这次补上新步骤"
