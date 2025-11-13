# CineIndex

CineIndex is a high-performance, terminal-based media indexer and browser for directory-style web servers (like Apache and h5ai).  


## Features

- **Fast crawler** — Scans directory-style servers and builds a local SQLite index.  
- **Incremental updates** — Detects changed directories and updates only where needed.  
- **Fuzzy search (fzf-style)** — Quickly find movies or shows from 200k+ entries.  
- **MPV integration** — Streams directly using `mpv` with resume and playlist support.  
- **Watch history** — Tracks recently played items and series progress.  
- **Download manager** — Queue multiple downloads via `aria2c`.  
- **Configurable** — Define extensions, blocked folders, and mpv flags in `config.json`.  
- **TUI menu** — Simple text interface with build/update/search/history options.


## Requirements

- Python 3.10+
- [MPV](https://mpv.io/)
- [aria2c](https://aria2.github.io/)
- [uv](https://github.com/astral-sh/uv) (or `pip`) for dependency management


## Installation

```bash
git clone https://github.com/yourusername/CineIndex.git
cd CineIndex
uv sync
```

## Run CineIndex

```bash
uv run cineindex
```


## Configuration

CineIndex creates demo `roots.json` and `config.json` on first run.

```jsonc
// roots.json
[
  {
    "url": "http://10.12.100.34/",
    "tag": "FTP"
  }
]

// config.json
{
  "video_extensions": ["mp4", "mkv", "avi"],
  "blocked_dirs": ["Ebooks", "Software"],
  "mpv_args": ["--save-position-on-quit", "--fullscreen", "--watch-later-options=start,volume,mute"],
  "download_dir": ""
}
```


## Watch History

MPV logs playback events through a built-in Lua script (`cineindex-history.lua`)
so you can easily resume your last watched shows and track series progress.


## Support

If you run into any problems or have suggestions, please report them on the GitHub page.

If you like this tool, consider buying me a coffee.

<a href="https://www.buymeacoffee.com/fahim.ahmed" target="_blank">
  <img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" 
       alt="Buy Me A Coffee" 
       style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5); -webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5);" />
</a>