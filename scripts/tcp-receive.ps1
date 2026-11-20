# Receive one TCP stream and save it to a file.
#
#   powershell -File scripts\tcp-receive.ps1 -Port 8020 -Out run\guest.log [-TimeoutSec 120]
#
# The guest pushes with Apple's netcat:  nc 10.0.2.2 8020 < /tmp/file
# (slirp's host alias is 10.0.2.2). Listens on all interfaces so the guest can
# reach it; prints the byte count and exits non-zero when nothing arrived.
param(
    [int]$Port = 8020,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$TimeoutSec = 120
)

$ErrorActionPreference = 'Stop'
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
$listener.Start()
try {
    $accept = $listener.AcceptTcpClientAsync()
    if (-not $accept.Wait($TimeoutSec * 1000)) { throw "no connection on port $Port within $TimeoutSec s" }
    $client = $accept.Result
    $stream = $client.GetStream()
    $dir = Split-Path $Out -Parent
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $file = [System.IO.File]::Create((Resolve-Path -LiteralPath $dir).Path + '\' + (Split-Path $Out -Leaf))
    try {
        $stream.CopyTo($file)
    } finally {
        $file.Close()
        $client.Close()
    }
    "received $((Get-Item $Out).Length) bytes -> $Out"
} finally {
    $listener.Stop()
}
