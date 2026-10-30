"""补中文标题和总结：arXiv、大模型都用假的。"""
from pwb import llm, summarize
from pwb import pdfs

from .test_authors import full


def test_fill_missing_titles(store, monkeypatch):
    store.upsert_paper(full(id="arxiv-2609.38661", title="EvoSteer", authors=[], year=2026, publicationStatus="投稿中", summary=""))
    store.upsert_paper(full(id="openreview-x1", title="MAS Two", authors=[], year=2026, publicationStatus="已发表", summary="", abstract="We do X."))
    store.upsert_paper(full(id="openreview-x2", title="Nothing", authors=[], year=2026, publicationStatus="已发表", summary=""))
    store.upsert_paper(full(id="openreview-x3", title="Edited", authors=[], year=2026, publicationStatus="已发表", summary="",
                            titleZh=None, userEdited=["titleZh"]))
    store.upsert_paper(full(id="openreview-x4", title="Done", titleZh="已有", authors=[], year=2026, publicationStatus="已发表", summary="s"))
    monkeypatch.setattr(summarize, "arxiv_meta", lambda a: {"abstract": "Online self-evolving graphs."} if a == "2609.38661" else None)
    prompts = []

    def fake(settings, config, system, prompt, schema):
        prompts.append(prompt)
        n = len([l for l in prompt.splitlines() if l.startswith("### ")])
        return {"items": [{"i": i, "titleZh": "中文标题" + str(i), "summary": "总结" + str(i)} for i in range(n)]}
    monkeypatch.setattr(llm, "complete_json", fake)
    assert set(summarize.todo(store)) == {"arxiv-2609.38661", "openreview-x1", "openreview-x2"}, "改过中文标题的、已有的不补"
    msg, n = summarize.step_summaries(store, {}, "2026-10-30", print, {})
    assert n == 2 and "1 篇没有摘要也没有 PDF" in msg, msg
    evo = store.get_paper("arxiv-2609.38661")
    assert evo["titleZh"].startswith("中文标题") and evo["abstract"] == "Online self-evolving graphs.", "从 arXiv 补上摘要"
    assert "Online self-evolving graphs." in prompts[0] and "We do X." in prompts[0]
    assert store.get_paper("openreview-x2").get("titleZh") is None
