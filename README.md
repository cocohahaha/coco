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
多个文件会排队转写，完成一个出一个。

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
| `coco memory [内容]` | 查看/追加全局记忆（人名、术语，注入每次分析） |
| `coco watch <文件夹>` | 监控文件夹，新音频自动转写（适合接语音备忘录、Plaud 导出目录等） |
| `coco record [标题]` | （可选）麦克风录音，Ctrl+C 停止后自动转写 |
| `coco web` | 启动本地 Web 界面（上传/本地导入/报告/对话/简报/记忆） |
| `coco config [键 值]` | 查看/修改配置 |

## 分析模板

纪要 · 行动项 · 情绪曲线 · 张力与分歧 · 认知偏误 · 话题延伸 · 客户跟进 · 招聘评估
（在 `coco/templates.py` 里可自行增改）

## 配置说明（coco config）

- `whisper_model`：`large`（默认，精度最高）/ `turbo`（约快 2 倍）。两个模型本机均已缓存。
- `claude_extra_args`：传给 claude CLI 的额外参数，如 `["--model","claude-sonnet-4-6"]`。
- `hf_endpoint`：模型下载源；网络不通时自动切换到 hf-mirror.com。

## 数据位置（全部本地）

```
library/<日期-标题>/   音频 audio.wav + transcript.md/json + reports/*.md
library/_briefs/       每日简报
memory/memory.md       全局记忆
coco.config.json       配置（首次 coco config 修改后生成）
```

## 注意事项

- 首次录音时 macOS 会弹窗请求终端的麦克风权限，需允许。
- 转写速度（Apple Silicon）：large 约为录音时长的 1/5～1/3，turbo 更快。
- Web 界面只监听 127.0.0.1，不对外网开放。
