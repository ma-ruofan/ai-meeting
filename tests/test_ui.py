from streamlit.testing.v1 import AppTest

from meeting_assistant.config import ROOT


def test_ui_sample_flow_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "cloud")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not app.exception
    button = next(b for b in app.button if b.label == "打开文字样例")
    button.click().run()
    assert not app.exception
    assert "产品评审" in app.title[0].value
    assert any("文字开发样例" in x.value for x in app.info)
    app.segmented_control[0].set_value("会议纪要").run()
    assert not app.exception
    assert next(b for b in app.button if b.label == "生成新版本纪要").disabled
    app.segmented_control[0].set_value("问答 Agent").run()
    assert not app.exception
    assert next(b for b in app.button if b.label == "查询会议 →").disabled


def test_ui_saved_minutes_agent_and_historical_evidence(tmp_path, monkeypatch, minutes):
    from conftest import FakeClient, final_action, tool_action

    from meeting_assistant.agent import ask_meeting
    from meeting_assistant.services import generate_minutes, import_sample
    from meeting_assistant.storage import Repository

    monkeypatch.setenv("MEETING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODEL", "")
    repo = Repository(tmp_path)
    ident = import_sample(repo, ROOT / "samples/product-review.json")
    generate_minutes(repo, FakeClient([minutes]), ident)
    ask_meeting(
        repo,
        FakeClient([tool_action(keywords=["文档"]), final_action(ids=["S005"])]),
        ident,
        "文档谁负责？",
    )
    texts = {s["segment_id"]: s["text"] for s in repo.segments(ident)}
    texts["S005"] = "现在改由小李负责。"
    repo.edit_transcript(ident, texts, 1)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    app.selectbox[0].set_value(ident).run()
    app.segmented_control[0].set_value("会议纪要").run()
    assert not app.exception
    assert any("历史纪要" in w.value for w in app.warning)
    next(b for b in app.button if b.label == "↗ S005 · 01:33").click().run()
    assert not app.exception
    assert any("暂时没有确定负责人" in text.value for text in app.text)
    app.segmented_control[0].set_value("问答 Agent").run()
    assert not app.exception
    assert any("这条回答基于旧转录" in w.value for w in app.warning)
