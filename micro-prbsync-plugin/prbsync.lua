VERSION = "1.0.0"

local micro = import("micro")
local shell = import("micro/shell")

function markStderr(err)
    micro.InfoBar():Message(err)
    micro.Log(err)
end

function onSave(bp)
    bp:Save()
    shell.JobSpawn("prbsync", {"mark", bp.buf.Path}, nil, markStderr, nil)
end
