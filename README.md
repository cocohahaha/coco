# coco — 本地会议录音 / 转写 / AI 分析工具

[YouNavi](https://younavi.me) 的本地复刻版：导入已有录音、本地 Whisper 转写、AI 分析报告、每日简报，
全部数据留在本机，AI 分析通过本机 `claude` CLI 完成（无需额外 API key）。

## 快速开始

**最快方式：在 Finder 里双击 `coco.command`**（或终端运行 `./run.sh`）——
自动启动服务并打开浏览器，重复运行不会重复起服务。

```bash
cd "~/Documents/Tool Creation/录音"

./run.sh                           # 一键启动网页版 → http://127.0.0.1:8765
./bin/coco transcribe 某录音.m4a    # 命令行导入音频/视频并转写
```

网页版三种导入方式：**拖拽文件进窗口**、「上传音频」多选、「本地路径」粘贴文件或文件夹路径（文件夹=批量导入）。
多个文件会排队转写，完成一个出一个。转写、报告、每日简报都可在界面一键下载为 .md。

转写页还提供：

- **✎ 编辑**：直接改转写正文并保存，后续 AI 分析用的就是改过的版本
- **✦ AI 校对**：用 claude 修正同音字、专有名词、标点（不改写内容），
  原始机器稿自动备份为 `transcript.raw.md`；全局记忆里的人名/术语写法会作为校对依据
- **顶栏模型切换**：turbo（快）/ large（最准），对之后的转写生效

界面其他能力：

- **删除会议**：侧边栏条目悬停出现 ✕，删除后整个会议移入回收站 `library/_trash`，
  需要时可手动拖回 `library/` 恢复
- **全局搜索**：侧边栏顶部搜索框，搜全部转写与报告，点结果直达会议
- **长期记忆（自动）**：每场转写完成后，coco 自动提取人物、项目与客户、
  承诺与决定、术语，合并进 `memory/longterm.md` 并注入之后所有分析。
  「◈ 记忆」弹窗里有「长期记忆」页签可查看修订；不想要可 `coco config auto_memory false`
- **⛓ 追踪**：跨全部会议分析承诺履行、表态变化、反复未决的问题，可聚焦某个人/客户
- **跨全部会议提问**：对话框勾选后，提问范围从当前会议扩展到全部已转写会议

建议把 CLI 加入 PATH：
```bash
echo 'alias coco="~/Documents/Tool\ Creation/录音/bin/coco"' >> ~/.zshrc
```

## 全部命令

| 命令 | 作用 |
|---|---|
| `coco transcribe <文件…>` | 导入音频/视频转写（`--model turbo` 换快速模型） |
| `coco list` / `coco show <会议>` | 查看会议库 / 某条转写 |
| `coco ask "问题" [会议…]` | 对会议内容提问（默认最近一条，可引用多条） |
| `coco report <会议> -t 模板` | 生成分析报告（`coco templates` 看全部模板） |
| `coco brief [日期]` | 每日简报：汇总当天所有会议的行动项与洞察 |
| `coco track [焦点]` | 跨会议追踪：承诺履行/表态变化/未决问题 |
| `coco search <词>` | 全文搜索所有转写和报告 |
| `coco delete <会议>` | 删除会议（移入回收站 `library/_trash`） |
| `coco memory [内容]` | 查看/追加全局记忆（人名、术语，注入每次分析） |
| `coco memorize [会议\|--all]` | 手动触发长期记忆提取（转写后默认自动做） |
| `coco watch <文件夹>` | 监控文件夹，新音频自动转写（适合接语音备忘录、Plaud 导出目录等） |
| `coco record [标题]` | （可选）麦克风录音，Ctrl+C 停止后自动转写 |
| `coco web` | 启动本地 Web 界面（上传/本地导入/报告/对话/简报/记忆） |
| `coco config [键 值]` | 查看/修改配置 |

## 分析模板

纪要 · 行动项 · 情绪曲线 · 张力与分歧 · 认知偏误 · 话题延伸 · 客户跟进 · 招聘评估
（在 `coco/templates.py` 里可自行增改）

## 配置说明（coco config）

- `whisper_model`：`turbo`（默认）或 `large`，网页顶栏也可切换。
  本机实测（M 系列，5 分钟真实会议音频）：turbo 约 23 倍速、large 约 7 倍速，
  即 45 分钟会议 turbo 约 2 分钟、large 约 6-7 分钟。
- `initial_prompt_extra`：常出现的人名/品牌/术语（逗号分隔），注入转写提示提高识别准确率。
- `auto_memory`：转写完成后是否自动提取长期记忆（默认 true）。
- `claude_extra_args`：传给 claude CLI 的额外参数，如 `["--model","claude-sonnet-4-6"]`。
- `hf_endpoint`：模型下载源；网络不通时自动切换到 hf-mirror.com。

## 数据位置（全部本地）

```
library/<日期-标题>/   音频 audio.wav + transcript.md/json + reports/*.md
library/_briefs/       每日简报
library/_tracking/     跨会议追踪报告
library/_trash/        回收站（删除的会议，可手动恢复）
memory/memory.md       全局记忆（手动维护）
memory/longterm.md     长期记忆（自动提取，可手动修订，覆盖前备份 .bak.md）
coco.config.json       配置（首次 coco config 修改后生成）
```

## 注意事项

- 首次录音时 macOS 会弹窗请求终端的麦克风权限，需允许。
- 长音频已开启防复读参数（condition_on_previous_text=False），减少 whisper 重复幻觉。
- Web 界面只监听 127.0.0.1，不对外网开放。
