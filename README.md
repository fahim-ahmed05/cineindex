# CineIndex

CineIndex is a high-performance, terminal-based media indexer and browser for directory-style web servers.

## Preview

![Preview](.github/preview.png)

## Features

- **Fast crawler** — Scans directory-style servers and builds a local SQLite index  
- **Incremental updates** — Re-indexes only changed directories  
- **Fuzzy search (fzf-style)** — Handles 200k+ entries instantly  
- **MPV integration** — Stream directly with resume & playlist support  
- **Watch history** — Tracks recently watched media  
- **Download manager** — Queue multiple aria2c downloads  
- **Configurable** — Extensions, blocked folders, mpv args, download dir  
- **TUI menu** — Build/Update/Search/History/Download options  

## Requirements

- Python 3.11+
- mpv
- aria2c
- fzf (optional, but recommended)
- uv (recommended) or pip (if you know how to use it)

## Installation

Install:

```bash
uv tool install git+https://github.com/fahim-ahmed05/cineindex.git
```
## Running CineIndex

Just type in a terminal:

```bash
cineindex
```


## File Locations

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

## Configuration Files

Created automatically on first run.

#### Example roots.json

Roots are organized by tag groups with shared configuration settings:

```json
[
  {
    "tag": "server1",
    "decode_percent": true,
    "dots_to_spaces": false,
    "threads": 15,
    "roots": [
      { "url": "http://192.168.1.1/" },
      { "url": "http://127.0.0.1/" }
    ]
  },
  {
    "tag": "server2",
    "decode_percent": true,
    "dots_to_spaces": true,
    "threads": 3,
    "roots": [
      { "url": "http://192.168.2.1/" },
      { "url": "http://127.0.0.2/" }
    ]
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

#### Additional configuration options

**Global options (in `config.json`):**

- **`max_per_root`**: integer. Limits the number of new files indexed per root during a single crawl. Use `0` for no limit (default).
- **`video_extensions`**: list of file extensions to index (e.g., `["mp4", "mkv", "avi"]`).
- **`blocked_dirs`**: list of directory names to skip during crawling (e.g., `["Ebooks", "Software"]`).
- **`mpv_args`**: custom MPV command-line arguments.
- **`download_dir`**: directory for aria2c downloads (empty string uses system default).

**Per-root options (in `roots.json`):**

Roots are organized into groups. Each group can share configuration settings that apply to all roots within it:

- **`tag`**: string. A label for this group (e.g., `"apache2"`, `"h5ai"`).
- **`roots`**: array. List of root URLs in this group.
- **`decode_percent`**: boolean. If `true`, decode percent-encoded paths (e.g., `%20` → space) for cleaner display. Default: `true`.
- **`dots_to_spaces`**: boolean. If `true`, convert dots in filenames to spaces (e.g., `Movie.Title.2023` → `Movie Title 2023`). Smart blocklist preserves dots in audio formats and bitrates. Default: `false`.
- **`threads`**: integer. Number of concurrent threads for crawling roots in this group (default: `15`).

Example showing a complete group configuration:

```json
{
  {
    "tag": "server3",
    "decode_percent": true,
    "roots": [
      { "url": "http://192.168.2.1/", "threads": 3 },
      { "url": "http://127.0.0.2/", "threads": 10, "dots_to_spaces": true }
    ]
  }
}
```

## Watch History

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

## Advanced Features

### Metadata Display

Media entries now display additional metadata:
- **File size**: Human-readable format (KB, MB, GB)
- **Modified date**: Last modification timestamp from the server

This metadata is also included in generated M3U playlists for enhanced media player support.

### Smart Filename Parsing

CineIndex employs intelligent filename processing to improve display quality:

- **Dot-to-space conversion**: When `dots_to_spaces` is enabled, dots are intelligently converted to spaces while preserving important patterns:
  - Audio formats: `5.1`, `2.0`, `7.1`
  - Bitrates and framerates: `1080.60`, `2.5Mbps`
  - Acronyms: `S.H.I.E.L.D`, `U.N.C.L.E`

- **Metadata tag stripping**: Removes quality indicators and tags from displayed names:
  - Resolution: `1080p`, `4K`
  - Codecs: `x264`, `x265`, `HEVC`
  - Audio: `AAC`, `DTS`, `AC3`, `5.1CH`
  - Format: `BluRay`, `WEBRip`, `WEB-DL`

- **h5ai suffix handling**: Automatically strips h5ai directory index suffixes for cleaner root names

### Episode Indexing

For TV series stored in standard formats (e.g., `Show Name S01E01.mkv`):
- Automatic show name extraction and normalization
- Episode metadata parsing (season and episode numbers)
- Fast episode lookup for series navigation
- Cached episode index (`fzf_ep_index.json`) for performance

### Concurrent Crawling

- **Configurable thread pool**: Each root can use a different thread count (default: 15)
- **URL tracking**: Prevents duplicate file submissions during concurrent directory traversal
- **Error tracking**: Maintains state during incremental crawls to safely clean up stale entries
- **Incremental updates**: Efficiently re-indexes only changed directories while preserving historical data

### Playlist Generation

Generated M3U playlists include:
- Root presentation labels for easy organization
- Media metadata (duration, file size)
- Proper encoding and formatting for media player compatibility





