"""领域检索入库：Semantic Scholar 和大模型都用假的。"""
import json

import pytest

from pwb import llm, survey

from .conftest import API, assert_contract

MAAS = "openreview-imcyVlzpXh"


def s2_paper(pid, title, venue, **kw):
    return {"paperId": pid, "title": title, "abstract": "abstract of " + title, "venue": venue, "publicationVenue": None,
            "year": 2025, "publicationDate": "2025-07-13", "externalIds": kw.get("ext", {}), "citationCount": kw.get("cites", 3),
            "openAccessPdf": {"url": "https://arxiv.org/pdf/" + kw["arxiv"]} if kw.get("arxiv") else None,
            "authors": [{"name": "Ann Lee"}, {"name": "Bo Li"}], "url": "https://www.semanticscholar.org/paper/" + pid}


PAGES = [
    {"data": [s2_paper("s1", "Multi-agent Architecture Search via Agentic Supernet", "International Conference on Machine Learning",
                       ext={"ArXiv": "2502.04180", "DBLP": "conf/icml/ZhangNF0B025"}, arxiv="2502.04180", cites=208),
              s2_paper("s2", "Topology Learning for LLM Teams", "ICLR", ext={"DOI": "10.5555/abc(1)"}),
              s2_paper("s3", "A Dataset of Cat Pictures", "CVPR", ext={"DBLP": "conf/cvpr/Cat25"}),
              s2_paper("s4", "Some arXiv-only agent paper", "arXiv.org"),
              s2_paper("s6", "Agent Findings Paper", "ACL", ext={"DOI": "10.18653/v1/2025.findings-acl.7"}),
              s2_paper("s7", "Unverified Agent Routing", "NeurIPS", ext={"ArXiv": "2605.00001", "DBLP": "journals/corr/abs-2605-00001"})], "token": "next"},
    {"data": [s2_paper("s5", "Explaining GNNs with Counterfactuals", "Nature Machine Intelligence", ext={"DOI": "10.1038/s42256-025-1"})]},
]


PROFILE = {"queries": [{"topic": "mas", "query": "q1"}], "direction": ["我的研究方向：多智能体系统。"],
           "extraTopics": ["LLM 可解释性", "图模型可解释性"]}


def fake_s2(monkeypatch, pages):
    calls = []

    def get(params, log, tries=25):
        calls.append(params)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else {"data": []}
    monkeypatch.setattr(survey, "_s2_get", get)
    monkeypatch.setattr(survey, "load_profile", lambda store, required=False: PROFILE)
    monkeypatch.setattr(survey.time, "sleep", lambda s: None)
    return calls


def fake_llm(monkeypatch, decide):
    def complete(settings, config, system, prompt, schema):
        if "keep" in schema["properties"]:
            titles = [l.split(". ", 1) for l in prompt.split("<标题列表>")[1].split("</标题列表>")[0].strip().splitlines()]
            return {"keep": [int(i) for i, t in titles if "Cat" not in t]}
        items = [l[4:].split(". ", 1) for l in prompt.splitlines() if l.startswith("### ")]
        return {"papers": [decide(int(i), t) for i, t in items]}
    monkeypatch.setattr(llm, "complete_json", complete)


def decide(i, title):
    if "Topology" in title:
        return {"i": i, "relevance": "related", "topics": ["其他相关方向"], "titleZh": "拓扑学习", "summary": "学习团队拓扑。", "reason": "拓扑设计"}
    if "GNN" in title:
        return {"i": i, "relevance": "core", "topics": ["图模型可解释性"], "titleZh": "图解释", "summary": "反事实解释。", "reason": "图可解释性"}
    return {"i": i, "relevance": "core", "topics": ["其他相关方向"], "titleZh": "超网", "summary": "超网采样。", "reason": "架构搜索"}


def test_survey_end_to_end(client, store, monkeypatch):
    store.upsert_paper({"id": MAAS, "title": "Multi-agent Architecture Search via Agentic Supernet", "titleZh": None, "authors": ["Guibin Zhang"],
                        "firstAuthor": "Guibin Zhang", "year": 2025, "venue": "ICML 2025", "publicationStatus": "已发表",
                        "versions": [], "urls": {"source": None, "pdf": None, "code": None}, "pdfSha256": None,
                        "citations": {"count": None, "updatedAt": None}, "metadataStatus": "部分核实", "summary": "", "matchedKeywords": []},
                       ["arxiv-2502.04180"])
    store.patch_user(MAAS, {"watched": True})
    calls = fake_s2(monkeypatch, PAGES)
    fake_llm(monkeypatch, decide)
    logs = []
    result = survey.run(store, llm.get_config(store), log=logs.append)
    assert calls[1]["token"] == "next" and "ICLR" in calls[0]["venue"], "翻页并带上会议过滤"
    assert result == {"title_drop": 1, "ingested": 4, "excluded": 1}, "arXiv 来源的不收；Findings 不收；猫图在标题初筛被筛掉"
    unverified = assert_contract(client.get(API + "/papers/arxiv-2605.00001").json(), "paper")
    assert unverified["publicationStatus"] == "待核实" and unverified["metadataStatus"] == "待核实" and "未核实" in unverified["libraryReason"]

    maas = assert_contract(client.get(API + "/papers/" + MAAS).json(), "paper")
    assert maas["id"] == MAAS and maas["user"]["watched"], "已有论文按编号合并，主 ID 和你的标记不变"
    assert maas["citations"]["count"] == 208 and maas["titleZh"] == "超网" and "dblp-conf_icml_ZhangNF0B025" in maas["aliases"]

    topo = assert_contract(client.get(API + "/papers/doi-10.5555_abc_1_").json(), "paper")
    assert topo["venue"] == "ICLR 2025" and topo["publicationStatus"] == "已发表" and topo["firstAuthor"] == "Ann Lee"
    assert topo["topics"] == ["其他相关方向"] and topo["libraryRelevance"] == "related"
    gnn = assert_contract(client.get(API + "/papers/doi-10.1038_s42256-025-1").json(), "paper")
    assert gnn["versions"][0]["kind"] == "journal" and gnn["venue"] == "Nat. Mach. Intell. 2025"
    assert client.get(API + "/papers?q=cat").json()["total"] == 0

    again = survey.run(store, llm.get_config(store), stages=("titles", "abstracts", "ingest"), log=logs.append)
    assert again == result, "重跑不重复处理"


def test_survey_keeps_batch_on_llm_failure(store, monkeypatch):
    fake_s2(monkeypatch, PAGES)
    survey.init(store)
    survey.fetch(store, "2023-2026", lambda m: None)

    def boom(*a):
        raise llm.LLMError("限额用完")
    monkeypatch.setattr(llm, "complete_json", boom)
    logs = []
    survey.screen_titles(store, llm.get_config(store), 2, logs.append)
    assert survey.stats(store) == {"fetched": 5, "excluded": 1} and any("限额用完" in l for l in logs), "失败的批次留在原阶段，下次重跑"


def test_acceptance():
    assert survey.acceptance({"externalIds": {"DBLP": "conf/nips/A24"}}) == "verified"
    assert survey.acceptance({"externalIds": {"DOI": "10.18653/v1/2025.acl-long.1"}}) == "verified"
    assert survey.acceptance({"externalIds": {"DOI": "10.18653/v1/2025.findings-emnlp.3"}}) == "findings"
    assert survey.acceptance({"externalIds": {"DBLP": "journals/corr/abs-1", "DOI": "10.48550/arXiv.1"}}) == "unverified"


def test_venue_year_prefers_dblp_and_acl():
    assert survey.venue_year({"year": 2024, "externalIds": {"DBLP": "conf/iclr/AzzolinLTP25"}}) == 2025
    assert survey.venue_year({"year": 2023, "externalIds": {"DBLP": "conf/aaai/Huang0L24"}}) == 2024
    assert survey.venue_year({"year": 2024, "externalIds": {"DOI": "10.18653/v1/2025.acl-long.3"}}) == 2025
    assert survey.venue_year({"year": 2026, "externalIds": {"DBLP": "journals/corr/abs-2605-1"}}) == 2026, "只有 arXiv 时用原值"


def test_venue_matching():
    assert survey._venue_short({"venue": "International Conference on Machine Learning"}) == "ICML"
    assert survey._venue_short({"venue": "Some Workshop on Agents"}) is None, "不认识的不猜"
    assert survey._venue_short({"venue": "x", "publicationVenue": {"name": "Nature Machine Intelligence"}}) == "Nat. Mach. Intell."
    assert survey._venue_short({"venue": "arXiv.org"}) is None


def test_topic_filter_and_counts(client, store, monkeypatch):
    fake_s2(monkeypatch, PAGES)
    fake_llm(monkeypatch, decide)
    survey.run(store, llm.get_config(store), log=lambda m: None)
    counts = assert_contract(client.get(API + "/papers/topics").json(), "paper-topics")["items"]
    assert counts == [{"label": "其他相关方向", "count": 3}, {"label": "图模型可解释性", "count": 1}]
    only = assert_contract(client.get(API + "/papers?topic=图模型可解释性").json(), "paper-list")
    assert [p["id"] for p in only["items"]] == ["doi-10.1038_s42256-025-1"] and only["items"][0]["libraryRelevance"] == "core"


def test_library_filters_sort_facets_and_abstract_search(client, store, monkeypatch):
    fake_s2(monkeypatch, PAGES)
    fake_llm(monkeypatch, decide)
    survey.run(store, llm.get_config(store), log=lambda m: None)
    body = assert_contract(client.get(API + "/papers?facets=true&sort=relevance").json(), "paper-list")
    assert [p["libraryRelevance"] for p in body["items"]][:2] == ["core", "core"] and body["items"][-1]["libraryRelevance"] == "related"
    assert body["items"][0]["summary"] and "urls" in body["items"][0]
    f = body["facets"]
    assert {"label": "ICLR", "count": 1} in f["venue"] and {"label": "待核实", "count": 1} in f["status"]
    assert {"label": "core", "count": 3} in f["relevance"]
    only = client.get(API + "/papers?venue=ICLR&facets=true").json()
    assert only["total"] == 1 and len(only["facets"]["venue"]) > 1, "会议分面自己不受会议筛选影响"
    assert client.get(API + "/papers?q=abstract%20topology").json()["total"] == 1, "能搜到摘要，多个词须全部命中"
    assert client.get(API + "/papers?status=待核实").json()["items"][0]["id"] == "arxiv-2605.00001"
    client.patch(API + "/me/papers/arxiv-2605.00001", json={"rating": 4})
    assert [p["id"] for p in client.get(API + "/papers?rating=3").json()["items"]] == ["arxiv-2605.00001"]
    assert client.get(API + "/papers?rating=unrated").json()["total"] == 3
    assert client.get(API + "/papers?sort=bogus").status_code == 400
    assert survey.backfill_abstracts(store, lambda m: None) == 0, "入库时已存摘要"


def test_user_edits_survive_reingest(client, store, monkeypatch):
    fake_s2(monkeypatch, PAGES)
    fake_llm(monkeypatch, decide)
    survey.run(store, llm.get_config(store), log=lambda m: None)
    pid = "doi-10.5555_abc_1_"
    body = assert_contract(client.patch(API + "/papers/" + pid, json={"publicationStatus": "已录用", "venue": "ICLR 2026", "firstAuthor": "Zed"}).json(), "paper")
    assert body["userEdited"] == ["firstAuthor", "publicationStatus", "venue"]
    assert client.patch(API + "/papers/" + pid, json={"publicationStatus": "发了"}).status_code == 400
    survey.reingest(store, lambda m: None)
    again = client.get(API + "/papers/" + pid).json()
    assert (again["publicationStatus"], again["venue"], again["firstAuthor"]) == ("已录用", "ICLR 2026", "Zed"), "重新入库不覆盖你改过的字段"
    assert again["citations"]["count"] == 3, "没改过的字段照常更新"


def test_profile_required_for_fetch(store):
    with pytest.raises(FileNotFoundError, match="survey.example.json"):
        survey.fetch(store, "2023-2026", lambda m: None)
    assert survey._topics(store)[1] == [], "没配置时筛选和整理照常进行，只是没有额外方向"
    example = json.loads(survey.PROFILE_EXAMPLE.read_text(encoding="utf-8"))
    survey.profile_path(store).write_text(json.dumps(example, ensure_ascii=False), encoding="utf-8")
    assert survey.load_profile(store, required=True)["queries"] and example["direction"][0] in survey._direction_text(store)
