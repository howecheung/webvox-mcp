$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$DistDir = Join-Path $ProjectRoot "dist"

# fnpack 查找顺序：FNPACK 环境变量 > PATH > 开发机上 .fpk-refs/bin 的本地副本
$Fnpack = $null
if ($env:FNPACK) {
  $Fnpack = $env:FNPACK
}
elseif (Get-Command fnpack.exe -ErrorAction SilentlyContinue) {
  $Fnpack = (Get-Command fnpack.exe -ErrorAction SilentlyContinue).Source
}
else {
  $LocalRef = Join-Path (Split-Path -Parent $ProjectRoot) ".fpk-refs\bin\fnpack.exe"
  if (Test-Path -LiteralPath $LocalRef) {
    $Fnpack = $LocalRef
  }
}
if (-not $Fnpack -or -not (Test-Path -LiteralPath $Fnpack)) {
  throw "未找到 fnpack。请从 fnOS 开发者官网下载 fnpack 并放入 PATH，或用 FNPACK 环境变量指定路径。"
}

New-Item -ItemType Directory -Force $DistDir | Out-Null

Push-Location $DistDir
try {
  & $Fnpack build -d $ProjectRoot
  if ($LASTEXITCODE -ne 0) {
    throw "fnpack build 失败，退出码 $LASTEXITCODE"
  }
  # 同时生成带版本号的提交/发布产物，例如 webvox-mcp-1.6.0.fpk
  $Version = [regex]::Match(
    (Get-Content -LiteralPath (Join-Path $ProjectRoot "manifest") -Raw),
    '(?m)^version\s*=\s*([0-9.]+)\s*$'
  ).Groups[1].Value
  if (-not $Version) {
    throw "无法从 manifest 解析 version"
  }
  $Named = Join-Path $ProjectRoot "webvox-mcp-$Version.fpk"
  Copy-Item -LiteralPath (Join-Path $DistDir "webvox-mcp.fpk") -Destination $Named -Force
  Write-Host "带版本号产物已生成：$Named"
}
finally {
  Pop-Location
}

Write-Host ""
Write-Host "FPK 已生成：$DistDir\webvox-mcp.fpk"
