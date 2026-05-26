param(
    [string]$CdpUrl = "http://127.0.0.1:9223"
)

$ErrorActionPreference = "Stop"

function Send-CdpMessage {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [object]$Payload
    )

    $json = $Payload | ConvertTo-Json -Compress -Depth 12
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $segment = [System.ArraySegment[byte]]::new($bytes)
    $null = $Socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
}

function Receive-CdpMessage {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket
    )

    $buffer = [byte[]]::new(65536)
    $stream = [System.IO.MemoryStream]::new()
    try {
        do {
            $segment = [System.ArraySegment[byte]]::new($buffer)
            $result = $Socket.ReceiveAsync($segment, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
            if ($result.Count -gt 0) {
                $stream.Write($buffer, 0, $result.Count)
            }
        } while (-not $result.EndOfMessage)

        $text = [System.Text.Encoding]::UTF8.GetString($stream.ToArray())
        if ([string]::IsNullOrWhiteSpace($text)) {
            return $null
        }
        return $text | ConvertFrom-Json
    }
    finally {
        $stream.Dispose()
    }
}

function Invoke-CdpCommand {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [int]$Id,
        [string]$Method,
        [hashtable]$Params = @{}
    )

    Send-CdpMessage -Socket $Socket -Payload @{ id = $Id; method = $Method; params = $Params }
    while ($true) {
        $message = Receive-CdpMessage -Socket $Socket
        if ($null -ne $message -and $message.id -eq $Id) {
            return $message
        }
    }
}

function Find-Frame {
    param(
        [object]$Node
    )

    if ($null -eq $Node) {
        return $null
    }
    if ($Node.frame.name -eq "content" -and $Node.frame.url -like "*samsungpop.com*") {
        return $Node.frame
    }
    foreach ($child in @($Node.childFrames)) {
        $found = Find-Frame -Node $child
        if ($null -ne $found) {
            return $found
        }
    }
    return $null
}

$targets = Invoke-RestMethod -Uri "$CdpUrl/json/list" -TimeoutSec 10
$target = $targets |
    Where-Object { $_.type -eq "page" -and $_.url -like "*samsungpop.com*" } |
    Select-Object -First 1

if ($null -eq $target) {
    Write-Output "NO_TAB"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($target.webSocketDebuggerUrl)) {
    Write-Output "NO_WEBSOCKET"
    exit 0
}

$socket = [System.Net.WebSockets.ClientWebSocket]::new()
try {
    $null = $socket.ConnectAsync([Uri]$target.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
    $null = Invoke-CdpCommand -Socket $socket -Id 1 -Method "Page.enable"
    $tree = Invoke-CdpCommand -Socket $socket -Id 2 -Method "Page.getFrameTree"
    $contentFrame = Find-Frame -Node $tree.result.frameTree

    if ($null -eq $contentFrame) {
        Write-Output "NO_CONTENT_FRAME"
        exit 0
    }

    if ($contentFrame.url -like "*login*") {
        Write-Output "LOGIN_PAGE"
        exit 0
    }

    $null = Invoke-CdpCommand -Socket $socket -Id 3 -Method "Page.navigate" -Params @{
        frameId = $contentFrame.id
        url = $contentFrame.url
    }
    Start-Sleep -Seconds 2

    $treeAfter = Invoke-CdpCommand -Socket $socket -Id 4 -Method "Page.getFrameTree"
    $contentFrameAfter = Find-Frame -Node $treeAfter.result.frameTree
    if ($null -ne $contentFrameAfter -and $contentFrameAfter.url -like "*login*") {
        Write-Output "LOGIN_PAGE"
        exit 0
    }

    Write-Output "REFRESHED"
}
finally {
    $socket.Dispose()
}
