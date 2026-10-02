# coco — local meeting strategist

**Transcribe · prepare · see what was not said · build lasting knowledge — all on your own computer.**

[中文说明 → README.zh-CN.md](README.zh-CN.md)

coco is a local meeting AI tool (inspired by [YouNavi](https://younavi.me)). Feed it recordings,
interviews, chat logs or other people's minutes and it helps you **understand the situation before
a meeting**, **catch what was left unsaid after it**, and **turn every conversation into reusable
knowledge**.

Audio never leaves your machine (Whisper runs locally). Analysis uses an AI **you already have**:
a ChatGPT account (through the official Codex CLI), a Claude account (through Claude Code), or one
pasted API key (OpenAI, Anthropic, Gemini, DeepSeek, Qwen, Kimi, GLM, OpenRouter …); a local Ollama
or LM Studio keeps everything offline. coco has no cloud account and no subscription of its own.

Interface in **English, 简体中文 and français**; AI output in any language the model speaks.
Works on macOS, Windows and Linux. Machines that cannot run a transcription engine still get every
analysis feature: drop in a transcript exported by any other tool (txt / docx / pdf / srt / vtt /
json …) or paste the text.

## Quick start

**macOS**: open Terminal, paste this line and press Return. coco then appears in Applications:
open it from Launchpad or Spotlight (⌘ Space, type coco) from now on.

```bash
curl -fsSL https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.sh | bash
```

**Windows**: open PowerShell, paste this line and press Enter. coco then has an icon in the Start
menu and on the desktop.

```powershell
irm https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.ps1 | iex
```

Prefer no command line: *Code → Download ZIP* on GitHub, unzip, then double-click **`coco.command`**
(Mac) or **`coco.bat`** (Windows). The first run prepares Python by itself (none installed is fine),
installs the dependencies, opens the browser and adds coco to Applications / the Start menu.

The first screen asks where the AI should come from: with a ChatGPT or Claude plan it is one click;
with an API key, choose the provider, paste the key, click *Load list* to pick a model and *Test*.
Details: [Install and run](#install-and-run) and [AI channels](#ai-channels-chatgpt-claude-openai-gemini-deepseek-ollama-).

## Who it is for

- **Consultants / FDEs / pre-sales** — ten interviews in a new organisation, ten stories:
  *Research synthesis* cross-compares needs and pain points into consensus, disagreement and a
  direction of implementation
- **Sales / account owners** — before every client meeting, *Prep* replays the history: who promised
  what, how attitudes shifted, how to open
- **Managers** — *Tracking* follows "who agreed to what, did it happen" across weekly meetings and
  surfaces the questions that keep being parked
- **Anyone with too many meetings** — transcription, minutes, action items, daily and weekly
  briefs in one place

## What it looks like

Screenshots below show the Chinese interface; the layout is identical in English and French.

**Pre-meeting brief** — understand the situation before walking in.

![Prep](docs/prep.png)

**Cross-meeting insight** — three analysis modes, optional focus on a person or project, optional
restriction to selected meetings.

![Insight](docs/insight.png)

**Knowledge base** — people, projects, commitments and glossary accumulated automatically. Click a
person to review them across every meeting.

![Knowledge](docs/knowledge.png)

## Features

### Getting material in

| Route | Notes |
|---|---|
| ● Record | In the browser on macOS, Windows and Linux. Audio is saved on this computer every 4 seconds, so a closed tab or a restart keeps what was recorded; transcription starts when you stop |
| Drag & drop / ↑ Upload | Audio, video, or existing text material — several files at once, queued |
| ⌖ Import · local path | File or folder (folder = bulk). Audio + same-name transcript (Whisper export folders) are paired into one meeting |
| ⌖ Import · paste | Chat logs, e-mails, someone else's minutes, raw transcripts — SRT / VTT / JSON content is detected and keeps its timestamps; the material's own date can be set |
| `coco watch` | Watch a folder and transcribe new recordings (voice memos, Plaud exports …) |

**Transcript formats accepted without transcription:** `txt md docx odt pdf rtf html eml csv tsv srt
vtt sbv lrc ass ssa json`. Exports from Zoom, Teams, Google Meet, Otter, Fireflies, Tencent
Meeting, Feishu Minutes, iFlytek and similar tools go straight in. Timed formats are rendered as
`[mm:ss] text` exactly like a local transcription; speaker labels (VTT voice tags, ASS names,
CSV/JSON speaker columns) are kept. UTF-8 / UTF-16 / GB18030 / Big5 are detected automatically.
Whisper `.tsv` millisecond offsets and word-level JSON exports are handled. PDF needs the optional
`pypdf` package (included in `requirements.txt`).

**Everything imported joins the memory**: text material is merged into long-term memory and takes
part in every cross-meeting analysis, exactly like a transcription.

### Transcription

- **Local Whisper**: Apple Silicon uses mlx-whisper (native acceleration; *turbo* transcribes a
  45-minute meeting in about 2 minutes); Windows / Linux / Intel Mac use faster-whisper (CUDA with an
  NVIDIA GPU, otherwise CPU — slow but working). *turbo* is fast, *large* the most accurate; model
  and transcription language (auto-detect or any ISO code) are set in ⚙ Settings → Transcription & recording.
- **Hallucination guard**: Whisper invents text on silence and noise (YouTube-style greetings,
  subtitle credits, repeated one-word segments) and echoes its own prompt. coco trims leading and
  trailing silence before decoding, enables Whisper's silence-hallucination threshold, keeps each
  segment's confidence, and strips the known shapes afterwards. Existing transcripts can be cleaned
  with *✦ Clean* on the transcript tab (preview, then apply; original kept as
  `transcript.preclean.md`) or `coco clean --all --apply`.
- **No engine? No problem.** If a machine cannot transcribe (typical: Windows without
  faster-whisper), dropping audio opens a help dialog that explains the situation and offers the
  three routes: export a transcript from another tool and drop it in, paste the text, or install an
  engine. CPU-only machines get a one-time warning about expected duration.
- **Glossary** (`memory/glossary.md`): standard spellings of names and terms, one per line
  `- Correct spelling (misheard: wrong1, wrong2) | note`. Correct spellings are injected into the
  Whisper prompt; *✦ Extract glossary* lets the AI collect names and terms from your history.
- **✦ Correct names & terms**: rewrites every transcript and report in the library to the glossary
  spellings; originals are backed up and can be restored per meeting.
- **✎ Edit**: transcripts and reports are editable; later analyses use the edited version.
- **Participants**: a *Participants* line under the meeting title (also written into the transcript
  header) holds `Name (role), Name (role)`. Fill it by hand, or let *✦ Detect* propose it from the
  transcript and confirm. Every later report, chat answer and cross-meeting analysis receives it as
  the authority for who said what, so a mis-attributed speaker is fixed once instead of in every report.
- **Download** a transcript as `.md`, `.txt`, `.srt`, `.vtt` or `.json`.

### Single-meeting analysis

Nine templates, one click. Each generation opens its own ⏳ tab and streams there as the text is
written (with a "reading and thinking… N s" status before the first token); the other tabs stay
readable and switching back resumes the live view. Reports are editable and downloadable:

> Minutes · Action items · Mood curve · Tensions · Cognitive biases · Open threads · Client debrief
> · Interview debrief · **Follow-up draft**

*Follow-up draft* produces a **ready-to-send message** (e-mail or chat, tone matched to the
relationship) plus a 48-hour action list. The chat panel answers free questions about the current
meeting; tick *across all meetings* to ask about the whole library. *↓ Export* saves the
conversation as Markdown and, when a meeting is selected, also stores it as a *Chat HH:MM* tab of
that meeting.

### Pre-meeting brief (◎ Prep)

Enter topic, participants and your goal. coco picks the most relevant past meetings and, with
long-term memory and the glossary, writes a brief: situation, key people with their past statements
(quoted with meeting id + timestamp), obstacles and opportunities, suggested approach and questions,
warning signs, information gaps. Inferences are labelled. Optional **web search** for public
information on participants and companies (sources cited; Claude CLI channel only; off by default).
Briefs are kept in `library/_prep/`.

### Cross-meeting insight (⛓ Insight)

| Mode | Question it answers |
|---|---|
| Tracking | Who committed to what and did it happen; whose statements changed; which questions keep being parked |
| Deep signals | Apparent agreement that was not real; subtext; what each party protects and pushes for; drift over time; the blind spot nobody mentioned |
| Research synthesis | Cross-interview comparison: role map, needs/pain-point matrix, consensus vs disagreement, information gaps, quick wins / medium / long-term direction (FDE research) |

Scope: the whole library or the meetings ticked in the sidebar; optional focus on a person,
project or client.

### Memory (it learns you)

- **Long-term memory** (automatic): after each transcript or import, people, projects & clients,
  commitments & decisions and terms are extracted and merged into `memory/longterm.md`, then
  injected into every later analysis. Changed positions keep their trajectory
  (*previously said X [old meeting] → now says Y [new meeting]*). Merging is **incremental**: the
  model only outputs new or changed entries and coco merges them by name in seconds, instead of
  rewriting the whole file each time. *⟲ Compact long-term memory* runs a full merge-and-compress
  pass when you want one.
- **Daily brief** and **weekly report** (☀ Briefs): action items and insights of a day; storylines,
  decisions, commitments ledger, changed positions and recommendations for a week. Briefs also feed
  the memory.
- **Knowledge base** (☷ Knowledge): counts, the memory and glossary at a glance, click-a-person
  review.
- Memory and glossary are plain Markdown you can edit; every automatic overwrite leaves a `.bak.md`.

### Management

Full-text search · date filter and recording-date correction · trash (`library/_trash`, soft
delete) · per-file download · bulk zip export · interrupted transcriptions resume after a restart.

## Languages

- **Interface**: English, 简体中文, français. Switch in ⚙ Settings → Language, `?lang=fr` in the URL, or `coco lang zh-CN` for the CLI. The choice is saved.
- **AI output language** (⚙ Settings): *same as the interface* (default), *same as the material*
  (write in whatever language the transcript is in), or any fixed language (Japanese, German …).
  It applies to reports, briefs, insights, prep, chat and the wording of memory and glossary.
- **Transcription language**: auto-detect or a fixed ISO code, in ⚙ Settings → Transcription & recording.
- Libraries survive language switches: coco recognises the memory/glossary headings of every
  shipped language, and old report files named in Chinese keep their labels.

Adding a language: copy `coco/locales/en.json` to `<code>.json`, translate, done (prompts stay in
English with a "write in <language>" directive; Chinese output uses the Chinese prompt set).
`python tests/check_i18n.py` verifies completeness.

## AI channels (ChatGPT, Claude, OpenAI, Gemini, DeepSeek, Ollama …)

The round chip at the top right shows the AI in use (green = ready); click it for ⚙ Settings → AI
channel. With nothing configured, coco uses a logged-in Claude Code, then a logged-in Codex
(ChatGPT); when neither exists, the first screen helps you pick one.

| You have | Choose | Notes |
|---|---|---|
| ChatGPT Plus / Pro / Business … | **ChatGPT account (Codex)** | Through OpenAI's official Codex CLI, on your ChatGPT plan, no API key. Run `codex login` once |
| A Claude plan | **Claude account (Claude Code)** | Through Claude Code, on your Claude plan, no API key. The only channel with web search in Prep |
| An API key | **OpenAI / Anthropic / Gemini / OpenRouter / DeepSeek / Qwen / Kimi / GLM / Doubao / MiniMax / SiliconFlow** | Pay per use. Every preset links to the provider's key page; mainland-China and international sites are separate presets (Alibaba, Kimi and Zhipu keys only work on the site that issued them) |
| Nothing may leave the computer | **Ollama / LM Studio** | The model runs locally; needs a capable machine, 14B+ models for long meetings |
| Another compatible server | **Custom (OpenAI- / Anthropic-compatible)** | Base URL, key, model name |

**No model names to memorise.** Choose the service, paste the key, click *↻ Load list*: coco asks the
provider for the models this key can use (for a ChatGPT account: the models your plan offers). Preset
defaults follow each vendor's documentation as of September 2026; when a vendor renames models, the
live list wins. *Test* sends one tiny request and reports the latency. Errors are translated into
steps you can act on (invalid key, unknown model, no balance, rate limit …) with the vendor's own
message kept underneath.

**Two channels.** The main channel writes what you read (reports, insights, prep, chat). The
background channel handles frequent, mechanical, long-input work: long-term memory merges, glossary
extraction, name correction. Untick *background tasks use the main channel* and it starts on the same
provider's cheaper model. *Advanced* holds the context cap, output limit, base URL and an environment
variable for the key. Keys stay in `coco.config.json` on this machine (git-ignored) and are never sent
back to the browser.

**Compatibility details handled for you:** `max_completion_tokens` for OpenAI's own API and
`max_tokens` elsewhere, retried without a cap when a model's ceiling is lower; an explicit output limit
for services with very short defaults (Doubao); `<think>` blocks from MiniMax and local reasoning
models removed from the answer; requests to local / LAN models bypass `http_proxy`.

**The ChatGPT (Codex) channel.** Codex is a coding agent and coco only uses its model: it runs in an
empty folder with a read-only sandbox, without your MCP servers or plugins, and is told not to run
commands. Codex does not stream, so a report appears when it is complete (with a "reading and
thinking… N s" status meanwhile). Model and reasoning effort can be chosen in Settings.

## Performance

Where the time goes: one CLI round-trip costs a fixed ~5–8 s before the first token on the machine
this was developed on (measured with `claude --version` 2.1.246, August 2026 — your numbers will
differ), then output speed. coco therefore:

- **streams** every report, chat answer, insight, brief and prep to the screen as it is written;
- merges long-term memory **incrementally** (a few hundred output tokens instead of rewriting a
  file that can grow to thousands of lines);
- lets you route background work to a **faster channel** (`haiku` via the Claude CLI, or DeepSeek);
- starts the CLI without MCP servers, without tools and in an empty working directory, so no
  project `CLAUDE.md` or MCP start-up leaks into the analysis.

## Install and run

### macOS

- **One line (recommended)**: in Terminal run
  `curl -fsSL https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.sh | bash`.
  Installs into `~/coco` (`COCO_HOME=path` to change) or updates an existing install in place.
  A command-line download does not trigger the "unidentified developer" block
- **ZIP**: unzip and double-click `coco.command`. If macOS says the developer cannot be verified:
  right-click → Open; from macOS 15 on, click "Open Anyway" in System Settings → Privacy & Security (once)

The first run finds a native Python 3.10+ (on Apple silicon it must be arm64, otherwise the fast
mlx-whisper cannot be installed) or installs one with [uv](https://docs.astral.sh/uv/); installs the
dependencies (measuring PyPI against the Tsinghua / Alibaba mirrors and using the faster); starts the
server, opens the browser and **adds coco to Applications**. From then on open it from Launchpad,
Spotlight or the Dock, without a Terminal window. A static ffmpeg ships with the Apple-silicon build
(no Homebrew needed). The first transcription downloads the Whisper model (small ≈ 0.5 GB / turbo
≈ 1.6 GB / large ≈ 3 GB; a mirror is used when huggingface.co is unreachable).

### Windows

- **One line (recommended)**: in PowerShell run
  `irm https://raw.githubusercontent.com/cocohahaha/coco/main/scripts/install.ps1 | iex`
  (installs into `%USERPROFILE%\coco`)
- **ZIP**: unzip and double-click `coco.bat` (SmartScreen: "More info → Run anyway")

No Python needed beforehand: uv provides one when missing. The first install includes faster-whisper
(a few hundred MB). coco then adds icons to the **Start menu and the desktop**; the server runs in the
background, so the black window can simply be closed.

**Typical Windows workflow: recording → transcript elsewhere → upload → analyse.** Without an NVIDIA
GPU, local transcription takes 10 to 30 minutes for a 45-minute file, so many people export a
transcript from their meeting software and drop it in. If faster-whisper cannot be installed, coco
installs the core only and the transcript route is unaffected.

### Linux

`./coco.command` (same steps: environment, dependencies, start, application-menu entry).

### Quit, restart, update

- **Quit**: ⏻ at the top right, ⚙ Settings → About → Quit coco, or `coco stop`. A running recording or
  transcription asks first; recorded audio is kept and unfinished transcriptions resume at the next start
- **Double-clicking again** never starts a second server, it only opens the browser; a port taken by
  another program is skipped automatically
- **Update**: `coco update` (`git pull` for a clone; for a ZIP install it downloads the latest code over
  the old one, never touching the library, memory or settings), or run the one-line installer again.
  After an update, opening coco restarts an older running server as the new version (unless it is
  recording or transcribing)
- Where data and logs live: ⚙ Settings → About opens both

### Modest hardware / transcription too slow?

- Switch the model to **small** in ⚙ Settings → Transcription & recording (≈ 0.5 GB download, faster
  and lighter; slightly less accurate, and name correction cleans up most of it)
- Or skip local transcription: let your meeting software or a transcription service produce the
  transcript (txt / docx / pdf / srt) and upload it; every analysis feature works the same
- The AI analysis does not run on your hardware (it runs on your subscription or API channel), so
  old machines are fine

## Command line

Everything the UI does (`alias coco="<repo>/bin/coco"` on macOS/Linux; `bin\coco.cmd` on Windows).
Output follows the interface language.

| Command | Purpose |
|---|---|
| `coco transcribe <files…>` | Import audio/video and transcribe (`--model turbo/large/small`) |
| `coco import <files/folders…>` | Import text material (txt/docx/pdf/srt/vtt/json …) without transcription |
| `coco record [title]` | Microphone recording, Ctrl+C to stop and transcribe (macOS) |
| `coco watch <folder>` | Watch a folder, transcribe new audio |
| `coco list` / `coco show <meeting>` | Library / one transcript |
| `coco ask "question" [meetings…]` | Ask about meetings (streamed) |
| `coco report <meeting> -t <template>` | Generate a report (`coco templates` lists ids) |
| `coco prep "topic" [--who …] [--goal …] [--web]` | Pre-meeting brief |
| `coco track [focus] [--mode track\|signals\|synthesis] [--refs a,b]` | Cross-meeting insight |
| `coco brief [date]` / `coco weekly [date]` | Daily brief / weekly report |
| `coco search <text>` | Full-text search |
| `coco glossary [entry] [--extract]` | Show / add / extract the glossary |
| `coco clean <meeting>\|--all [--apply]` | Remove Whisper hallucinations from existing transcripts (preview without `--apply`) |
| `coco memory [text]` | Show / append global memory |
| `coco memorize [meeting\|--all\|--compact]` | Extract long-term memory / compact it |
| `coco lang [code] [--output …]` | Interface and AI output language |
| `coco ai show\|test\|preset\|set\|task` | AI channels (`--profile fast` for the background channel) |
| `coco start` · `coco stop` · `coco restart` · `coco status` | Start in the background and open the browser · quit · restart · state and data location |
| `coco update` · `coco setup` · `coco shortcut [--remove]` | Update to the latest version · install / repair dependencies · Applications / Start-menu icon |
| `coco delete <meeting>` · `coco config [key value]` · `coco web` | Trash · config · run the web server in the foreground |

Template and mode ids: `minutes actions mood tension bias topics client hiring followup` ·
`track signals synthesis`. The original Chinese names are accepted as well.

## Data and configuration

Everything is ordinary folders and Markdown on your disk — inspect, back up, move at will:

```
library/<date-title>/   one meeting: audio + transcript.md/json + reports/*.md
library/_briefs/        daily briefs          library/_weekly/    weekly reports
library/_tracking/      cross-meeting insights library/_prep/     pre-meeting briefs
library/_trash/         trash
memory/memory.md        global memory (hand-written, injected everywhere)
memory/longterm.md      long-term memory (automatic, editable)
memory/glossary.md      glossary (names and terms)
coco.config.json        configuration (incl. API keys — git-ignored)
```

Useful keys (`coco config <key> <value>`): `ui_language` (auto / en / zh-CN / fr),
`output_language` (ui / source / ISO code), `whisper_model`, `language` (transcription),
`beam_size`, `hallucination_filter`, `transcribe_backend` (auto / mlx / faster / none), `record_mode`
(auto / browser / ffmpeg), `auto_memory`, `memory_merge` (delta / full), `ai_profiles`, `ai_tasks`, `claude_bin`,
`codex_bin`, `claude_extra_args`, `hf_endpoint`, `port`, `desktop_shortcut` (auto / created / off), `allowed_hosts`
(extra host names accepted besides this computer, advanced).
`COCO_ROOT` points the data directory elsewhere; `COCO_LANG` overrides the CLI language; `COCO_PORT` sets the
port; `COCO_PIP_INDEX` sets the package index used for installing dependencies.

## Known limits

- No speaker diarization for local transcription (speaker labels from imported material are kept)
- Whisper's anti-repetition logic means the glossary prompt mostly helps at the start of a
  recording; whole-file corrections are done by *✦ Correct names & terms*
- Prep's web search sends search terms to a search engine — tick it only when you want public
  intelligence; nothing else goes online (except model downloads and the API channel you chose)
- Browser recording captures the microphone only. To also record the other side of an online call on
  macOS, install a virtual audio device such as BlackHole, choose server recording in ⚙ Settings →
  Transcription & recording and select the input with `coco devices` / `audio_device`
- The ChatGPT (Codex) channel does not stream; web search exists on the Claude account channel only
- coco only accepts requests addressed to this computer (127.0.0.1 / localhost) and refuses
  state-changing requests from other web pages
- Legacy `.doc` is not read; save as `.docx` or `.txt`. Scanned PDFs need OCR first

## Development

```bash
.venv/bin/python tests/smoke.py      # ~240 assertions: temporary COCO_ROOT, stub claude / codex, fake local APIs
.venv/bin/python tests/check_i18n.py # every key used in Python / the UI exists in every locale
```

Neither touches real data or spends model quota (Windows: `.venv\Scripts\python tests\smoke.py`).

## Changelog

### 2026-09-25 · One way to start, and ChatGPT accounts work too
- **Start**: one double-click file per system at the root (`coco.command` on Mac, `coco.bat` on Windows);
  `run.sh` / `run.bat` are gone. Start-up lives in the cross-platform `coco start`: finds a native Python
  (or installs one with uv), measures PyPI against mirrors, skips a taken port, runs in the background
  without a terminal, only opens the browser on a second double-click, and restarts an older running
  version after an update
- **One-line install**: `scripts/install.sh` / `install.ps1`; after the first start coco is in
  Applications / the Start menu and on the desktop
- **Quit and update**: ⏻ in the interface; `coco stop / restart / status / update / shortcut`
- **ChatGPT channel**: new `codex-cli` type analyses on a ChatGPT plan; with nothing configured coco picks
  a logged-in Claude or Codex by itself
- **API channels**: presets for OpenAI, Anthropic, Gemini, OpenRouter, DeepSeek, Qwen (CN / intl), Kimi
  (CN / intl), GLM / Z.ai, Doubao, MiniMax, SiliconFlow, Ollama, LM Studio; *Load list* fetches the live
  models; HTTP errors become actionable hints; `max_completion_tokens`, `<think>` blocks and proxies handled
- **First-run guide**: without a usable AI, the first screen asks which account you have; Settings is now
  four tabs: AI channel / Transcription & recording / Language / About
- **Recording**: in the browser, so Windows and Linux can record too; saved every 4 seconds and recoverable
  after a closed tab or a restart; live level and timer, a warning after 20 s of silence
- **Fixes**: Apple-silicon Macs without Homebrew's ffmpeg could not transcribe any audio (mlx-whisper calls
  the ffmpeg command); a static ffmpeg now ships with the dependencies. claude / codex not found when
  started from the icon (the login shell's PATH is now merged in)
- **Security**: only this computer's host names are accepted and state-changing requests from other web
  pages are refused (DNS rebinding / cross-site requests)
- Smoke test grows to ~240 checks (codex stub, fake API servers with the usual failures)

### 2026-09-01 · Out-of-the-box experience for non-technical users
- **macOS one-click setup**: the first double-click of `coco.command` builds the environment,
  installs dependencies and explains what to do when Python or the network is missing
- **AI-not-ready guidance**: with no claude CLI and no API key, a banner in the UI leads to the
  settings dialog or to per-platform copy-paste install steps for Claude
- **small transcription model** (≈ 0.5 GB) for low-spec machines; the CPU hint now suggests it
- Model downloads announce their size and expected wait; missing Python on Windows points to the
  Microsoft Store route

### 2026-08-26 · Multilingual, streaming, pluggable AI channels
- **Three interface languages** (English, 简体中文, français) with per-request language for every
  message, file label and download name; **AI output language** independent of the interface
  (follow UI / follow material / fixed language); libraries survive language switches
- **Streaming**: reports, chat, insights, briefs and prep render as they are written
- **Incremental long-term memory** merge (seconds instead of a full rewrite) + on-demand compaction
- **AI channels**: Claude CLI (default), Anthropic-compatible and OpenAI-compatible HTTP providers
  (DeepSeek, OpenAI, Ollama …); primary/background split per task; connection test; presets
- **More transcript formats**: pdf, odt, rtf, html, eml, csv/tsv, sbv, lrc, ass/ssa, richer JSON
  detection (speakers, millisecond offsets, word-level exports); pasted SRT/VTT/JSON detected;
  transcript download as md/txt/srt/vtt/json
- **Friendlier no-engine experience**: a help dialog when audio is dropped on a machine that cannot
  transcribe, CPU-speed warning, paste-a-transcript shortcut
- Claude CLI started without MCP servers / tools and in a clean working directory
- Smoke test grown to ~150 assertions, plus an i18n completeness check

### 2026-08-25 · Windows support
- `run.bat` one-click setup; `bin\coco.cmd`; claude CLI located via PATHEXT; UTF-8 subprocesses
- Engine abstraction: mlx-whisper on Apple Silicon, faster-whisper elsewhere (CUDA/CPU)
- `.docx` import; UI adapts to local capabilities

### 2026-08-24
- Weekly report; README rewritten as product documentation

### 2026-08-21 · From "minutes" to "before – during – after – memory"
- Pre-meeting brief, glossary, insight modes (tracking / deep signals / research synthesis),
  text material import, knowledge base, follow-up draft template, smoke test

### 2026-06 · First versions
- Local Whisper transcription, analysis templates, chat, daily brief, long-term memory,
  cross-meeting tracking, search, trash, editing, name correction
