# coco — 本地会议参谋

**录音转写 · 会前调查 · 深层洞察 · 知识沉淀，全部在你自己的电脑上完成。**

[English README → README.md](README.md)

coco 是一个跑在本机的会议 AI 工具（灵感来自 [YouNavi](https://younavi.me)）：
把会议录音、访谈、聊天记录、他人纪要都喂给它，它帮你在**会前看懂局**、
**会后抓住没说破的东西**、并把每一次沟通**沉淀成可复用的认知资产**。

音频不出本机（Whisper 本地转写）。分析用你**已经有的** AI：ChatGPT 账号（通过官方 Codex CLI）、
Claude 账号（通过 Claude Code），或者粘贴一个 API key（DeepSeek、通义千问、Kimi、智谱、豆包、
OpenAI、Gemini、OpenRouter……），也可以接本机 Ollama / LM Studio 完全离线。没有 coco 自己的
云端账号，也不另收订阅费。

界面提供**简体中文、English、français** 三种语言；AI 输出语言可以单独设置。支持 macOS、
Windows、Linux。不能本地转写的电脑一样能用全部分析功能：把任何工具导出的文字稿
（txt / docx / pdf / srt / vtt / json 等）拖进来，或直接粘贴文字。

## 快速开始

**macOS**：打开「终端」，粘贴下面一行回车。装好后 coco 会出现在「应用程序」里，以后从启动台或
聚焦搜索（⌘ 空格，输入 coco）打开。

```bash
curl -fsSL https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.sh | bash
```

**Windows**：打开「PowerShell」，粘贴下面一行回车。装好后开始菜单和桌面会有 coco 图标。

```powershell
irm https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.ps1 | iex
```

不想用命令行：GitHub 页面「Code → Download ZIP」解压，Mac 双击 **`coco.command`**，Windows 双击
**`coco.bat`**。首次会自动准备 Python 环境（电脑上没有 Python 也行）、安装依赖，然后打开浏览器，
并把 coco 加进「应用程序」/ 开始菜单。

打开后第一屏会问「AI 从哪来」：有 ChatGPT 或 Claude 订阅的点一下就能用；有 API key 的选服务商、
粘贴 key、点「获取列表」选模型、点「测试连接」。详见 [安装与启动](#安装与启动) 和
[AI 模型通道](#ai-模型通道chatgptclaudedeepseek通义千问ollama)。

## 它适合谁

- **顾问 / FDE / 售前**：进入一家新公司，访谈十个人，各说各话——用「调研综合」交叉对比
  各方诉求与痛点，理出共识、分歧和落地方向
- **销售 / 客户负责人**：每次见客户前用「会前调查」过一遍历史——谁承诺过什么、态度怎么变的、
  这次该怎么开场
- **管理者**：跨周例会追踪「谁答应的事有没有下文」，发现反复被搁置的问题
- **任何开很多会的人**：转写、纪要、行动项、日报周报，一条龙

## 界面一览

**会前调查**——先看懂局，再进会。

![会前调查](docs/prep.png)

**跨会议洞察**——三种分析模式，可聚焦某个人/项目，可只分析勾选的几场访谈。

![跨会议洞察](docs/insight.png)

**知识底座**——人物、项目、承诺、词表的自动沉淀总览。点一个人名，跨全部会议梳理 TA。

![知识底座](docs/knowledge.png)

## 功能全览

### 材料进来

| 方式 | 说明 |
|---|---|
| ● 录音 | 浏览器直接录（macOS / Windows / Linux 都可以），音频每 4 秒存到本机，页面意外关闭也能恢复已录部分；停止后自动转写 |
| 拖拽 / ↑ 上传 | 音频、视频，或已有的文字材料，多选、排队 |
| ⌖ 导入 · 本地路径 | 文件或文件夹（文件夹 = 批量）；音频 + 同名转写（Whisper 导出目录）自动配对成一场会议 |
| ⌖ 导入 · 粘贴文本 | 聊天记录、邮件、他人纪要、原始文字稿；SRT / VTT / JSON 内容自动识别并保留时间轴；可标注材料原始日期 |
| `coco watch` | 监控文件夹，新录音自动转写（语音备忘录、Plaud 导出目录） |

**不经转写直接入库的文字格式：** `txt md docx odt pdf rtf html eml csv tsv srt vtt sbv lrc ass ssa json`。
腾讯会议、飞书妙记、讯飞听见、Zoom、Teams、Google Meet、Otter、Fireflies 等工具导出的文字稿
可以原样上传。带时间轴的渲染成与本地转写一致的 `[mm:ss] 文本`；说话人标签（VTT voice 标签、
ASS 角色名、CSV/JSON 的 speaker 列）保留；自动识别 UTF-8 / UTF-16 / GB18030 / Big5，微信、QQ
导出不乱码；Whisper `.tsv` 的毫秒偏移与逐词 JSON 导出都能正确处理。PDF 需要 `pypdf`
（已列入 `requirements.txt`）。

**入库即入记忆**：文字材料和转写一样，自动并入长期记忆、参与所有跨会议分析。

### 转写与校正

- **本地 Whisper**：Apple Silicon 用 mlx-whisper（原生加速，turbo 转 45 分钟会议约 2 分钟）；
  Windows / Linux / Intel Mac 用 faster-whisper（有 NVIDIA 显卡走 CUDA，否则 CPU，慢但可用）。
  turbo 快、large 最准；在「⚙ 设置 → 转写与录音」切换模型与转写语言（自动识别或指定 ISO 码）
- **幻觉抑制**：Whisper 会在静音和噪音处脑补文字（YouTube 式开场白、字幕致谢、单字复读），还会把
  注入的提示词原样吐出来。coco 转写前先裁掉首尾静音，开启 Whisper 自带的静音幻觉阈值，保留每段的置信度，
  转写后再按已知模式清洗。已有转写可在「转写」页签点「✦ 清理」（先预览再应用，原稿备份为
  `transcript.preclean.md`），或用 `coco clean --all --apply`
- **没有引擎也不慌**：不能本地转写的电脑（典型：没装 faster-whisper 的 Windows）一旦有人
  拖入音频，会弹出说明对话框：这台电脑暂时不能转写，录音本身没问题，给出三条路——用其他工具
  转成文字稿再拖进来 / 直接粘贴文字稿 / 安装引擎（附命令）。只有 CPU 的机器首次会提示预计耗时
- **词表**（`memory/glossary.md`）：人名与专有名词的标准写法，格式
  `- 正确写法（误写：错1、错2）｜备注`。正确写法注入转写提示；「✦ 提炼词表」让 AI 从会议历史里
  自动归集
- **✦ 人名与术语校正**：按词表统一修正全部会议转写与报告里的写法；原文自动备份，可逐场恢复原稿
- **✎ 编辑**：转写和报告都可以直接改，后续分析用改过的版本
- **参与人员**：会议标题下多一行「参与人员」（同步写进转写头部），格式「姓名（角色）、姓名（角色）」。
  可手动填写，也可点「✦ 识别」让 AI 从转写里提出建议再确认。之后的每份报告、对话回答和跨会议分析
  都以它为准判断谁说了什么——说话人判断失误改一次即可，不用每份报告重来
- **下载转写**：`.md` / `.txt` / `.srt` / `.vtt` / `.json` 任选

### 单场分析

九个模板，一键生成。每次生成都会立刻新开一个 ⏳ 页签在里面**边生成边显示**（第一个字出来之前显示
「模型正在阅读与思考… N 秒」），其他页签照常可读，切回来继续看。报告可编辑、可下载：

> 纪要 · 行动项 · 情绪曲线 · 张力与分歧 · 认知偏误 · 话题延伸 · 客户跟进 · 招聘评估 · **跟进草稿**

「跟进草稿」直接产出**可发送的跟进消息**（邮件/即时消息，语气匹配双方关系）+ 48 小时行动清单。
右侧对话框可对当前会议自由提问；勾选「跨全部会议」后提问范围扩展到整个会议库。「↓ 导出」把这次对话
保存为 Markdown，选中会议时同时存为该会议的「对话 HH:MM」页签。

### 会前调查（◎ 会前）

填三样：会议主题、参会人、你的目标。coco 自动挑出最相关的历史会议，结合长期记忆与词表生成
会前简报——所有判断标注来源（会议 id + 时间戳），推测明确标「推测」。可选**联网搜索**参会人/
公司的公开信息（结果注明来源；仅 Claude CLI 通道；默认关闭，逐次勾选）。历史简报保存在
`library/_prep/`。

### 跨会议洞察（⛓ 洞察）

| 模式 | 回答的问题 |
|---|---|
| 追踪 | 谁承诺了什么、兑现没有；谁的说法前后变了；哪些问题反复被搁置 |
| 深层信号 | 表面同意实际没同意的地方；潜台词；各方在保护什么、争取什么；观点漂移轨迹；所有人都没提的盲区 |
| 调研综合 | 多方访谈交叉对比：角色图、诉求与痛点矩阵、共识区与分歧区、信息盲区、快赢/中期/长期落地建议（FDE 调研场景） |

范围可选全部会议，或侧边栏勾选的几场；可聚焦某个人/项目/客户。

### 沉淀（越用越懂你）

- **长期记忆**（自动）：每场转写/导入完成后，提取人物、项目与客户、承诺与决定、术语，合并进
  `memory/longterm.md`，注入之后所有分析；同一人表态变化保留轨迹（「原说 X [旧会议] → 现说 Y
  [新会议]」）。合并是**增量**的：模型只输出新增/变化的条目，coco 按名称合并，几秒完成，不再
  每次重写整份文件。「⟲ 压缩长期记忆」按需做一次整体合并压缩
- **日报 / 周报**（☀ 简报）：汇总一天的行动项与洞察；一周的主线、决定、行动项总账、表态变化、
  下周建议。简报也并入长期记忆
- **知识底座**（☷ 底座）：沉淀统计 + 人物点选提问
- 记忆、词表都是可手动编辑的 Markdown；任何自动覆盖前都留 `.bak.md` 备份

### 管理

全局搜索 · 日期筛选与录音日期校准 · 回收站软删除（`library/_trash`）· 单文件下载 · 批量 zip
导出 · 服务重启自动恢复中断的转写队列

## 语言

- **界面语言**：简体中文、English、français。「⚙ 设置 → 语言」、URL 加 `?lang=en`、
  命令行 `coco lang zh-CN` 都能切换，选择会记住
- **AI 输出语言**（⚙ 设置）：跟随界面（默认）/ 跟随材料（转写是什么语言就用什么语言）/
  指定某种语言（日语、德语……）。影响报告、简报、洞察、会前调查、对话回答，以及长期记忆和
  词表的写法
- **转写语言**：「⚙ 设置 → 转写与录音」单独设置，自动识别或指定 ISO 码
- 切换语言不影响已有会议库：coco 认得所有语言包的记忆/词表章节标题，旧的中文报告文件名也照常
  显示标签

新增语言：复制 `coco/locales/en.json` 为 `<code>.json` 翻译即可（提示词沿用英文并附「用 X 语言
输出」指令；中文输出用中文提示词集）。`python tests/check_i18n.py` 检查键是否齐全。

## AI 模型通道（ChatGPT、Claude、DeepSeek、通义千问、Ollama……）

顶栏右侧的圆点标签显示当前用的 AI（绿色 = 就绪），点它进入「⚙ 设置 → AI 通道」。什么都没配置时，
coco 会自动使用已登录的 Claude Code，其次是已登录的 Codex（ChatGPT）；都没有就在首屏引导你选一个。

| 你有什么 | 选哪个 | 说明 |
|---|---|---|
| ChatGPT Plus / Pro / Business 等订阅 | **ChatGPT 账号（Codex）** | 通过 OpenAI 官方 Codex CLI，用 ChatGPT 订阅额度分析，不需要 API key。装好后运行一次 `codex login` |
| Claude 订阅 | **Claude 账号（Claude Code）** | 通过 Claude Code，用 Claude 订阅额度分析，不需要 API key。唯一支持「会前调查 · 联网搜索」的通道 |
| 某家的 API key | **DeepSeek / 通义千问 / Kimi / 智谱 GLM / 豆包 / MiniMax / 硅基流动 / OpenAI / Anthropic / Gemini / OpenRouter** | 按用量付费。每个预设都带「获取 key」链接；国内与国际站点分开（阿里云、Kimi、智谱的 key 只在创建它的站点有效） |
| 想完全离线 | **Ollama / LM Studio** | 模型跑在本机，会议文字不出电脑；需要配置较好的电脑，长会议建议 14B 以上的模型 |
| 其他兼容接口 | **自定义（OpenAI 兼容 / Anthropic 兼容）** | 填 Base URL、key、模型名 |

**模型名不用背。** 选好服务、粘贴 key 后点「↻ 获取列表」，coco 会向服务商实时拉取你这个 key 可用的
模型（ChatGPT 账号列出的是你的订阅当前提供的模型）。预设里的默认模型取自各家 2026 年 9 月的官方文档，
服务商改名时以列表为准。「测试连接」发一条最小请求并报告延迟；出错时会翻译成能照着做的提示
（key 无效、模型名不存在、余额不足、限流……），同时保留服务商的原话。

**两条通道。** 主通道负责你要读的东西（报告、洞察、会前调查、对话）；后台通道负责长期记忆合并、
词表提炼、人名校正这类频繁、机械、输入很长的任务。取消勾选「后台任务与主通道使用同一个模型」后，
后台通道默认选同一家的便宜模型。每条通道可在「高级设置」里改上下文上限、单次输出上限、Base URL、
用环境变量提供 key。API key 只保存在本机 `coco.config.json`（已在 .gitignore），界面和接口都不回传。

**兼容细节**（已替你处理）：OpenAI 官方接口用 `max_completion_tokens`，其他服务用 `max_tokens`，
模型的输出上限更低时自动去掉该参数重试；豆包等默认输出很短的服务会显式放宽上限；MiniMax 和本机推理
模型混在正文里的 `<think>` 思考过程会被去掉；发往本机 / 局域网模型的请求不走 `http_proxy`。

**ChatGPT（Codex）通道的特点。** Codex 是一个编程 agent，coco 只用它的模型：在空目录、只读沙箱里运行，
不加载你的 MCP 和插件，并明确要求它不执行命令。Codex 不流式输出，报告会在生成完成后一次显示
（期间显示「模型正在阅读与思考… N 秒」）。可以在设置里选模型和推理强度。

## 性能

时间花在哪：在本机测得，每次 CLI 往返在拿到第一个字之前有约 5–8 秒的固定开销
（`claude --version` 2.1.246，2026 年 8 月；你的机器数字会不同），之后是模型输出速度。所以 coco：

- **流式输出**：报告、对话、洞察、简报、会前调查边生成边显示；
- **增量合并长期记忆**：只输出几百 token 的变化条目，而不是重写可能有几千行的整份文件；
- 后台任务可走**更快的通道**（Claude CLI 的 `haiku`，或 DeepSeek）；
- 启动 CLI 时不加载 MCP、不给工具、在空目录里运行，避免项目 `CLAUDE.md` 和 MCP 启动拖慢每次分析。

## 安装与启动

### macOS

- **一行安装（推荐）**：终端运行
  `curl -fsSL https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.sh | bash`。
  装到 `~/coco`（可用 `COCO_HOME=路径` 改），已安装则原地更新。命令行下载不会触发「身份不明的开发者」拦截
- **下载 ZIP**：解压后双击 `coco.command`。若提示「无法打开…来自身份不明的开发者」，右键 → 打开；
  macOS 15 起需到「系统设置 → 隐私与安全性」点「仍要打开」（只需一次）

首次运行会：找一个原生的 Python 3.10+（Apple 芯片上必须是 arm64 版，否则装不了快速的 mlx-whisper），
没有就用 [uv](https://docs.astral.sh/uv/) 自动装一个；安装依赖（先实测 PyPI 与清华 / 阿里云镜像的下载速度，
谁快用谁）；启动服务并打开浏览器；把 **coco 加进「应用程序」**。以后从启动台、聚焦搜索或程序坞打开，
不再弹终端窗口。Apple 芯片自带静态 ffmpeg（不需要 Homebrew）；首次转写会下载 Whisper 模型（small 约
0.5GB / turbo 约 1.6GB / large 约 3GB；国内网络自动切 hf-mirror）。

### Windows

- **一行安装（推荐）**：PowerShell 运行
  `irm https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.ps1 | iex`，装到
  `%USERPROFILE%\coco`
- **下载 ZIP**：解压后双击 `coco.bat`（SmartScreen 提示时点「更多信息 → 仍要运行」）

不需要先装 Python：没有的话 coco 会用 uv 自动准备。首次安装含 faster-whisper（几百 MB）。启动后会在
**开始菜单和桌面**添加 coco 图标，服务在后台运行，黑色窗口可以直接关掉。

**Windows 上的典型用法：已有录音 → 文字稿 → 上传 → 分析。** 没有 NVIDIA 显卡时本机转写 45 分钟录音
需要 10–30 分钟，可以用腾讯会议、飞书妙记、讯飞听见等工具导出文字稿再拖进来；faster-whisper 装不上时
coco 只装核心依赖，文字稿路线不受影响。

### Linux

`./coco.command`（同样会建环境、装依赖、启动，并添加到应用菜单）。

### 退出、重启、更新

- **退出**：顶栏右上角 ⏻，或「⚙ 设置 → 关于 → 退出 coco」，或命令行 `coco stop`。有录音或转写在进行时会先确认；
  已录的音频会保留，未完成的转写下次启动自动继续
- **重复双击**不会起第二个服务，只会打开浏览器；端口被别的程序占用时自动换一个
- **更新**：`coco update`（git 仓库执行 `git pull`，ZIP 安装则下载最新代码覆盖，会议库、记忆和配置不动），
  或重新运行一行安装命令。代码更新后再双击图标，旧版本服务会自动重启为新版本（正在录音或转写时除外）
- 数据和日志在哪：「⚙ 设置 → 关于」可以直接打开数据文件夹和日志

### 电脑配置一般 / 转写太慢？

- 「⚙ 设置 → 转写与录音」把模型切成 **small**（下载约 0.5GB，更快、更省内存；精度略降，人名可靠「校正」修）
- 或者不在本机转写：会议软件 / 转写服务出文字稿（txt / docx / pdf / srt），直接上传，分析功能完整
- AI 分析本身不吃本机配置（跑在订阅账号 / API 通道上），老电脑一样能用

## 命令行

界面能做的 CLI 都能做（macOS/Linux 建议 `alias coco="<项目路径>/bin/coco"`；Windows 用
`bin\coco.cmd`）。输出跟随界面语言。

| 命令 | 作用 |
|---|---|
| `coco transcribe <文件…>` | 导入音频/视频转写（`--model turbo/large/small`） |
| `coco import <文件/文件夹…>` | 导入文字材料（txt/docx/pdf/srt/vtt/json…），不转写 |
| `coco record [标题]` | 麦克风录音，Ctrl+C 停止后转写（macOS） |
| `coco watch <文件夹>` | 监控文件夹，新音频自动转写 |
| `coco list` / `coco show <会议>` | 会议库 / 某条转写 |
| `coco ask "问题" [会议…]` | 对会议提问（流式输出） |
| `coco report <会议> -t <模板>` | 生成报告（`coco templates` 看 id） |
| `coco prep "主题" [--who …] [--goal …] [--web]` | 会前调查 |
| `coco track [焦点] [--mode track\|signals\|synthesis] [--refs a,b]` | 跨会议洞察 |
| `coco brief [日期]` / `coco weekly [日期]` | 日报 / 周报 |
| `coco search <词>` | 全文搜索 |
| `coco glossary [词条] [--extract]` | 查看/追加/AI 提炼词表 |
| `coco clean <会议>\|--all [--apply]` | 清理已有转写里的 Whisper 幻觉（不加 `--apply` 只预览） |
| `coco memory [内容]` | 查看/追加全局记忆 |
| `coco memorize [会议\|--all\|--compact]` | 提取长期记忆 / 压缩整理 |
| `coco lang [代码] [--output …]` | 界面与 AI 输出语言 |
| `coco ai show\|test\|preset\|set\|task` | AI 通道（`--profile fast` 操作后台通道） |
| `coco start` · `coco stop` · `coco restart` · `coco status` | 后台启动并打开浏览器 · 退出 · 重启 · 运行状态与数据位置 |
| `coco update` · `coco setup` · `coco shortcut [--remove]` | 更新到最新版 · 安装 / 修复依赖 · 应用程序 / 开始菜单图标 |
| `coco delete <会议>` · `coco config [键 值]` · `coco web` | 回收站 · 配置 · 前台运行 Web 服务 |

模板与模式 id：`minutes actions mood tension bias topics client hiring followup` ·
`track signals synthesis`；原来的中文名（纪要、追踪……）照样接受。

## 数据与配置

所有数据都是本机磁盘上的普通文件夹和 Markdown，随时可以翻看、备份、迁移：

```
library/<日期-标题>/   每场会议：音频 + transcript.md/json + reports/*.md
library/_briefs/       每日简报            library/_weekly/     周报
library/_tracking/     跨会议洞察报告       library/_prep/       会前调查简报
library/_trash/        回收站
memory/memory.md       全局记忆（手动维护，注入每次分析）
memory/longterm.md     长期记忆（自动提取，可手动修订）
memory/glossary.md     词表（人名与专有名词标准写法）
coco.config.json       配置（含 API key，已在 .gitignore）
```

常用配置（`coco config 键 值`）：`ui_language`（auto / zh-CN / en / fr）、`output_language`
（ui / source / 语言代码）、`whisper_model`、`language`（转写语言）、`beam_size`、
`hallucination_filter`、`transcribe_backend`（auto / mlx / faster / none）、`record_mode`（auto / browser / ffmpeg）、
`auto_memory`、`memory_merge`（delta / full）、`ai_profiles`、`ai_tasks`、`claude_bin`、`codex_bin`、`claude_extra_args`、
`hf_endpoint`、`port`、`desktop_shortcut`（auto / created / off）、`allowed_hosts`（除本机外额外允许的主机名，高级）。环境变量 `COCO_ROOT` 可把数据根
目录指到别处；`COCO_LANG` 覆盖 CLI 语言；`COCO_PORT` 指定端口；`COCO_PIP_INDEX` 指定依赖安装源。

## 已知边界

- 本地转写不区分说话人（导入材料里的说话人标签会保留）
- Whisper 的防复读机制使词表提示主要作用于音频开头，全文纠错靠「✦ 校正」完成
- 会前调查的「联网搜索」会把检索词发给搜索引擎——只在需要公开情报时勾选；其余功能均不联网
  （模型下载与你选择的 API 通道除外）
- 浏览器录音只录麦克风。要同时录下线上会议对方的声音：macOS 安装 BlackHole 等虚拟声卡，在「⚙ 设置 →
  转写与录音」选服务端录音并用 `coco devices` / `audio_device` 指定输入
- ChatGPT（Codex）通道不流式输出；联网搜索只有 Claude 账号通道支持
- coco 只接受本机访问（Host 必须是 127.0.0.1 / localhost），并拒绝其他网页发来的修改类请求
- 不支持旧版 `.doc`，请另存为 `.docx` 或 `.txt`；扫描版 PDF 需先 OCR

## 开发自检

```bash
.venv/bin/python tests/smoke.py      # 约 240 项断言：临时 COCO_ROOT + stub claude / codex + 本地假 API
.venv/bin/python tests/check_i18n.py # Python / 前端用到的每个键在三个语言包里都存在
```

都不碰真实数据、不消耗模型额度（Windows：`.venv\Scripts\python tests\smoke.py`）。

## 更新日记

### 2026-09-25 · 一个入口启动，ChatGPT 账号也能用
- **启动**：根目录只留每个系统一个双击入口（Mac `coco.command`、Windows `coco.bat`），`run.sh` / `run.bat` 移除；
  启动逻辑收敛到跨平台的 `coco start`：找原生 Python（没有就用 uv 自动装）、测速选 PyPI 镜像、端口冲突自动换、
  后台运行不占终端、重复双击只开浏览器、旧版本在跑时自动重启为新版本
- **一行安装**：`scripts/install.sh` / `install.ps1`；首次启动后 coco 出现在「应用程序」/ 开始菜单和桌面
- **退出与更新**：界面 ⏻ 退出；`coco stop / restart / status / update / shortcut`
- **ChatGPT 通道**：新增 `codex-cli`，用 ChatGPT 订阅额度分析；什么都没配时自动选已登录的 Claude 或 Codex
- **API 通道**：预设扩充到 DeepSeek、通义千问（国内 / 国际）、Kimi（国内 / 国际）、智谱 GLM / Z.ai、豆包、
  MiniMax、硅基流动、OpenAI、Anthropic、Gemini、OpenRouter、Ollama、LM Studio；「获取列表」实时拉取可用模型；
  HTTP 错误翻译成可操作的提示；处理 `max_completion_tokens`、`<think>` 思考块、本机请求绕过代理
- **首屏向导**：没有可用 AI 时，第一屏按「你有什么账号」引导；设置面板改为「AI 通道 / 转写与录音 / 语言 / 关于」四页
- **录音**：改为浏览器录音，Windows / Linux 也能录；每 4 秒存盘，页面关闭或服务重启后可恢复已录部分；
  录音时显示音量与计时，20 秒无声会提醒
- **修复**：没装 Homebrew ffmpeg 的 Apple 芯片 Mac 无法转写任何音频（mlx-whisper 依赖 ffmpeg 命令），现随包附带静态 ffmpeg；
  从图标启动时找不到 claude / codex（补全登录 shell 的 PATH）
- **安全**：只接受本机 Host，拒绝其他网页发来的修改类请求（防 DNS rebinding / 跨站请求）
- 冒烟测试增至约 240 项（新增 codex 模拟程序、假 API 服务的各种异常）

### 2026-09-01 · 面向非技术用户的开箱体验
- **macOS 一键安装**：双击 `coco.command` 首次运行自动建环境、装依赖、给出缺 Python/网络失败的指引
- **AI 未就绪引导**：没装 claude 也没配 API 通道时，界面顶部出现引导条——
  「配置 AI 通道」直达设置，「如何安装 Claude」给分平台的复制粘贴步骤
- **small 转写模型**：约 0.5GB，低配电脑可用；CPU 转写提示里给出切换建议
- 首次下载模型时给出体积与等待预期；Windows 缺 Python 时提示 Microsoft Store 路线

### 2026-08-26 · 多语言、流式输出、可插拔 AI 通道
- **三种界面语言**（简体中文、English、français），每条消息、文件标签、下载文件名都按请求语言
  输出；**AI 输出语言**独立于界面（跟随界面 / 跟随材料 / 指定语言）；切换语言不影响已有会议库
- **流式输出**：报告、对话、洞察、简报、会前调查边生成边显示
- **长期记忆增量合并**（几秒完成，不再整份重写）+ 按需「压缩整理」
- **AI 通道**：Claude CLI（默认）、Anthropic 兼容与 OpenAI 兼容 HTTP 接口（DeepSeek、OpenAI、
  Ollama……）；主通道 / 后台通道按任务分配；连接测试；预设
- **更多文字稿格式**：pdf、odt、rtf、html、eml、csv/tsv、sbv、lrc、ass/ssa，JSON 识别更宽
  （说话人、毫秒偏移、逐词导出）；粘贴的 SRT/VTT/JSON 自动识别；转写可下载为 md/txt/srt/vtt/json
- **无引擎时更友好**：拖入音频弹出说明对话框，CPU 转写耗时提示，一键跳到「粘贴文字稿」
- claude CLI 以不加载 MCP、不给工具、空工作目录的方式启动
- 冒烟测试增至约 150 项，新增语言包完整性检查

### 2026-08-25 · Windows 支持
- `run.bat` 一键装环境并启动；`bin\coco.cmd`；claude CLI 按 PATHEXT 定位；子进程强制 UTF-8
- 转写引擎抽象：Apple Silicon 用 mlx-whisper，其他平台用 faster-whisper（CUDA / CPU）
- docx 导入；界面按本机能力自适应

### 2026-08-24
- 周报；README 重写为完整产品文档

### 2026-08-21 · 从「会后纪要」到「会前-会中-会后-沉淀」全流程
- 会前调查、词表、洞察三模式（追踪 / 深层信号 / 调研综合）、文字材料归集、知识底座、
  跟进草稿模板、冒烟测试

### 2026-06 · 首批版本
- 本地 Whisper 转写、分析模板、对话问答、每日简报、长期记忆、跨会议追踪、搜索、回收站、
  编辑、人名校正
