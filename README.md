# coco — local meeting strategist

**Transcribe · prepare · see what was not said · build lasting knowledge — all on your own computer.**

[中文说明 → README.zh-CN.md](README.zh-CN.md)

coco is a local meeting AI tool (inspired by [YouNavi](https://younavi.me)). Feed it recordings,
interviews, chat logs or other people's minutes and it helps you **understand the situation before
a meeting**, **catch what was left unsaid after it**, and **turn every conversation into reusable
knowledge**.

Audio never leaves your machine (Whisper runs locally). Analysis goes through the `claude` CLI you
are already logged in to by default, so there is no extra API key, cloud account or subscription;
any Anthropic- or OpenAI-compatible endpoint (DeepSeek, OpenAI, a local Ollama …) can be plugged in
instead.

Interface in **English, 简体中文 and français**; AI output in any language the model speaks.
Works on macOS, Windows and Linux. Machines that cannot run a transcription engine still get every
analysis feature: drop in a transcript exported by any other tool (txt / docx / pdf / srt / vtt /
json …) or paste the text.

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
| ● Record | Microphone recording from the web UI (macOS), transcribed when stopped |
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
  and transcription language (auto-detect or any ISO code) switch in the top bar.
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

- **Interface**: English, 简体中文, français. Switch with the language select in the top bar, in
  ⚙ Settings, with `?lang=fr` in the URL, or `coco lang zh-CN` for the CLI. The choice is saved.
- **AI output language** (⚙ Settings): *same as the interface* (default), *same as the material*
  (write in whatever language the transcript is in), or any fixed language (Japanese, German …).
  It applies to reports, briefs, insights, prep, chat and the wording of memory and glossary.
- **Transcription language**: auto-detect or a fixed ISO code, in the top bar.
- Libraries survive language switches: coco recognises the memory/glossary headings of every
  shipped language, and old report files named in Chinese keep their labels.

Adding a language: copy `coco/locales/en.json` to `<code>.json`, translate, done (prompts stay in
English with a "write in <language>" directive; Chinese output uses the Chinese prompt set).
`python tests/check_i18n.py` verifies completeness.

## AI channels (Claude, DeepSeek, OpenAI, Ollama …)

By default every analysis runs through the **Claude Code CLI** you are logged in to. ⚙ Settings (or
`coco ai`) lets you define two channels:

- **Primary** — reports, insights, prep, chat
- **Background** — long-term memory merges, glossary extraction, name correction

Each channel is one of:

| Type | Use it for |
|---|---|
| `claude-cli` | The logged-in Claude Code CLI. With a base URL + API key the same CLI can talk to any Anthropic-compatible endpoint (DeepSeek's `https://api.deepseek.com/anthropic`); coco then starts it with `--bare` (under a second instead of several) |
| `anthropic` | Direct HTTPS to an Anthropic-compatible `/v1/messages` endpoint — Anthropic, DeepSeek /anthropic … |
| `openai` | Direct HTTPS to an OpenAI-compatible `/chat/completions` endpoint — DeepSeek, OpenAI, Ollama / LM Studio on localhost |

Presets fill in the URLs and model names (DeepSeek `deepseek-v4-pro` / `deepseek-v4-flash` as
documented by DeepSeek in August 2026; check their docs if a name is rejected). **Test connection**
runs one tiny request and reports latency. API keys stay in `coco.config.json` on your machine
(git-ignored) or come from an environment variable.

**Using DeepSeek well.** A sensible split: keep the primary channel on Claude for the analyses you
read (prep, insights, reports) and send the background channel to DeepSeek. Memory merges, glossary
extraction and name correction are frequent, mechanical and long-input tasks; a cheaper channel
there saves quota without touching what you read. If you route the primary channel to DeepSeek as
well, judge on your own meetings: run the same template through both (`coco ai test` gives raw
latency; quality has to be read). Two hard limits: web search in Prep only exists on the Claude CLI
channel, and HTTP providers stream straight from the API, so the CLI's tool sandbox is not involved.
The per-channel *context cap* protects models with smaller windows.

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

**Requirements:** Python 3.10+ · [Claude Code](https://claude.com/claude-code) installed and logged
in (`claude` works in a terminal) — or an API channel configured in Settings. The web UI listens on
127.0.0.1 only.

### macOS

```bash
git clone https://github.com/cocohahaha/coco.git
cd coco
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh        # starts the server and opens http://127.0.0.1:8765
```

Later: double-click `coco.command` or run `./run.sh` again (it will not start a second server).
Apple Silicon installs mlx-whisper automatically; the first transcription downloads the Whisper
model (turbo ≈ 1.6 GB, large ≈ 3 GB; a mirror is used automatically when huggingface.co is
unreachable). macOS asks for microphone permission on the first recording.

### Windows

1. Install [Python 3.10+](https://www.python.org/downloads/) (tick **Add python.exe to PATH**)
2. Install [Git for Windows](https://git-scm.com/download/win) (Claude Code needs it)
3. Install and log in to [Claude Code](https://claude.com/claude-code): run `claude` once
4. Download this repository (`git clone` or *Code → Download ZIP*) and **double-click `run.bat`**

The first run creates the virtual environment and installs dependencies (faster-whisper is a few
hundred MB); then the server starts and the browser opens. Close the black window to stop.
Command line: `bin\coco.cmd`.

**Typical Windows workflow: recording → transcript elsewhere → upload → analyse.** Most people do
not need Whisper on their PC: let your meeting software or a transcription service produce the
transcript, export `txt / docx / pdf / srt`, drop it into coco. If faster-whisper fails to install,
`run.bat` installs the core only and the UI hides the recording/model options; the transcript route
is unaffected. Local transcription without an NVIDIA GPU takes 10–30 minutes for a 45-minute file.

### Linux

Same as macOS (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, then
`.venv/bin/python -m coco web --open`); faster-whisper is used, recording from the web UI is
macOS-only.

## Command line

Everything the UI does (`alias coco="<repo>/bin/coco"` on macOS/Linux; `bin\coco.cmd` on Windows).
Output follows the interface language.

| Command | Purpose |
|---|---|
| `coco transcribe <files…>` | Import audio/video and transcribe (`--model turbo/large`) |
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
| `coco memory [text]` | Show / append global memory |
| `coco memorize [meeting\|--all\|--compact]` | Extract long-term memory / compact it |
| `coco lang [code] [--output …]` | Interface and AI output language |
| `coco ai show\|test\|preset\|set\|task` | AI channels (`--profile fast` for the background channel) |
| `coco delete <meeting>` · `coco config [key value]` · `coco web` | Trash · config · web UI |

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
`beam_size`, `transcribe_backend` (auto / mlx / faster / none), `auto_memory`, `memory_merge`
(delta / full), `ai_profiles`, `ai_tasks`, `claude_extra_args`, `hf_endpoint`.
`COCO_ROOT` points the data directory elsewhere; `COCO_LANG` overrides the CLI language.

## Known limits

- No speaker diarization for local transcription (speaker labels from imported material are kept)
- Whisper's anti-repetition logic means the glossary prompt mostly helps at the start of a
  recording; whole-file corrections are done by *✦ Correct names & terms*
- Prep's web search sends search terms to a search engine — tick it only when you want public
  intelligence; nothing else goes online (except model downloads and the API channel you chose)
- Web-UI recording is macOS-only; on Windows record with the system recorder or meeting software
- Legacy `.doc` is not read; save as `.docx` or `.txt`. Scanned PDFs need OCR first

## Development

```bash
.venv/bin/python tests/smoke.py      # ~150 assertions against a temporary COCO_ROOT with a stub claude
.venv/bin/python tests/check_i18n.py # every key used in Python / the UI exists in every locale
```

Neither touches real data or spends model quota (Windows: `.venv\Scripts\python tests\smoke.py`).

## Changelog

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
