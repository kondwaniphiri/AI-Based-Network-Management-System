$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.tools\python\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { $pythonPath = 'python' }
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $projectRoot '.tools\zig-global-cache'
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $projectRoot '.tools\zig-cache'
New-Item -ItemType Directory -Force (Join-Path $projectRoot '.tools') | Out-Null
& $pythonPath -m ziglang cc -std=c11 -Wall -Wextra -Werror -shared firmware/esp32_gateway/main/snmp_codec.c -o .tools/snmp_codec.dll
if ($LASTEXITCODE -ne 0) { throw 'Native codec compilation failed' }
& $pythonPath -m unittest discover -s tests -p test_gateway_snmp.py -v
if ($LASTEXITCODE -ne 0) { throw 'Gateway tests failed' }
