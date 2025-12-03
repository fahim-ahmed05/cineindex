# CineIndex

CineIndex is a high-performance, terminal-based media indexer and browser for directory-style web servers (Apache-style, h5ai, autoindex, etc.).

## 🎬 Features

- ⚡ Fast crawler — Scans directory-style servers and builds a local SQLite index  
- 🔄 Incremental updates — Re-indexes only changed directories  
- 🔍 Fuzzy search (fzf-style) — Handles 200k+ entries instantly  
- 🎥 MPV integration — Stream directly with resume & playlist support  
- 📝 Watch history — Tracks recently watched media  
- ⬇️ Download manager — Queue multiple aria2c downloads  
- ⚙️ Configurable — Extensions, blocked folders, mpv args, download dir  
- 🖥️ TUI menu — Build/Update/Search/History/Download options  

## 📦 Requirements

- Python 3.10+
- mpv
- aria2c
- uv (recommended) or pip (if you know how to use it)

## 📥 Installation

Clone and install:

    git clone git@github.com:fahim-ahmed05/cineindex.git
    cd cineindex
    uv sync
    uv tool install .

## ▶️ Run CineIndex

Just type in a terminal:

    cineindex



# 📁 File Locations (Config, Database, History)

CineIndex stores all persistent data in OS-appropriate application directories, managed via `platformdirs`.

## Linux

Config directory:

    ~/.config/CineIndex/

Data directory:

    ~/.local/share/CineIndex/

Actual files:

- ~/.config/CineIndex/config.json  
- ~/.config/CineIndex/roots.json  
- ~/.local/share/CineIndex/media_index.db  
- ~/.local/share/CineIndex/cineindex-mpv-events.log  

## macOS

Config & data (Apple merges them):

    ~/Library/Application Support/CineIndex/

Files:

- ~/Library/Application Support/CineIndex/config.json  
- ~/Library/Application Support/CineIndex/roots.json  
- ~/Library/Application Support/CineIndex/media_index.db  
- ~/Library/Application Support/CineIndex/cineindex-mpv-events.log  

## Windows

Config:

    %APPDATA%\Fahim Ahmed\CineIndex\

Data:

    %LOCALAPPDATA%\Fahim Ahmed\CineIndex\

Files:

- %APPDATA%\Fahim Ahmed\CineIndex\config.json  
- %APPDATA%\Fahim Ahmed\CineIndex\roots.json  
- %LOCALAPPDATA%\Fahim Ahmed\CineIndex\media_index.db  
- %LOCALAPPDATA%\Fahim Ahmed\CineIndex\cineindex-mpv-events.log  



# ⚙️ Configuration Files

Created automatically on first run.

## Example roots.json

    [
      {
        "url": "http://10.12.100.34/",
        "tag": "FTP"
      }
    ]

## Example config.json

    {
      "video_extensions": ["mp4", "mkv", "avi"],
      "blocked_dirs": ["Ebooks", "Software"],
      "mpv_args": [
        "--save-position-on-quit",
        "--fullscreen",
        "--watch-later-options=start"
      ],
      "download_dir": ""
    }



# 🎞️ Watch History

Playback events are recorded by `cineindex-history.lua`, which MPV loads automatically.

CineIndex sets this environment variable when launching MPV:

    CINEINDEX_HISTORY_PATH=/path/to/data/cineindex-mpv-events.log

Each played media generates a JSON line like:

    {"Name":"Movie Title","Url":"http://server/file.mkv","Time":"2024-03-01 21:42:10"}

This is used to populate the in-app **Watch History** menu and resume playback.



# ☕ Support

If you find CineIndex helpful, consider supporting development:

<a href="https://www.buymeacoffee.com/fahim.ahmed" target="_blank">
  <img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" 
       alt="Buy Me A Coffee"
       style="height: 41px !important; width: 174px !important; box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5);" />
</a>



Enjoy CineIndex!
