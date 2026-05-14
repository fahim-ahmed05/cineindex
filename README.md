# CineIndex

CineIndex is a high-performance, terminal-based media indexer and browser for directory-style web servers.

### 🖼️ Preview

![Preview](.github/preview.png)

### 🎬 Features

- ⚡ Fast crawler — Scans directory-style servers and builds a local SQLite index  
- 🔄 Incremental updates — Re-indexes only changed directories  
- 🔍 Fuzzy search (fzf-style) — Handles 200k+ entries instantly  
- 🎥 MPV integration — Stream directly with resume & playlist support  
- 📝 Watch history — Tracks recently watched media  
- ⬇️ Download manager — Queue multiple aria2c downloads  
- ⚙️ Configurable — Extensions, blocked folders, mpv args, download dir  
- 🖥️ TUI menu — Build/Update/Search/History/Download options  

### 📦 Requirements

- Python 3.10+
- mpv
- aria2c
- fzf (optional, but recommended)
- uv (recommended) or pip (if you know how to use it)

### 📥 Installation

Install:

```bash
uv tool install git+https://github.com/fahim-ahmed05/cineindex.git
```
### ▶️ Run CineIndex

Just type in a terminal:

```bash
cineindex
```


### 📁 File Locations (Config, Database, History)

CineIndex stores all persistent data in OS-appropriate application directories, managed via `platformdirs`.

#### Linux

Config directory:

```bash
~/.config/CineIndex/
```

Data directory:

```bash
~/.local/share/CineIndex/
```

Actual files:

```bash
~/.config/CineIndex/
├── config.json  
└── roots.json  

~/.local/share/CineIndex/
├── media_index.db  
└── cineindex-mpv-events.log  
```

#### MacOS

Config & data (Apple merges them):

```zsh
~/Library/Application Support/CineIndex/
```
Files:

```zsh
~/Library/Application Support/CineIndex/
├── config.json  
├── roots.json  
├── media_index.db  
└── cineindex-mpv-events.log  
```

#### Windows

Config & data:

```powershell
$env:LocalAppData\CineIndex\CineIndex\
```

Files:

```pwsh
$env:LocalAppData\CineIndex\CineIndex\
├── config.json  
├── roots.json  
├── media_index.db  
└── cineindex-mpv-events.log  
```

### ⚙️ Configuration Files

Created automatically on first run.

#### Example roots.json

```json
[
  {
    "url": "http://10.12.100.34/",
    "tag": "FTP"
  }
]
```

#### Example config.json

```json
{
  "video_extensions": ["mp4", "mkv", "avi"],
  "blocked_dirs": ["Ebooks", "Software", "Games"],
  "mpv_args": [
    "--save-position-on-quit",
    "--fullscreen",
    "--watch-later-options=start"
  ],
  "download_dir": ""
}
```

### 🎞️ Watch History

Playback events are recorded by `cineindex-history.lua`, which MPV loads automatically.

CineIndex sets this environment variable when launching MPV:

```python
CINEINDEX_HISTORY_PATH=/path/to/data/cineindex-mpv-events.log
```

Each played media generates a JSON line like:

```json
{"Name":"Movie Title","Url":"http://server/file.mkv","Time":"2024-03-01 21:42:10"}
```

This is used to populate the in-app **Watch History** menu and resume playback.





