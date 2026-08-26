"""coco smoke test: temporary COCO_ROOT + stub claude; exercises every API surface
without touching real data or spending model quota.

Run: .venv/bin/python tests/smoke.py   (Windows: .venv\\Scripts\\python tests\\smoke.py)
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix="coco-smoke-"))
os.environ["COCO_ROOT"] = str(ROOT)
STUB = REPO / "tests" / "claude-stub"
if os.name == "nt":  # Windows ignores the shebang: wrap the stub in a .cmd using this interpreter
    STUB = ROOT / "claude-stub.cmd"
    STUB.write_text(f'@"{sys.executable}" -X utf8 "{REPO / "tests" / "claude-stub"}" %*\n', encoding="utf-8")
CONFIG = ROOT / "coco.config.json"


def write_cfg(**extra):
    CONFIG.write_text(json.dumps({"claude_bin": str(STUB), "ui_language": "zh-CN", **extra},
                                 ensure_ascii=False), encoding="utf-8")


write_cfg()

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
from fastapi.testclient import TestClient  # noqa: E402
from coco.server import app  # noqa: E402

ZH = {"X-Coco-Lang": "zh-CN"}
EN = {"X-Coco-Lang": "en"}
FR = {"X-Coco-Lang": "fr"}
c = TestClient(app, headers=ZH)
FAIL = []


def check(name, cond, extra=""):
    print(("✓" if cond else "✗"), name, extra)
    if not cond:
        FAIL.append(name)


def stream(path, body, headers=None):
    """Collect SSE events from a streaming endpoint → (deltas, done, error)."""
    deltas, done, error = [], None, None
    with c.stream("POST", path, json=body, headers=headers or ZH) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
        for frame in buf.split("\n\n"):
            ev, data = None, ""
            for line in frame.splitlines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].strip()
            if not ev or not data:
                continue
            d = json.loads(data)
            if ev == "delta":
                deltas.append(d["text"])
            elif ev == "done":
                done = d
            elif ev == "error":
                error = d["detail"]
    return deltas, done, error


# 0. i18n completeness + basic language plumbing
import check_i18n  # noqa: E402
check("i18n 三语言包键完整", check_i18n.main() == 0)
r = c.get("/api/i18n?lang=fr").json()
check("i18n fr 返回字典", r["lang"] == "fr" and r["strings"]["header"]["upload"].startswith("↑") and len(r["available"]) == 3)
check("错误信息跟随请求语言 fr", "Contenu vide" in c.post("/api/import-text", json={"content": " "}, headers=FR).json()["detail"])
check("错误信息跟随请求语言 zh", "内容为空" in c.post("/api/import-text", json={"content": " "}).json()["detail"])
check("Accept-Language 兜底", c.post("/api/import-text", json={"content": " "},
      headers={"X-Coco-Lang": "", "Accept-Language": "en-US,en;q=0.9"}).json()["detail"] == "Empty content")

# 1. pasted text ×2 (multi-interview scenario)
r1 = c.post("/api/import-text", json={
    "content": "张三：我们最大的痛点是数据分散在各个系统。\n张三：置仓项目推了半年没进展。" * 20,
    "title": "访谈-张三", "date": "2026-08-19"})
check("import-text 张三", r1.status_code == 200, r1.text[:120])
r2 = c.post("/api/import-text", json={
    "content": "李四：数据其实不分散，是权限没打通。\n李四：智舱的事我建议先做试点。" * 20,
    "title": "访谈-李四", "date": "2026-08-20"})
check("import-text 李四", r2.status_code == 200)
mid1, mid2 = r1.json()["meeting_id"], r2.json()["meeting_id"]

time.sleep(2)  # background long-term memory threads (stub answers instantly)

ms = c.get("/api/meetings").json()
check("会议列表 2 条且已转写", len(ms) == 2 and all(m["has_transcript"] for m in ms))
check("日期校准生效", any(m["date"] == "2026-08-19" for m in ms))
check("来源标签本地化", all(m["source_label"] == "粘贴文本" for m in ms), str([m["source_label"] for m in ms]))
d = c.get(f"/api/meetings/{mid1}").json()
check("转写头部为中文标签", "- 日期：" in d["transcript"] or "- 日期:" in d["transcript"], d["transcript"][:80])

# 2. long-term memory via incremental (delta) merge
mem = c.get("/api/memory").json()["longterm"]
check("增量合并后有四个章节", all(h in mem for h in ("## 人物", "## 项目与客户", "## 承诺与决定", "## 术语与说法")), mem[:200])
check("增量合并不重复条目", mem.count("**林炜**") == 1, str(mem.count("**林炜**")))

from coco.ai import apply_delta  # noqa: E402
from coco.i18n import detect_headings_lang  # noqa: E402
old = ("# 长期记忆\n\n## 人物\n- **林炜**：产品负责人\n  - 6 月说排期没问题\n- **张三**：IT\n\n"
       "## 承诺与决定\n- 林炜 承诺 8/25 前给出二期排期 [a] 状态：进行中\n")
delta = ("## 人物\n- **林炜**：产品负责人，原说排期没问题 [a] → 现说要延期 [b]\n- **王五**：财务\n\n"
         "## 承诺与决定\n- 林炜 承诺 8/25 前给出二期排期 [a] 状态：已兑现 [b]\n\n## 术语与说法\n- **智舱**：座舱项目\n")
merged = apply_delta(old, delta, "zh-CN")
check("apply_delta 同名替换", merged.count("**林炜**") == 1 and "现说要延期" in merged and "6 月说排期" not in merged, merged)
check("apply_delta 新增条目", "**王五**" in merged and "**张三**" in merged)
check("apply_delta 承诺模糊匹配替换", merged.count("8/25") == 1 and "已兑现" in merged)
check("apply_delta 新章节按语言创建", "## 术语与说法" in merged and merged.index("## 承诺与决定") < merged.index("## 术语与说法"))
check("apply_delta 保留标题与章节顺序", merged.startswith("# 长期记忆") and merged.index("## 人物") < merged.index("## 承诺与决定"))
en_old = "# Long-term memory\n\n## People\n- **Lin Wei**: lead\n"
en_merged = apply_delta(en_old, "## Commitments & decisions\n- Lin Wei: schedule by 8/25 [x] in progress\n", "en")
check("apply_delta 英文标题识别", "## Commitments & decisions" in en_merged and "## People" in en_merged)
check("detect_headings_lang", detect_headings_lang(old) == "zh-CN" and detect_headings_lang(en_old) == "en")
twice = apply_delta("# 长期记忆\n\n## 承诺与决定\n- 王五 决定（8/17）：李四 不进 AI 试点 [a] 状态：待确认\n",
                    "## 承诺与决定\n- 王五 决定（8/17）：李四 不进 AI 试点，8/26 需重新确认 [a][b] 状态：待确认\n"
                    "- 王五 决定（8/17）：李四 进培训序列 [b] 状态：已生效\n", "zh-CN")
check("apply_delta 同一条目只被替换一次（相似的第二条追加）", twice.count("- 王五") == 2 and "重新确认" in twice and "培训序列" in twice, twice)
from coco.ai import _has_all_sections  # noqa: E402
check("全量重写校验要求四章节齐全", _has_all_sections(old) is False and _has_all_sections(old + "\n## 项目与客户\n\n## 术语与说法\n") is True
      and _has_all_sections("## 人物\n## 项目与客户\n## 承诺与决定\n## 术语与说法\n") is False)

# 3. .srt upload
srt = ("1\n00:00:01,000 --> 00:00:03,000\n王五：预算要走集团流程。\n\n"
       "2\n00:00:04,000 --> 00:00:06,000\n王五：最快十月。\n")
r = c.post("/api/upload", files={"file": ("访谈-王五.srt", srt.encode(), "text/plain")})
check("上传 srt 直接入库", r.status_code == 200 and r.json().get("text") is True, r.text[:120])
mid3 = r.json()["meeting_id"]
d = c.get(f"/api/meetings/{mid3}").json()
check("srt 转成 [mm:ss] 形态", "[00:01] 王五" in d["transcript"], d["transcript"][:120])
check("has_segments 上报", d["has_segments"] is True)

# 4. folder import
folder = ROOT / "import-folder"
folder.mkdir()
(folder / "会议纪要-老赵.txt").write_text("老赵说：先把报表自动化做了。", encoding="utf-8")
r = c.post("/api/import", json={"path": str(folder)})
check("文件夹路径导入 txt", r.status_code == 200 and r.json()["count"] == 1, r.text[:150])

# 5. memory trio + glossary
m = c.get("/api/memory").json()
check("memory 返回 glossary", "glossary" in m)
r = c.post("/api/memory", json={"glossary": "# 词表\n\n## 人名\n- 林炜（误写：林伟）\n\n## 专有名词\n- 智舱（误写：置仓）\n"})
check("保存词表", r.status_code == 200)
from coco.glossary import initial_prompt_terms, glossary_stats, parse_glossary  # noqa: E402
from coco.i18n import memory_placeholder  # noqa: E402
check("词表注入转写提示", initial_prompt_terms() == "林炜、智舱", initial_prompt_terms())
check("占位模板 0 词条（三语）", all(glossary_stats(memory_placeholder("glossary", l)) == {"names": 0, "terms": 0}
                              for l in ("zh-CN", "en", "fr")))
g_en = parse_glossary("# Glossary\n## Names\n- Lin Wei (misheard: Lin Way) | lead\n## Terms\n- SmartCabin (variants: Smart Cabinet)\n")
check("英文词表解析", [e["term"] for e in g_en["names"]] == ["Lin Wei"] and g_en["names"][0]["wrong"] == ["Lin Way"]
      and g_en["terms"][0]["wrong"] == ["Smart Cabinet"], str(g_en))

# 6. glossary extraction
r = c.post("/api/glossary/extract")
check("词表提炼", r.status_code == 200 and "## 专有名词" in r.json()["glossary"], r.text[:150])

# 7. insight modes (ids + localized names, legacy names accepted)
modes = c.get("/api/track-modes").json()
check("洞察模式 3 种（id + 中文名）", [m["id"] for m in modes] == ["track", "signals", "synthesis"]
      and [m["name"] for m in modes] == ["追踪", "深层信号", "调研综合"], str(modes))
check("洞察模式英文名", [m["name"] for m in c.get("/api/track-modes", headers=EN).json()] == ["Tracking", "Deep signals", "Research synthesis"])
r = c.post("/api/track", json={"mode": "调研综合", "focus": ""})
check("调研综合（旧中文名，全部）", r.status_code == 200 and "synthesis" in r.json()["name"] and r.json()["mode_label"] == "调研综合", r.text[:150])
r = c.post("/api/track", json={"mode": "signals", "ids": [mid1, mid2], "focus": "智舱"})
check("深层信号（限定2场+焦点）", r.status_code == 200, r.text[:150])
check("非法模式 400", c.post("/api/track", json={"mode": "xx"}).status_code == 400)
check("限定1场 400", c.post("/api/track", json={"ids": [mid1]}).status_code == 400)
check("重复会议 id 400", c.post("/api/track", json={"ids": [mid1, mid1]}).status_code == 400)
ra = c.post("/api/track", json={"focus": "A/B 测试", "ids": [mid1, mid2]})
check("斜杠焦点安全", ra.status_code == 200 and "/A" not in ra.json()["name"], ra.text[:120])
rb = c.post("/api/track", json={"focus": "A/B 测试", "ids": [mid1, mid2]})
check("同分钟不覆盖", rb.json()["name"] != ra.json()["name"])
deltas, done, err = stream("/api/stream/track", {"mode": "track", "ids": [mid1, mid2]})
check("洞察流式：有增量 + done", len(deltas) >= 2 and done and "track" in done["name"] and err is None, str(err))

# 8. prep
r = c.post("/api/prep", json={"topic": "与智舱项目组对齐二期排期", "people": "张三,李四", "goal": "拿到排期承诺"})
check("会前调查生成", r.status_code == 200, r.text[:150])
prep_name = r.json()["name"]
preps = c.get("/api/preps").json()
check("会前调查历史", len(preps) == 1 and preps[0]["name"] == prep_name)
check("会前调查标题本地化", preps[0]["content"].startswith("# 会前调查"), preps[0]["content"][:40])
check("会前调查下载", c.get(f"/api/download/prep/{prep_name}").status_code == 200)
check("prep 空主题 400", c.post("/api/prep", json={"topic": " "}).status_code == 400)
deltas, done, err = stream("/api/stream/prep", {"topic": "复盘"})
check("会前流式", len(deltas) >= 2 and done and done["name"] and err is None, str(err))

# 9. knowledge base
k = c.get("/api/knowledge").json()
check("底座统计", k["stats"]["meetings"] == 4 and k["stats"]["glossary"] >= 2 and k["stats"]["persons"] >= 2,
      json.dumps(k["stats"], ensure_ascii=False))
check("人物点选名单", "林炜" in k["person_names"], str(k["person_names"]))

# 10. name / term correction (single meeting; stub echoes)
r = c.post(f"/api/fix-names/{mid1}")
check("单场校正", r.status_code == 200, r.text[:120])
check("校正后保留原稿备份", c.get(f"/api/meetings/{mid1}").json()["has_raw"] is True)

# 11. export (prep group + traversal guard)
man = c.get("/api/export/manifest").json()
check("导出清单含会前调查", len(man.get("preps", [])) == 2, str(list(man.keys())))
r = c.post("/api/export", json={"items": [
    {"type": "transcript", "id": mid1}, {"type": "prep", "name": prep_name}, {"type": "tracking", "name": "../evil"}]})
check("zip 导出", r.status_code == 200 and r.headers["content-type"] == "application/zip")
names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
check("zip 内目录名本地化", any(n.startswith("会前调查/") for n in names) and any(n.endswith("/转写.md") for n in names), str(names))

# 12. delete prep
check("删除会前调查", c.delete(f"/api/preps/{prep_name}").status_code == 200)
check("删除后剩一份（流式生成的那份）", len(c.get("/api/preps").json()) == 1)

# 13. templates / reports (ids, legacy names, localized labels, streaming)
tpls = c.get("/api/templates").json()
check("模板列表 id+中文名", [t["id"] for t in tpls][:2] == ["minutes", "actions"] and tpls[0]["name"] == "纪要", str(tpls[:2]))
check("模板列表英文名", c.get("/api/templates", headers=EN).json()[8]["name"] == "Follow-up draft")
r = c.post("/api/report", json={"id": mid2, "template": "跟进草稿"})
check("旧中文模板名生成报告", r.status_code == 200 and r.json()["name"].startswith("followup-") and r.json()["label"].startswith("跟进草稿 "), r.text[:200])
r = c.post("/api/report", json={"id": mid2, "template": "minutes"}, headers=EN)
check("英文请求：报告标签英文", r.status_code == 200 and r.json()["label"].startswith("Minutes "), r.text[:200])
check("英文请求：报告内容英文提示词", "Stub title" in r.json()["content"], r.json()["content"][:60])
check("未知模板 400", c.post("/api/report", json={"id": mid2, "template": "nope"}).status_code == 400)
deltas, done, err = stream("/api/stream/report", {"id": mid2, "template": "actions"})
check("报告流式：增量 + done 含 name/label", len(deltas) >= 2 and done and done["name"].startswith("actions-") and err is None, str(err))
deltas, done, err = stream("/api/stream/report", {"id": "no-such-meeting", "template": "minutes"})
check("报告流式：错误事件", done is None and err and "找不到会议" in err, str(err))
d = c.get(f"/api/meetings/{mid2}").json()
check("会议详情报告带 label", all("label" in rp for rp in d["report_list"]) and len(d["report_list"]) == 3)
check("旧报告文件名也能映射标签", c.get(f"/api/meetings/{mid2}", headers=EN).json()["report_list"][0]["label"].split(" ")[0] in ("Follow-up", "Minutes", "Action"))
check("对话", c.post("/api/ask", json={"question": "李四怎么看？", "ids": [mid2]}).status_code == 200)
deltas, done, err = stream("/api/stream/ask", {"question": "李四怎么看？", "ids": [mid2]})
check("对话流式", len(deltas) >= 2 and done and done["answer"] and err is None, str(err))
check("每日简报", c.post("/api/brief", json={"date": "2026-08-20"}).status_code == 200)
deltas, done, err = stream("/api/stream/brief", {"date": "2026-08-19"})
check("简报流式", done and done["date"] == "2026-08-19" and err is None, str(err))
check("搜索", len(c.get("/api/search", params={"q": "痛点"}).json()) >= 1)
check("配置读取", c.get("/api/config").status_code == 200)
check("全库校正预览（不执行）", c.post("/api/fix-all-names", json={}).json().get("preview") is True)

# 14. weekly
from coco.ai import week_bounds  # noqa: E402
s_, e_, w_ = week_bounds("2026-08-19")
check("周界计算", (s_, e_, w_) == ("2026-08-17", "2026-08-23", "2026-W34"), f"{s_} {e_} {w_}")
r = c.post("/api/weekly", json={"date": "2026-08-19"})
check("生成周报", r.status_code == 200 and r.json()["week"] == "2026-W34", r.text[:150])
wl = c.get("/api/weeklies").json()
check("周报列表", any(w["week"] == "2026-W34" and w["content"] for w in wl), str([w["week"] for w in wl]))
check("周报下载", c.get("/api/download/weekly/2026-W34").status_code == 200)
check("无会议的周 400", c.post("/api/weekly", json={"date": "2000-01-03"}).status_code == 400)
check("周报删除", c.delete("/api/weeklies/2026-W34").status_code == 200)
deltas, done, err = stream("/api/stream/weekly", {"date": "2026-08-19"})
check("周报流式", done and done["week"] == "2026-W34" and err is None, str(err))
check("导出清单含周报字段", "weeklies" in c.get("/api/export/manifest").json())

# 15. lenient glossary parsing
g = parse_glossary(
    "# 词表\n## 人名\n- 林炜（产品负责人）\n- **张三**\n- ---\n"
    "- 这是一条很长的说明文字，不应该被当成词条收进词表里面去，明显超过了四十个字符的长度限制所以会被过滤掉\n"
    "## 专有名词\n- 智舱（误写：置仓)｜车机\n")
check("词表宽容解析", [e["term"] for e in g["names"]] == ["林炜", "张三"] and g["names"][0]["note"] == "产品负责人"
      and g["terms"][0]["wrong"] == ["置仓"], str(g))

# 16. VTT speaker tags
vtt = "WEBVTT\n\n00:01.000 --> 00:03.000\n<v 林炜>排期下周给。</v>\n"
r = c.post("/api/upload", files={"file": ("带说话人.vtt", vtt.encode(), "text/plain")})
check("上传 vtt", r.status_code == 200, r.text[:120])
d = c.get(f"/api/meetings/{r.json()['meeting_id']}").json()
check("VTT 说话人保留", "林炜: 排期下周给。" in d["transcript"], d["transcript"][-80:])

# 17. GBK text import
gbk_file = ROOT / "gbk笔记.txt"
gbk_file.write_bytes("老王：这个季度回款有问题，供应商在催。".encode("gb18030"))
r = c.post("/api/import", json={"path": str(gbk_file)})
check("GBK 导入", r.status_code == 200, r.text[:150])
d = c.get(f"/api/meetings/{r.json()['imported'][0]['meeting_id']}").json()
check("GBK 解码正确", "回款有问题" in d["transcript"], d["transcript"][-60:])

# 18. Whisper export folder: audio + same-stem transcripts → one meeting, audio archived
pair = ROOT / "whisper-export"
pair.mkdir()
(pair / "rec1.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n试点先跑两周。\n", encoding="utf-8")
(pair / "rec1.txt").write_text("试点先跑两周。", encoding="utf-8")
(pair / "rec1.m4a").write_bytes(b"fake-audio")
r = c.post("/api/import", json={"path": str(pair)})
ok = r.status_code == 200 and r.json()["count"] == 1 and r.json()["imported"][0].get("text") is True
check("同名音频+转写只建一场", ok, r.text[:200])
from coco.library import find_meeting  # noqa: E402
m = find_meeting(r.json()["imported"][0]["meeting_id"])
check("配套音频已归档", m.audio_file is not None and m.audio_file.name == "audio.m4a")

# 19. Word (.docx) transcripts
def make_docx(paras):
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{x}</w:t></w:r></w:p>" for x in paras)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()

docx = make_docx(["发言人1 00:00:05", "我们先看渠道预算。", "发言人2 00:00:12", "渠道预算要等集团批。"])
r = c.post("/api/upload", files={"file": ("腾讯会议-渠道复盘.docx", docx, "application/octet-stream")})
check("上传 docx 直接入库", r.status_code == 200 and r.json().get("text") is True, r.text[:150])
d = c.get(f"/api/meetings/{r.json()['meeting_id']}").json()
check("docx 正文按段落抽出", "渠道预算要等集团批" in d["transcript"] and "发言人1 00:00:05" in d["transcript"], d["transcript"][-120:])
bad = c.post("/api/upload", files={"file": ("坏.docx", b"not a zip", "application/octet-stream")})
check("坏 docx 400 且提示", bad.status_code == 400 and "Word" in bad.json()["detail"], bad.text[:120])
dd = ROOT / "docx-folder"; dd.mkdir()
(dd / "复盘.docx").write_bytes(make_docx(["docx 版本"]))
(dd / "复盘.txt").write_text("txt 版本", encoding="utf-8")
r = c.post("/api/import", json={"path": str(dd)})
d = c.get(f"/api/meetings/{r.json()['imported'][0]['meeting_id']}").json()
check("同名 docx+txt 只建一场且取 txt", r.json()["count"] == 1 and "txt 版本" in d["transcript"], r.text[:150])

# 20. more transcript formats: sbv / lrc / ass / csv / tsv / json (speakers) / html / rtf / eml / odt / pdf / pasted srt
def up(name, content, mime="text/plain"):
    r = c.post("/api/upload", files={"file": (name, content if isinstance(content, bytes) else content.encode("utf-8"), mime)})
    if r.status_code != 200:
        return r, None
    return r, c.get(f"/api/meetings/{r.json()['meeting_id']}").json()

r, d = up("yt.sbv", "0:00:01.000,0:00:03.000\n第一句字幕\n\n0:00:04.500,0:00:06.000\n第二句字幕\n")
check("sbv 导入", r.status_code == 200 and "[00:01] 第一句字幕" in d["transcript"] and "[00:04] 第二句字幕" in d["transcript"], r.text[:120])
r, d = up("song.lrc", "[ti:test]\n[00:12.50]第一行\n[01:05.00]第二行\n")
check("lrc 导入", r.status_code == 200 and "[00:12] 第一行" in d["transcript"] and "[01:05] 第二行" in d["transcript"], r.text[:120])
ass = ("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       "Dialogue: 0,0:00:01.00,0:00:03.00,Default,林炜,0,0,0,,{\\b1}排期\\N下周给\n")
r, d = up("sub.ass", ass)
check("ass 导入含说话人", r.status_code == 200 and "[00:01] 林炜: 排期 下周给" in d["transcript"], d["transcript"][-80:] if d else r.text[:120])
csv_txt = "speaker,start,text\n张三,00:00:05,数据分散\n李四,12.5,权限没打通\n"
r, d = up("export.csv", csv_txt)
check("csv 带表头导入", r.status_code == 200 and "[00:05] 张三: 数据分散" in d["transcript"] and "[00:12] 李四: 权限没打通" in d["transcript"], d["transcript"][-100:] if d else r.text[:120])
r, d = up("whisper.tsv", "start\tend\ttext\n0\t2500\t第一段\n65000\t70000\t第二段\n")
check("whisper tsv 毫秒换算", r.status_code == 200 and "[00:00] 第一段" in d["transcript"] and "[01:05] 第二段" in d["transcript"], d["transcript"][-80:] if d else r.text[:120])
otter = json.dumps({"title": "x", "sentences": [
    {"speaker_name": "Lin", "start_time": 1.2, "text": "Hello there"},
    {"speaker_name": "Zhang", "start_time": 65.0, "text": "Budget first"}]})
r, d = up("otter.json", otter, "application/json")
check("json 通用结构（sentences + speaker）", r.status_code == 200 and "[00:01] Lin: Hello there" in d["transcript"] and "[01:05] Zhang: Budget first" in d["transcript"], d["transcript"][-100:] if d else r.text[:120])
aws = json.dumps({"results": {"transcripts": [{"transcript": "整段 AWS 文本"}], "items": [{"type": "punctuation", "alternatives": [{"content": "."}]}]}})
r, d = up("aws.json", aws, "application/json")
check("json AWS Transcribe 结构", r.status_code == 200 and "整段 AWS 文本" in d["transcript"], r.text[:120])
ms_json = json.dumps([{"start": 90000, "end": 92000, "text": "毫秒起点"}, {"start": 3600000, "end": 3605000, "text": "一小时处"}])
r, d = up("ms.json", ms_json, "application/json")
check("json 毫秒偏移识别", r.status_code == 200 and "[01:30] 毫秒起点" in d["transcript"] and "[1:00:00] 一小时处" in d["transcript"], d["transcript"][-80:] if d else r.text[:120])
html = "<html><head><style>p{}</style><script>x=1</script></head><body><h1>纪要</h1><p>第一段&amp;内容</p><div>第二段</div></body></html>"
r, d = up("page.html", html, "text/html")
check("html 导入去标签", r.status_code == 200 and "第一段&内容" in d["transcript"] and "x=1" not in d["transcript"] and "第二段" in d["transcript"], d["transcript"][-80:] if d else r.text[:120])
rtf = r"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}{\colortbl;\red0\green0\blue0;}\f0\par Hello \b bold\b0  world\par \u20250? next\par}"
r, d = up("note.rtf", rtf)
check("rtf 导入", r.status_code == 200 and "Hello bold world" in d["transcript"] and "会 next" in d["transcript"], d["transcript"][-80:] if d else r.text[:120])
eml = ("From: a@x.com\r\nTo: b@y.com\r\nSubject: =?utf-8?b?6aKE566X?=\r\nDate: Mon, 1 Jan 2026 10:00:00 +0000\r\n"
       "Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n预算要走集团流程。\r\n")
r, d = up("mail.eml", eml.encode("utf-8"), "message/rfc822")
check("eml 导入含头部与正文", r.status_code == 200 and "Subject: 预算" in d["transcript"] and "集团流程" in d["transcript"], d["transcript"][-100:] if d else r.text[:120])
def make_odt(paras):
    T = 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    body = "".join(f"<text:p>{x}</text:p>" for x in paras)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", f'<?xml version="1.0"?><office:document-content {T}><office:body><office:text>{body}</office:text></office:body></office:document-content>')
    return buf.getvalue()
r, d = up("doc.odt", make_odt(["ODT 第一段", "ODT 第二段"]), "application/vnd.oasis.opendocument.text")
check("odt 导入", r.status_code == 200 and "ODT 第一段" in d["transcript"] and "ODT 第二段" in d["transcript"], d["transcript"][-80:] if d else r.text[:120])
r = c.post("/api/upload", files={"file": ("scan.pdf", b"%PDF-1.4 not really", "application/pdf")})
try:
    import pypdf  # noqa: F401
    check("pdf 坏文件 400", r.status_code == 400, r.text[:120])
except ImportError:
    check("pdf 缺 pypdf 时给出安装提示", r.status_code == 400 and "pypdf" in r.json()["detail"], r.text[:120])
r = c.post("/api/import-text", json={"content": srt, "title": "粘贴的 srt"})
check("粘贴 SRT 自动识别", r.status_code == 200 and r.json()["format"] == "srt", r.text[:120])
d = c.get(f"/api/meetings/{r.json()['meeting_id']}").json()
check("粘贴 SRT 保留时间轴", "[00:01] 王五" in d["transcript"], d["transcript"][-80:])
r = c.post("/api/import-text", json={"content": otter, "title": "粘贴的 json"})
check("粘贴 JSON 自动识别", r.status_code == 200 and r.json()["format"] == "json", r.text[:120])
cfg = c.get("/api/config").json()
check("config text_exts 含新格式", all(e in cfg["text_exts"] for e in (".pdf", ".rtf", ".html", ".csv", ".tsv", ".sbv", ".lrc", ".ass", ".odt", ".eml")))

# 21. transcript downloads in several formats
r = c.get(f"/api/download/meeting/{mid3}/transcript?fmt=srt")
check("转写下载 srt", r.status_code == 200 and "00:00:01,000 --> 00:00:03,000" in r.text and "王五" in r.text, r.text[:120])
r = c.get(f"/api/download/meeting/{mid3}/transcript?fmt=vtt")
check("转写下载 vtt", r.status_code == 200 and r.text.startswith("WEBVTT") and "00:00:04.000 --> 00:00:06.000" in r.text, r.text[:120])
r = c.get(f"/api/download/meeting/{mid3}/transcript?fmt=txt")
check("转写下载 txt", r.status_code == 200 and r.text.startswith("王五：预算") and "[" not in r.text, r.text[:120])
check("转写下载 json", c.get(f"/api/download/meeting/{mid3}/transcript?fmt=json").status_code == 200)
check("转写下载 md 默认", c.get(f"/api/download/meeting/{mid3}/transcript").status_code == 200)
r = c.get(f"/api/download/meeting/{mid1}/transcript?fmt=srt")
check("无时间轴转写不能导出 srt", r.status_code == 400 and "时间轴" in r.json()["detail"], r.text[:120])
r = c.get(f"/api/download/meeting/{mid1}/transcript?fmt=txt")
check("纯文本转写导出 txt 去头部", r.status_code == 200 and "张三：我们最大的痛点" in r.text and not r.text.startswith("#"), r.text[:80])
check("非法格式 400", c.get(f"/api/download/meeting/{mid1}/transcript?fmt=doc").status_code == 400)

# 22. AI channels: fake OpenAI- and Anthropic-compatible servers on localhost
class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        FakeAPI.seen.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if self.path.endswith("/chat/completions"):
            for piece in ("po", "ng"):
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:  # /v1/messages
            self.wfile.write(b'event: message_start\ndata: {"type":"message_start"}\n\n')
            for piece in ("po", "ng"):
                self.wfile.write(("event: content_block_delta\ndata: " + json.dumps(
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}) + "\n\n").encode())
            self.wfile.write(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

FakeAPI.seen = []
srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from coco import providers  # noqa: E402
r = c.post("/api/settings/ai", json={
    "primary": {"type": "claude-cli"},
    "fast": {"type": "openai", "base_url": base + "/v1", "model": "fake-fast", "api_key": "sk-test"},
    "fast_same_as_primary": False, "tasks": {"memory": "fast", "glossary": "fast"}})
check("保存 AI 通道设置", r.status_code == 200 and r.json()["fast_configured"] is True and r.json()["fast"]["has_key"] is True
      and "api_key" not in r.json()["fast"], r.text[:200])
check("配置文件里有 key 但接口不回传", json.loads(CONFIG.read_text(encoding="utf-8"))["ai_profiles"]["fast"]["api_key"] == "sk-test")
t_ = providers.test_profile("fast")
check("OpenAI 兼容通道流式调用", t_["ok"] and t_["reply"] == "pong", str(t_))
hdr = lambda: {k.lower(): v for k, v in FakeAPI.seen[-1][1].items()}
check("OpenAI 通道带 Bearer 头", hdr().get("authorization") == "Bearer sk-test" and FakeAPI.seen[-1][2]["stream"] is True)
os.environ["FAKE_KEY"] = "env-key"
r = c.post("/api/settings/ai/test", json={"profile": "fast", "inline": {
    "type": "anthropic", "base_url": base, "model": "fake-pro", "api_key": "", "api_key_env": "FAKE_KEY"}})
check("Anthropic 兼容通道（inline + 环境变量 key）", r.status_code == 200 and r.json()["ok"] and r.json()["reply"] == "pong", r.text[:200])
check("Anthropic 通道带 x-api-key 头", hdr().get("x-api-key") == "env-key" and FakeAPI.seen[-1][0] == "/v1/messages")
check("任务映射保存", c.get("/api/settings/ai").json()["tasks"]["memory"] == "fast")
r = c.post("/api/settings/ai", json={"fast_same_as_primary": True})
check("后台通道回退为主通道", r.json()["fast_configured"] is False and providers.get_profile("fast")["type"] == "claude-cli")
check("非法通道类型 400", c.post("/api/settings/ai", json={"primary": {"type": "nope"}}).status_code == 400)
p_web = providers.get_profile("primary")
check("HTTP 通道不支持联网", not providers.supports_web({"type": "openai"}) and providers.supports_web(p_web))
srv.shutdown(); srv.server_close()
r = c.post("/api/settings/ai", json={"fast": {"type": "openai", "base_url": base + "/v1", "model": "x"}, "fast_same_as_primary": False})
t_ = providers.test_profile("fast")
check("通道不可达时报连接错误", not t_["ok"] and t_["error"], str(t_))
c.post("/api/settings/ai", json={"fast_same_as_primary": True})
check("claude 通道 timeout 文案", "超时" in providers.ModelError(providers.t("ai.timeout", seconds=5)).args[0])

# 23. language settings
r = c.post("/api/config", json={"ui_language": "fr", "output_language": "source"})
check("保存界面/输出语言", r.status_code == 200 and r.json()["ui_language"] == "fr" and r.json()["output_language"] == "source")
check("无头请求跟随配置语言", c.get("/api/i18n", headers={"X-Coco-Lang": ""}).json()["lang"] == "fr")
check("非法界面语言 400", c.post("/api/config", json={"ui_language": "xx"}).status_code == 400)
check("非法输出语言 400", c.post("/api/config", json={"output_language": "not a lang"}).status_code == 400)
check("memory_merge 校验", c.post("/api/config", json={"memory_merge": "nope"}).status_code == 400
      and c.post("/api/config", json={"memory_merge": "full"}).json()["memory_merge"] == "full")
c.post("/api/config", json={"ui_language": "zh-CN", "output_language": "ui", "memory_merge": "delta"})
from coco.i18n import lang_name, normalize  # noqa: E402
check("语言代码归一化", normalize("zh_CN.UTF-8") == "zh-CN" and normalize("en-GB") == "en" and normalize("fr-CH") == "fr" and normalize("xx") is None)
check("语言名用于提示词", lang_name("ja").startswith("日本語") and lang_name("zh-CN").startswith("简体中文"))
from coco import prompts  # noqa: E402
check("输出语言指令", "日本語" in prompts.language_directive("ja") and "简体中文" in prompts.language_directive("zh-CN")
      and prompts.get("zh-CN") is prompts.zh and prompts.get("ja") is prompts.en)

# 24. memory compaction job + clear placeholders
r = c.post("/api/memory/compact")
check("压缩长期记忆任务启动", r.status_code == 200 and r.json().get("job"), r.text[:120])
for _ in range(40):
    j = c.get(f"/api/jobs/{r.json()['job']}").json()
    if j["status"] != "running":
        break
    time.sleep(0.25)
check("压缩长期记忆完成", j["status"] == "done", str(j))
check("压缩后仍是中文标题", "## 人物" in c.get("/api/memory").json()["longterm"])
r = c.post("/api/memory/clear", json={"which": "longterm"})
check("清空长期记忆用当前语言占位", r.status_code == 200 and r.json()["content"].startswith("# 长期记忆"))
r = c.post("/api/memory/clear", json={"which": "glossary"}, headers=EN)
check("清空词表用英文占位", r.json()["content"].startswith("# Glossary") and "## Names" in r.json()["content"])
c.post("/api/memory", json={"glossary": "# 词表\n\n## 人名\n- 林炜（误写：林伟）\n\n## 专有名词\n- 智舱（误写：置仓）\n"})

# 25. no engine: text route still works, audio is refused with the transcript hint (typical Windows without faster-whisper)
cfg = c.get("/api/config").json()
check("config 上报能力字段", all(k in cfg for k in ("platform", "transcribe", "transcribe_device", "record", "text_exts", "ai", "available_languages")), str(list(cfg)))
n_before = len(c.get("/api/meetings").json())
write_cfg(transcribe_backend="none")
check("backend=none 时 transcribe 为空", c.get("/api/config").json()["transcribe"] == "" and c.get("/api/config").json()["transcribe_device"] == "")
r = c.post("/api/upload", files={"file": ("录音.wav", b"RIFF----WAVEfake", "audio/wav")})
check("无引擎上传音频 400 并指向文字稿", r.status_code == 400 and "faster-whisper" in r.json()["detail"], r.text[:160])
r = c.post("/api/upload", files={"file": ("rec.wav", b"RIFF----WAVEfake", "audio/wav")}, headers=EN)
check("无引擎提示英文", r.status_code == 400 and "no usable transcription engine" in r.json()["detail"], r.text[:160])
(ROOT / "solo.m4a").write_bytes(b"fake")
r = c.post("/api/import", json={"path": str(ROOT / "solo.m4a")})
check("无引擎路径导入音频 400", r.status_code == 400 and "文字稿" in r.json()["detail"], r.text[:160])
r = c.post("/api/upload", files={"file": ("纪要.txt", "无引擎也能导文字稿".encode(), "text/plain")})
check("无引擎上传文字稿照常入库", r.status_code == 200 and r.json().get("text") is True, r.text[:120])
check("拒绝的音频没有留下空会议", len(c.get("/api/meetings").json()) == n_before + 1)
mix = ROOT / "mixed-folder"; mix.mkdir()
(mix / "纪要.txt").write_text("文字稿内容", encoding="utf-8")
(mix / "另一段录音.m4a").write_bytes(b"fake")
r = c.post("/api/import", json={"path": str(mix)})
items = r.json()["imported"]
check("无引擎混合文件夹：文字稿照导、音频逐项报错并标记 audio",
      r.status_code == 200 and any(it.get("text") for it in items) and any(it.get("audio") for it in items), r.text[:200])
check("无引擎时 record 能力为 false", c.get("/api/config").json()["record"] is False)
check("无引擎录音 400", c.post("/api/record/start", json={"title": "x"}).status_code == 400)
bad_pair = ROOT / "bad-pair"; bad_pair.mkdir()
(bad_pair / "会.docx").write_bytes(b"not a zip"); (bad_pair / "会.m4a").write_bytes(b"fake")
n_mid = len(c.get("/api/meetings").json())
r = c.post("/api/import", json={"path": str(bad_pair)})
check("无引擎坏 docx+同名音频不回退建会议", r.json()["imported"][0].get("error") and len(c.get("/api/meetings").json()) == n_mid, r.text[:160])
write_cfg()
from coco.transcriber import detect_backend  # noqa: E402
check("backend 恢复 auto 后重新探测", detect_backend() in ("mlx", "faster", ""))

# 26. non-macOS: recording refused without leaving an empty meeting
import coco.server as srv_mod  # noqa: E402
_orig = srv_mod.record_supported
srv_mod.record_supported = lambda: False
n_before = len(c.get("/api/meetings").json())
r = c.post("/api/record/start", json={"title": "x"})
check("录音不支持时 400", r.status_code == 400 and "macOS" in r.json()["detail"], r.text[:120])
check("录音拒绝不建会议", len(c.get("/api/meetings").json()) == n_before)
check("config 上报 record=false", c.get("/api/config").json()["record"] is False)
srv_mod.record_supported = _orig

# 27. claude CLI resolution
from coco.ai import resolve_claude_bin  # noqa: E402
check("claude_bin 绝对路径解析", Path(resolve_claude_bin()).name.startswith("claude-stub"), resolve_claude_bin())

# 28. CLI in another language (help text + templates listing)
import subprocess  # noqa: E402
env = {**os.environ, "COCO_ROOT": str(ROOT), "COCO_LANG": "en", "PYTHONPATH": str(REPO)}
write_cfg(ui_language="auto")
out = subprocess.run([sys.executable, "-m", "coco", "templates"], capture_output=True, text=True, env=env, cwd=str(REPO))
check("CLI 英文输出", out.returncode == 0 and "Minutes" in out.stdout and "Tracking" in out.stdout, out.stdout[:120] + out.stderr[:200])
out = subprocess.run([sys.executable, "-m", "coco", "lang"], capture_output=True, text=True, env={**env, "COCO_LANG": "zh_CN.UTF-8"}, cwd=str(REPO))
check("CLI 中文输出（系统语言）", out.returncode == 0 and "界面语言" in out.stdout, out.stdout[:120] + out.stderr[:200])
out = subprocess.run([sys.executable, "-m", "coco", "ai", "show"], capture_output=True, text=True, env=env, cwd=str(REPO))
check("CLI ai show", out.returncode == 0 and "[primary]" in out.stdout and "Presets" in out.stdout, out.stdout[:200] + out.stderr[:200])
write_cfg()

print("\n" + ("全部通过 ✓" if not FAIL else f"失败 {len(FAIL)} 项：{FAIL}"))
sys.exit(1 if FAIL else 0)
