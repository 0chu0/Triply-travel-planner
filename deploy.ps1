<#
.SYNOPSIS
    Triply 一键部署脚本：把本地改动同步到云服务器并重建后端容器。

.DESCRIPTION
    自动处理三类文件，避免手动 scp + build 出错：
      1) frontend/**     → 挂载卷，scp 覆盖后 force-recreate 即生效（无需重建镜像）
      2) app/** 及 scripts/** → 后端代码打进镜像，必须 docker compose up -d --build
      3) .env           → 环境变量，改后必须 force-recreate（restart 不重读 env_file）

    用法（在项目根目录 E:\travel-planner 下）：
      .\deploy.ps1                # 增量同步：只上传本次有改动的文件，改后端则自动 --build
      .\deploy.ps1 -Build         # 强制全量重建（等价于改了后端代码）
      .\deploy.ps1 -FrontendOnly  # 只同步 frontend 并 force-recreate（最快）
      .\deploy.ps1 -All           # 全量上传 app/scripts/frontend/.env 并重建
      .\deploy.ps1 -SkipBuild     # 只 scp，不重建（罕见：确认容器会自动热加载时才用）

.NOTES
    私钥/服务器地址/远端路径集中配置在下方 CONFIG 区，改动时只需改这里。
#>
param(
    [switch]$Build,         # 强制重建镜像
    [switch]$FrontendOnly,  # 只同步前端
    [switch]$All,           # 全量上传
    [switch]$SkipBuild      # 只上传不重建
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # 去掉 scp 进度条刷屏

# ================= CONFIG（按需修改） =================
$SSH_KEY   = "C:\Users\Administrator\.ssh\zhixing.pem"
$REMOTE    = "root@47.95.232.253"
$REMOTE_DIR = "/root/travel-planner"
$CONTAINER = "travel_backend"
$LOCAL_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
# ======================================================

function Log { param([string]$Msg, [string]$Color = "White") Write-Host $Msg -ForegroundColor $Color }
function LogOk  { param([string]$Msg) Log "[OK]   $Msg" Green }
function LogWarn{ param([string]$Msg) Log "[WARN] $Msg" Yellow }
function LogErr { param([string]$Msg) Log "[ERR]  $Msg" Red }

# SSH 选项：禁用主机密钥提示，避免首次连接卡住
$SSH_OPTS = @("-i", $SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=25")

# ---------- 0. 前置检查 ----------
Log "==============================================" Cyan
Log " Triply 一键部署" Cyan
Log "==============================================" Cyan

if (-not (Test-Path $SSH_KEY)) { LogErr "私钥不存在: $SSH_KEY"; exit 1 }
if (-not (Test-Path (Join-Path $LOCAL_ROOT "docker-compose.yml"))) {
    LogErr "未在项目根目录运行（找不到 docker-compose.yml）: $LOCAL_ROOT"; exit 1
}

function Test-SSH {
    & ssh @SSH_OPTS $REMOTE "echo SSH_OK" 2>$null
    return ($LASTEXITCODE -eq 0)
}
Log "检查 SSH 连通性..."
if (-not (Test-SSH)) {
    LogErr "SSH 连接失败，请确认私钥/网络/服务器状态"; exit 1
}
LogOk "SSH 连通"

# ---------- 1. 决定要上传哪些文件 ----------
function Get-TrackedFiles {
    # 参与部署的顶层：app、scripts、frontend、.env
    $items = @()
    foreach ($dir in @("app", "scripts", "frontend")) {
        if (Test-Path (Join-Path $LOCAL_ROOT $dir)) {
            Get-ChildItem -Path (Join-Path $LOCAL_ROOT $dir) -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch "__pycache__|\.pyc$|\.log$" } |
                ForEach-Object { $items += $_.FullName }
        }
    }
    $envPath = Join-Path $LOCAL_ROOT ".env"
    if (Test-Path $envPath) { $items += $envPath }
    return $items
}

# 用 git 判断"本次改了哪些文件"（更可靠）；无 git 仓库时退回全量
function Get-ChangedFiles {
    $gitChanged = $null
    try {
        $staged = @(git -C $LOCAL_ROOT diff --cached --name-only 2>$null)
        $unstaged = @(git -C $LOCAL_ROOT diff --name-only 2>$null)
        $untracked = @(git -C $LOCAL_ROOT ls-files --others --exclude-standard 2>$null)
        $gitChanged = @($staged + $unstaged + $untracked | Where-Object { $_ } | Sort-Object -Unique)
        # 只保留参与部署的文件（app/scripts/frontend/.env）
        $gitChanged = @($gitChanged | Where-Object {
            $_ -match "^(app|scripts|frontend)/" -or $_ -eq ".env"
        })
    } catch { $gitChanged = $null }

    if ($null -ne $gitChanged -and $gitChanged.Count -gt 0) {
        return @($gitChanged | ForEach-Object { Join-Path $LOCAL_ROOT ($_ -replace "/", "\") })
    }
    return $null
}

$files = @()
if ($All) {
    $files = @(Get-TrackedFiles)
    Log "模式: 全量上传（app/scripts/frontend/.env 全部文件，共 $($files.Count) 个）" Yellow
} elseif ($FrontendOnly) {
    $files = @(Get-ChildItem -Path (Join-Path $LOCAL_ROOT "frontend") -Recurse -File |
        Where-Object { $_.FullName -notmatch "__pycache__|\.pyc$" })
    Log "模式: 仅前端（共 $($files.Count) 个）" Yellow
} else {
    $files = @(Get-ChangedFiles)
    if ($files.Count -eq 0) {
        LogWarn "未检测到改动文件（可能不是 git 仓库，或确实没有改动）。改用全量上传。"
        $files = @(Get-TrackedFiles)
    }
    Log "模式: 增量同步（本次改动 $($files.Count) 个文件）" Yellow
}

if ($files.Count -eq 0) {
    LogWarn "没有需要上传的文件，退出。"
    exit 0
}

# ---------- 2. 批量 scp（按顶层目录打包，避免逐文件 63 个连接瞬间打满 sshd 的 MaxStartups 被限流） ----------
function Invoke-Scp {
    param([string[]]$Sources, [string]$RemoteTarget, [switch]$Recursive)
    $scpArgs = @("-i", $SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=25")
    if ($Recursive) { $scpArgs += "-r" }
    $scpArgs += $Sources
    $scpArgs += "$REMOTE`:$RemoteTarget"

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # scp 的 stderr 不应升级为终止错误
    $ok = $false
    try {
        for ($a = 1; $a -le 3; $a++) {
            & scp @scpArgs 2>$null
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
            if ($a -lt 3) { Start-Sleep -Seconds 2; LogWarn "  连接超时，2 秒后重试 ($a/3)..." }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    return $ok
}

# 分类：本次改动涉及哪些顶层桶（每个桶一次 scp 连接，远少于逐文件）
$hasBackend = $false
$hasFrontend = $false
$hasEnv = $false
$buckets = @{ app = $false; scripts = $false; frontend = $false; env = $false }
foreach ($f in $files) {
    $rel = $f.Substring($LOCAL_ROOT.Length + 1)
    if ($rel -match "^(app|scripts|frontend)/") { $buckets[$Matches[1]] = $true }
    elseif ($rel -eq ".env") { $buckets["env"] = $true }
}
if ($buckets["app"] -or $buckets["scripts"]) { $hasBackend = $true }
if ($buckets["frontend"]) { $hasFrontend = $true }
if ($buckets["env"]) { $hasEnv = $true }

$okBuckets = 0; $failBuckets = 0
if ($buckets["app"]) {
    if (Invoke-Scp -Sources (Join-Path $LOCAL_ROOT "app") -RemoteTarget $REMOTE_DIR -Recursive) { $okBuckets++; Log "  上传目录 app/" DarkGray }
    else { $failBuckets++; LogErr "  上传失败 app/" }
}
if ($buckets["scripts"]) {
    if (Invoke-Scp -Sources (Join-Path $LOCAL_ROOT "scripts") -RemoteTarget $REMOTE_DIR -Recursive) { $okBuckets++; Log "  上传目录 scripts/" DarkGray }
    else { $failBuckets++; LogErr "  上传失败 scripts/" }
}
if ($buckets["frontend"]) {
    if (Invoke-Scp -Sources (Join-Path $LOCAL_ROOT "frontend") -RemoteTarget $REMOTE_DIR -Recursive) { $okBuckets++; Log "  上传目录 frontend/" DarkGray }
    else { $failBuckets++; LogErr "  上传失败 frontend/" }
}
if ($buckets["env"]) {
    if (Invoke-Scp -Sources (Join-Path $LOCAL_ROOT ".env") -RemoteTarget "$REMOTE_DIR/.env") { $okBuckets++; Log "  上传 .env" DarkGray }
    else { $failBuckets++; LogErr "  上传失败 .env" }
}
LogOk "上传完成：成功 $okBuckets 个目录/文件，失败 $failBuckets 个"

if ($failBuckets -gt 0) {
    LogErr "有上传失败，中止部署（避免半成品上线）。可重新运行本脚本重试。"
    exit 1
}

# ---------- 3. 重建 / 重启容器 ----------
$needBuild = $Build -or $hasBackend   # 后端代码改动 → 必须 --build
$needRecreate = $needBuild -or $hasEnv -or $hasFrontend -or $All

if ($SkipBuild) {
    LogWarn "-SkipBuild：只上传，不重启容器。"
} elseif ($needBuild) {
    Log "检测到后端代码改动 → 重建镜像并重启（docker compose up -d --build backend）" Yellow
    $remoteCmd = "cd $REMOTE_DIR && docker compose up -d --build backend"
    & ssh @SSH_OPTS $REMOTE $remoteCmd 2>&1 | ForEach-Object { Log $_ DarkGray }
    if ($LASTEXITCODE -ne 0) { LogErr "重建失败，请检查上方构建日志"; exit 1 }
    LogOk "重建完成"
} elseif ($needRecreate) {
    Log "仅前端/环境变量改动 → force-recreate（不重建镜像）" Yellow
    $remoteCmd = "cd $REMOTE_DIR && docker compose up -d --force-recreate backend"
    & ssh @SSH_OPTS $REMOTE $remoteCmd 2>&1 | ForEach-Object { Log $_ DarkGray }
    if ($LASTEXITCODE -ne 0) { LogErr "force-recreate 失败"; exit 1 }
    LogOk "容器已重建"
}

# ---------- 4. 等待健康 ----------
if (-not $SkipBuild) {
    Log "等待容器健康（最多 120 秒）..." Yellow
    $healthy = $false
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Seconds 5
        $status = & ssh @SSH_OPTS $REMOTE "docker ps --filter name=$CONTAINER --format '{{.Status}}'" 2>$null
        $status = ($status -join " ")
        if ($status -match "healthy") { $healthy = $true; break }
        if ($status -match "Exited|Restarting") {
            LogErr "容器异常: $status"
            & ssh @SSH_OPTS $REMOTE "docker logs $CONTAINER --tail 30" 2>&1 | ForEach-Object { Log $_ DarkGray }
            exit 1
        }
    }
    if ($healthy) { LogOk "容器已 healthy" }
    else { LogWarn "容器尚未在 120 秒内进入 healthy（可能仍在加载 MCP 工具），请稍后自行确认。" }
}

# ---------- 5. 汇总 ----------
Log "==============================================" Cyan
LogOk "部署流程结束。"
if (-not $SkipBuild) {
    Log "访问地址: http://47.95.232.253:8000/app/" Green
}
Log "==============================================" Cyan
