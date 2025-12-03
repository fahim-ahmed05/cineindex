-- cineindex-history.lua
-- Logs file-loaded events as JSON lines to a log file next to this script.
local utils = require "mp.utils"

local script_dir = mp.get_script_directory() or "."
local logfile = utils.join_path(script_dir, "cineindex-mpv-events.log")

local function json_escape(str)
    if not str then
        return ""
    end
    str = str:gsub("\\", "\\\\")
    str = str:gsub("\"", "\\\"")
    str = str:gsub("\n", "\\n")
    str = str:gsub("\r", "\\r")
    return str
end

local function write_event()
    local path = mp.get_property("path") or ""
    local title = mp.get_property("media-title") or ""
    local t = os.date("%Y-%m-%d %H:%M:%S")

    title = json_escape(title)
    path = json_escape(path)

    local line = string.format('{"Name":"%s","Url":"%s","Time":"%s"}\n', title, path, t)

    local f, err = io.open(logfile, "a")
    if f then
        f:write(line)
        f:close()
    else
        mp.msg.warn("cineindex-history.lua: failed to open log file: " .. tostring(err))
    end
end

mp.register_event("file-loaded", write_event)
mp.msg.info("cineindex-history.lua loaded; logging to: " .. logfile)
