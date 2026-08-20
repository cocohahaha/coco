"""coco 冒烟测试：临时 COCO_ROOT + stub claude，覆盖全部关键接口，不碰真实数据。

运行：.venv/bin/python tests/smoke.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix="coco-smoke-"))
os.environ["COCO_ROOT"] = str(ROOT)
(ROOT / "coco.config.json").write_text(json.dumps(
    {"claude_bin": str(REPO / "tests" / "claude-stub")}, ensure_ascii=False))

sys.path.insert(0, str(REPO))
from fastapi.testclient import TestClient  # noqa: E402
from coco.server import app  # noqa: E402

c = TestClient(app)
FAIL = []


def check(name, cond, extra=""):
    print(("✓" if cond else "✗"), name, extra)
    if not cond:
        FAIL.append(name)


# 1. 粘贴文本导入 ×2（多方访谈场景）
r1 = c.post("/api/import-text", json={
    "content": "张三：我们最大的痛点是数据分散在各个系统。\n张三：置仓项目推了半年没进展。" * 20,
    "title": "访谈-张三", "date": "2026-08-19"})
check("import-text 张三", r1.status_code == 200, r1.text[:120])
r2 = c.post("/api/import-text", json={
    "content": "李四：数据其实不分散，是权限没打通。\n李四：智舱的事我建议先做试点。" * 20,
    "title": "访谈-李四", "date": "2026-08-20"})
check("import-text 李四", r2.status_code == 200)
mid1, mid2 = r1.json()["meeting_id"], r2.json()["meeting_id"]
check("import-text 空内容 400", c.post("/api/import-text", json={"content": "  "}).status_code == 400)

time.sleep(1.5)  # 等后台长期记忆线程（stub 秒回）

ms = c.get("/api/meetings").json()
check("会议列表 2 条且已转写", len(ms) == 2 and all(m["has_transcript"] for m in ms))
check("日期校准生效", any(m["date"] == "2026-08-19" for m in ms))

# 2. 上传 .srt 文字材料
srt = ("1\n00:00:01,000 --> 00:00:03,000\n王五：预算要走集团流程。\n\n"
       "2\n00:00:04,000 --> 00:00:06,000\n王五：最快十月。\n")
r = c.post("/api/upload", files={"file": ("访谈-王五.srt", srt.encode(), "text/plain")})
check("上传 srt 直接入库", r.status_code == 200 and r.json().get("text") is True, r.text[:120])
mid3 = r.json()["meeting_id"]
d = c.get(f"/api/meetings/{mid3}").json()
check("srt 转成 [mm:ss] 形态", "[00:01] 王五" in d["transcript"], d["transcript"][:120])

# 3. 路径导入（文件夹含 txt）
folder = ROOT / "import-folder"
folder.mkdir()
(folder / "会议纪要-老赵.txt").write_text("老赵说：先把报表自动化做了。", encoding="utf-8")
r = c.post("/api/import", json={"path": str(folder)})
check("文件夹路径导入 txt", r.status_code == 200 and r.json()["count"] == 1, r.text[:150])

# 4. 记忆三件套 + 词表
m = c.get("/api/memory").json()
check("memory 返回 glossary", "glossary" in m)
r = c.post("/api/memory", json={"glossary": "# 词表\n\n## 人名\n- 林炜（误写：林伟）\n\n## 专有名词\n- 智舱（误写：置仓）\n"})
check("保存词表", r.status_code == 200)
from coco.glossary import initial_prompt_terms, glossary_stats
from coco.config import GLOSSARY_PLACEHOLDER
check("词表注入转写提示", initial_prompt_terms() == "林炜、智舱", initial_prompt_terms())
check("占位模板 0 词条", glossary_stats(GLOSSARY_PLACEHOLDER) == {"names": 0, "terms": 0})

# 5. 词表提炼
r = c.post("/api/glossary/extract")
check("词表提炼", r.status_code == 200 and "## 专有名词" in r.json()["glossary"], r.text[:150])

# 6. 洞察三模式
modes = c.get("/api/track-modes").json()
check("洞察模式 3 种", [m["name"] for m in modes] == ["追踪", "深层信号", "调研综合"])
r = c.post("/api/track", json={"mode": "调研综合", "focus": ""})
check("调研综合（全部）", r.status_code == 200 and "调研综合" in r.json()["name"], r.text[:150])
r = c.post("/api/track", json={"mode": "深层信号", "ids": [mid1, mid2], "focus": "智舱"})
check("深层信号（限定2场+焦点）", r.status_code == 200, r.text[:150])
check("非法模式 400", c.post("/api/track", json={"mode": "xx"}).status_code == 400)
check("限定1场 400", c.post("/api/track", json={"ids": [mid1]}).status_code == 400)
check("重复会议 id 400", c.post("/api/track", json={"ids": [mid1, mid1]}).status_code == 400)
ra = c.post("/api/track", json={"focus": "A/B 测试", "ids": [mid1, mid2]})
check("斜杠焦点安全", ra.status_code == 200 and "/A" not in ra.json()["name"], ra.text[:120])
rb = c.post("/api/track", json={"focus": "A/B 测试", "ids": [mid1, mid2]})
check("同分钟不覆盖", rb.json()["name"] != ra.json()["name"])

# 7. 会前调查
r = c.post("/api/prep", json={"topic": "与智舱项目组对齐二期排期",
                              "people": "张三,李四", "goal": "拿到排期承诺"})
check("会前调查生成", r.status_code == 200, r.text[:150])
prep_name = r.json()["name"]
preps = c.get("/api/preps").json()
check("会前调查历史", len(preps) == 1 and preps[0]["name"] == prep_name)
check("会前调查下载", c.get(f"/api/download/prep/{prep_name}").status_code == 200)
check("prep 空主题 400", c.post("/api/prep", json={"topic": " "}).status_code == 400)

# 8. 知识底座
k = c.get("/api/knowledge").json()
check("底座统计", k["stats"]["meetings"] == 4 and k["stats"]["glossary"] >= 2,
      json.dumps(k["stats"], ensure_ascii=False))
check("人物点选名单", "林炜" in k["person_names"], str(k["person_names"]))

# 9. 人名与术语校正（单场，stub 原样返回）
r = c.post(f"/api/fix-names/{mid1}")
check("单场校正", r.status_code == 200, r.text[:120])
check("校正后保留原稿备份", c.get(f"/api/meetings/{mid1}").json()["has_raw"] is True)

# 10. 导出（含 prep 分组与穿越防护）
man = c.get("/api/export/manifest").json()
check("导出清单含会前调查", len(man.get("preps", [])) == 1, str(list(man.keys())))
r = c.post("/api/export", json={"items": [
    {"type": "transcript", "id": mid1},
    {"type": "prep", "name": prep_name},
    {"type": "tracking", "name": "../evil"}]})
check("zip 导出", r.status_code == 200 and r.headers["content-type"] == "application/zip")

# 11. 删除 prep
check("删除会前调查", c.delete(f"/api/preps/{prep_name}").status_code == 200)
check("删除后列表空", c.get("/api/preps").json() == [])

# 12. 旧接口回归
check("模板列表含跟进草稿", any(t["name"] == "跟进草稿" for t in c.get("/api/templates").json()))
check("生成跟进草稿报告", c.post("/api/report", json={"id": mid2, "template": "跟进草稿"}).status_code == 200)
check("对话", c.post("/api/ask", json={"question": "李四怎么看？", "ids": [mid2]}).status_code == 200)
check("每日简报", c.post("/api/brief", json={"date": "2026-08-20"}).status_code == 200)
check("搜索", len(c.get("/api/search", params={"q": "痛点"}).json()) >= 1)
check("配置读取", c.get("/api/config").status_code == 200)
check("全库校正预览（不执行）", c.post("/api/fix-all-names", json={}).json().get("preview") is True)

print("\n" + ("全部通过 ✓" if not FAIL else f"失败 {len(FAIL)} 项：{FAIL}"))
sys.exit(1 if FAIL else 0)
