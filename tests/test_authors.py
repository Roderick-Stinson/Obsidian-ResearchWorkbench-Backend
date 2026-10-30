"""作者机构与团队：OpenAlex、PDF 第一页、大模型都用假的。"""
from pwb import authors, daily, llm

from .conftest import API, assert_contract
from .test_daily import DATE, setup

OA = "https://openalex.org/"
WORK = {"title": "Agent Teams Search", "publication_year": 2025, "authorships": [
    {"author": {"id": OA + "A1", "display_name": "Wang, Wei"}, "is_corresponding": False,
     "institutions": [{"id": OA + "I2", "display_name": "Institute of Computing Technology", "country_code": "CN",
                       "lineage": [OA + "I1", OA + "I2"]}]},
    {"author": {"id": OA + "A3", "display_name": "Li Na"}, "is_corresponding": False, "institutions": []}]}


def full(**kw):
    return {"titleZh": None, "venue": "", "versions": [], "urls": {"source": None, "pdf": None, "code": None}, "pdfSha256": None,
            "citations": {"count": None, "updatedAt": None}, "metadataStatus": "待核实", "matchedKeywords": [], "firstAuthor": "", **kw}


def fake_oa(calls):
    def oa(path, params):
        calls.append((path, params))
        if path == "works" and "title.search" in params.get("filter", ""):
            return {"results": [WORK] if "agent teams search" in params["filter"] else []}
        if path == "institutions" and "filter" in params:
            return {"results": [{"id": OA + "I1", "display_name": "Chinese Academy of Sciences", "country_code": "CN"}]}
        if path == "institutions" and "search" in params:
            return {"results": [{"id": OA + "I9", "display_name": "Tsinghua University", "country_code": "CN", "lineage": [OA + "I9"]}]
                    if params["search"] == "Tsinghua University" else []}
        if path == "works" and "author.id" in params["filter"]:
            return {"results": [{"title": "Followed New Work", "publication_date": "2026-10-27",
                                 "authorships": [{"author": {"id": OA + "A1", "display_name": "Wei Wang"}}],
                                 "locations": [{"landing_page_url": "https://arxiv.org/abs/2610.00077"}],
                                 "abstract_inverted_index": {"New": [0], "work": [1]}}]}
        raise AssertionError((path, params))
    return oa


def test_names():
    assert authors.norm_name("Zhang, Guibin") == authors.norm_name("Guibin Zhang") == authors.norm_name("zhang guibin")
    assert authors.norm_name("Yao-Dong Yang") == authors.norm_name("Yaodong Yang")
    assert authors.display_name("Zhang, Guibin") == "Guibin Zhang" and authors.display_name("Li Na") == "Li Na"
    assert authors.person_key("Guibin Zhang") == "n-guibin-zhang" and authors.person_key("x", "A1") == "A1"


def test_enrich_merges_openalex_and_pdf(client, store, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(authors, "_oa", fake_oa(calls))
    store.upsert_paper(full(id="arxiv-2501.00001", title="Agent Teams Search", authors=["Wei Wang"], firstAuthor="Wei Wang",
                            year=2025, publicationStatus="预印本", summary="", libraryRelevance="core",
                            matchedKeywords=[{"nodeId": "kw-1", "label": "架构搜索"}]))
    store.upsert_paper(full(id="arxiv-2501.00002", title="Nobody Knows This", authors=["A B"], year=None, publicationStatus="预印本", summary=""))
    (store.settings.pdf_dir / "p.pdf").write_bytes(b"%PDF")
    store.set_pdf("arxiv-2501.00001", "p.pdf", "0" * 64)
    monkeypatch.setattr(authors, "_first_page", lambda path: "Agent Teams Search. Wei Wang (ICT, CAS), Na Li* (Tsinghua) *Corresponding author")
    prompts = []

    def fake(settings, config, system, prompt, schema):
        prompts.append(prompt)
        if schema is authors.PDF_SCHEMA:
            return {"items": [{"i": 0, "marked": True, "authors": [
                {"name": "Wei Wang", "corresponding": False, "affiliations": [{"institution": "Chinese Academy of Sciences", "unit": "ICT"}]},
                {"name": "Na Li", "corresponding": True, "affiliations": [{"institution": "Tsinghua University", "unit": "THUNLP"}]}]}]}
        if schema is authors.ZH_SCHEMA:
            names = [l.split(". ", 1)[1] for l in prompt.splitlines() if l[:1].isdigit()]
            zh = {"Chinese Academy of Sciences": "中国科学院", "Institute of Computing Technology": "计算技术研究所", "Tsinghua University": "清华大学"}
            return {"items": [{"i": i, "zh": zh.get(n, "")} for i, n in enumerate(names)]}
        raise AssertionError(prompt[:100])
    monkeypatch.setattr(llm, "complete_json", fake)

    # 先只查 OpenAlex（快），PDF 之后再补读：补读时不重查 OpenAlex。
    msg, n = authors.run(store, {}, lambda *a: None, read_pdf=False)
    assert n == 2 and "OpenAlex 找到 1 篇，读 PDF 第一页 0 篇" in msg, msg
    assert store.get_paper("arxiv-2501.00001")["authorships"][1]["corresponding"] is None, "没读 PDF，不知道通讯作者"
    looked = len([c for c in calls if c[0] == "works"])
    msg, n = authors.run(store, {}, lambda *a: None)
    assert n == 1 and "读 PDF 第一页 1 篇" in msg, msg
    assert len([c for c in calls if c[0] == "works"]) == looked, "补读 PDF 时不重查 OpenAlex"
    p = store.get_paper("arxiv-2501.00001")
    assert p["authors"] == ["Wei Wang", "Li Na"] and p["firstAuthor"] == "Wei Wang", "只记了一作的补全作者"
    wang, li = p["authorships"]
    assert wang["openalexId"] == "A1" and wang["affiliations"] == [{"key": "I2", "unit": "ICT"}], "OpenAlex 的机构为准，PDF 补院系"
    assert wang["corresponding"] is False and li["corresponding"] is True, "PDF 标了通讯作者"
    assert li["affiliations"] == [{"key": "I9", "unit": "THUNLP"}], "OpenAlex 没有机构的，用 PDF 的（搜 OpenAlex 对上编号）"
    insts = store.get_kv("institutions")
    assert insts["I2"]["parent"] == "I1" and insts["I1"]["name"] == "Chinese Academy of Sciences" and insts["I1"]["nameZh"] == "中国科学院"
    assert store.get_paper("arxiv-2501.00002")["affiliations"]["sources"] == [], "两边都没有也记下查过了"
    assert store.get_paper("arxiv-2501.00001")["affiliations"]["sources"] == ["openalex", "pdf"]
    assert authors.run(store, {}, lambda *a: None)[0] == "没有要整理的论文"

    body = assert_contract(client.get(API + "/papers/arxiv-2501.00001").json(), "paper")
    assert "authorships" not in body and body["authorsDetail"][1] == {
        "name": "Li Na", "personKey": "A3", "corresponding": True,
        "affiliations": [{"key": "I9", "name": "Tsinghua University", "nameZh": "清华大学", "parent": None, "unit": "THUNLP"}]}
    assert body["authorsDetail"][0]["affiliations"][0]["parent"] == "中国科学院"
    row = next(i for i in client.get(API + "/papers").json()["items"] if i["id"] == "arxiv-2501.00001")
    assert row["institutions"] == ["清华大学", "中国科学院"], "通讯作者的机构在前，取上级机构"

    teams = assert_contract(client.get(API + "/teams").json(), "teams")
    assert [t["nameZh"] for t in teams["items"]] == ["中国科学院", "清华大学"] and teams["coverage"] == {"papers": 2, "withAffiliations": 1}
    cas = teams["items"][0]
    assert cas["keywords"] == [{"label": "架构搜索", "count": 1}] and cas["units"] == ["ICT 1"] and cas["people"][0]["name"] == "Wei Wang"
    thu = teams["items"][1]["people"][0]
    assert thu["correspondingCount"] == 1 and thu["lastAuthorCount"] == 1
    assert client.get(API + "/teams?q=清华").json()["total"] == 1

    f = assert_contract(client.put(API + "/me/follows/A3", json={"name": "Li Na", "openalexId": "A3", "institution": "I9"}).json(), "follows")
    assert f["people"][0]["institutionName"] == "清华大学"
    assert client.get(API + "/teams").json()["items"][1]["people"][0]["followed"] is True
    assert client.delete(API + "/me/follows/A3").json()["people"] == []
    assert client.put(API + "/me/follows/A3", json={}).status_code == 400


def test_daily_recommends_followed_people(client, store, monkeypatch):
    fetched, prompts = setup(store, monkeypatch)
    calls = []
    monkeypatch.setattr(authors, "_oa", fake_oa(calls))
    store.set_kv("institutions", {"I1": {"name": "Chinese Academy of Sciences", "nameZh": "中国科学院", "parent": None, "openalex": True}})
    store.set_kv("follows", {"people": [{"key": "A1", "name": "Wei Wang", "openalexId": "A1", "institution": "I1", "followedAt": DATE},
                                        {"key": "n-b", "name": "B", "openalexId": None, "institution": None, "followedAt": DATE}]})
    daily.put_settings(store, {"adjacent": {"seeds": []}})
    daily.run(store, llm.get_config(store), lambda *a: None, date=DATE)
    day = assert_contract(client.get(API + "/recommendations/" + DATE).json(), "recommendations-day")
    by_id = {p["id"]: p for p in day["papers"]}
    assert by_id["arxiv-2610.00077"]["category"] == "adjacent" and "来自你关注的Wei Wang（中国科学院）" in by_id["arxiv-2610.00077"]["reason"], \
        "关注的人的新论文（OpenAlex 按人取来）"
    assert "来自你关注的B" in by_id["arxiv-2610.00002"]["reason"], "按作者名字认出关注的人"
    assert any(c[0] == "works" and "author.id:A1" in c[1]["filter"] for c in calls)
    steps = {s["key"]: s["status"] for s in client.get(API + "/pipeline").json()["latestRun"]["steps"]}
    assert steps["affiliations"] == "ok"


def test_openalex_quota_keeps_papers_pending(store, monkeypatch):
    """额度用完时不再往下查，没查到的论文留着下次查（不能记成“查过了没找到”）。"""
    calls = []

    def limited(path, params):
        calls.append(path)
        raise authors.OpenAlexLimit("OpenAlex 的额度用完了")
    monkeypatch.setattr(authors, "_oa", limited)
    for i in range(3):
        store.upsert_paper(full(id="arxiv-2501.0000{}".format(i), title="Paper {}".format(i), authors=["A"], year=2025,
                                publicationStatus="预印本", summary=""))
    msg, n = authors.run(store, {}, lambda *a: None)
    assert n == 3 and "额度用完" in msg and len(calls) == 1, "额度用完后不再一篇篇试"
    assert all("affiliations" not in store.get_paper("arxiv-2501.0000{}".format(i)) for i in range(3))
    assert len(authors.todo_papers(store)) == 3
    monkeypatch.setenv("OPENALEX_API_KEY", "k-test")
    authors.use_key(store)
    assert authors._KEY["value"] == "k-test"


def test_openalex_after_pdf_keeps_pdf_affiliations(store, monkeypatch):
    """没有 key 时先读了 PDF，后来 OpenAlex 能查了：保留 PDF 的机构和通讯作者，只补 OpenAlex 的作者编号。"""
    store.upsert_paper(full(id="arxiv-2501.00001", title="Agent Teams Search", authors=["Wei Wang", "Li Na"], year=2025,
                            publicationStatus="预印本", summary="",
                            authorships=[{"name": "Wei Wang", "openalexId": None, "affiliations": [{"key": "x-somelab", "unit": None}], "corresponding": True},
                                         {"name": "Li Na", "openalexId": None, "affiliations": [], "corresponding": False}],
                            affiliations={"sources": ["pdf"], "updatedAt": "2026-10-30T00:00:00+08:00", "pdfTried": True, "openalexTried": False}))
    calls = []
    monkeypatch.setattr(authors, "_oa", fake_oa(calls))
    monkeypatch.setattr(llm, "complete_json", lambda *a: {"items": []})
    authors.run(store, {}, lambda *a: None)
    p = store.get_paper("arxiv-2501.00001")
    wang, li = p["authorships"]
    assert wang["affiliations"] == [{"key": "x-somelab", "unit": None}] and wang["corresponding"] is True and wang["openalexId"] == "A1"
    assert li["openalexId"] == "A3" and li["affiliations"] == [], "OpenAlex 也没有机构的保持空"
    assert p["affiliations"]["sources"] == ["openalex", "pdf"] and p["affiliations"]["openalexTried"] is True


def test_universities_are_not_merged_into_parent():
    insts = {"I1": {"name": "University of London", "type": "education", "parent": None},
             "I2": {"name": "University College London", "type": "education", "parent": "I1"},
             "I3": {"name": "Chinese Academy of Sciences", "type": "government", "parent": None},
             "I4": {"name": "Institute of Computing Technology", "type": "facility", "parent": "I3"}}
    assert authors.top_of(insts, "I2") == "I2", "UCL 自成一组"
    assert authors.top_of(insts, "I4") == "I3", "计算所并到中科院"


def test_ref_titles_migration(store):
    """旧数据里的论文引用显示的是中文结论式标题：启动时换成原题，简报里另存中文标题。"""
    from pwb import migrate
    store.upsert_paper(full(id="arxiv-2501.00001", title="Agent Teams Search", titleZh="用超网把搜索成本降一半", authors=[],
                            year=2025, publicationStatus="预印本", summary=""))
    store.put_doc({"schemaVersion": 1, "kind": "brief", "generatedAt": "2026-10-30T00:00:00+08:00", "runId": "daily-2026-10-30",
                   "date": "2026-10-30", "updatedAt": "2026-10-30T00:00:00+08:00", "newCount": 1, "pickedCount": 1, "summary": "",
                   "highlights": [{"title": "要点", "text": "说明", "sources": [{"id": "arxiv-2501.00001", "title": "用超网把搜索成本降一半"}]}],
                   "picks": [{"id": "arxiv-2501.00001", "title": "用超网把搜索成本降一半", "summary": "速读", "publishedAt": "2026-10-29", "reason": "核心"}],
                   "todayNew": [], "weekly": None})
    store.set_kv("migrated-ref-titles", None)
    migrate.run(store)
    b = store.get_doc("brief", "2026-10-30")
    assert b["picks"][0]["title"] == "Agent Teams Search" and b["picks"][0]["titleZh"] == "用超网把搜索成本降一半"
    assert b["highlights"][0]["sources"][0] == {"id": "arxiv-2501.00001", "title": "Agent Teams Search"}
