<#
.SYNOPSIS
    启动（或重启）本机 Celery worker，消费 generation / agent / agent.workflow / maintenance 四个队列。

.DESCRIPTION
    1. 使用 api\.venv 内的 Python 与 -A app.worker.celery_app.celery_app 启动 worker，工作目录固定为 api 目录，确保读取 api\.env。
    2. Windows 限制：必须使用 --pool=solo；不能使用 -B（内嵌 beat 在 Windows 不受支持）。
       因此本脚本只起 worker，不含 beat。周期维护（dispatch_outbox / agent_runtime）在容器化部署由 docker-compose 的 worker 以 -B 运行。
       本地缺 beat 时，新建 Agent 任务仍会由 API 在 app/services/outbox.py 内联投递 dispatch 任务，主流程可跑通；
       但租约过期 / 任务超时的周期性回收不会自动执行。
    3. 就绪判定用 `celery inspect ping`（收到 pong 即视为就绪），不依赖 HTTP。
    4. 日志写入 api\logs\worker.out.log 与 worker.err.log，PID 写入 api\logs\worker.pid。
    5. venv 的 python.exe 可能是 uv trampoline（启动器派生真实子进程），因此停止时按命令行匹配并结束整个进程树，
       保证 -Restart 能真正停掉 worker，而不是只杀掉启动器。
    6. 脚本不包含也不读取任何账号、密码、密钥等凭据，凭据仍由 api\.env 管理。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File api\scripts\start_worker.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File api\scripts\start_worker.ps1 -Restart
#>
[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [switch]$Restart,
    [int]$WaitSeconds = 40
)

$ErrorActionPreference = "Stop"

$ApiDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ApiDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ApiDir "logs"
$OutLog = Join-Path $LogDir "worker.out.log"
$ErrLog = Join-Path $LogDir "worker.err.log"
$PidFile = Join-Path $LogDir "worker.pid"
$AppTarget = "app.worker.celery_app.celery_app"
# 必须包含 CELERY_KNOWLEDGE_QUEUE（默认 knowledge），否则知识库导入任务会投进一个
# 没有消费者认领的队列，界面上表现为状态永远停在"排队中"而不报错。
$Queues = "generation,agent,agent.workflow,maintenance,knowledge"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境解释器：$Python。请先在 api 目录创建 .venv 并安装依赖。"
    exit 1
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# 后续的 celery inspect ping 依赖 cwd=api 才能 import app 包；Start-Process 另行用 -WorkingDirectory 固定。
Push-Location $ApiDir

function Test-ManagedCommandLine {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    # 只认可本项目真正在跑 worker 子命令的进程；inspect/beat 等其它 celery 命令不匹配。
    return ($CommandLine -match "celery") -and ($CommandLine -match "celery_app\s+worker")
}

function Test-WorkerPing {
    # 原生命令的 stderr 在 5.1 下可能被 $ErrorActionPreference=Stop 当成终止错误，这里局部放宽并恢复。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = (& $Python -m celery -A $AppTarget inspect ping --timeout 2 2>&1 | Out-String)
        return ($out -match "pong")
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Get-WorkerProcesses {
    # 返回所有匹配本项目 worker 命令行的 python 进程（可能包含 uv trampoline 启动器与其真实子进程）。
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue
    if (-not $procs) { return @() }
    return @($procs | Where-Object { Test-ManagedCommandLine -CommandLine $_.CommandLine })
}

function Get-ActualWorkerProcess {
    # 真实 worker 是“父进程也在匹配集合内”的子进程；找不到则退回第一个匹配，保证 PID 可停止。
    $procs = Get-WorkerProcesses
    if ($procs.Count -eq 0) { return $null }
    $ids = @($procs | ForEach-Object { $_.ProcessId })
    $child = $procs | Where-Object { $ids -contains $_.ParentProcessId } | Select-Object -First 1
    if ($child) { return $child }
    return ($procs | Select-Object -First 1)
}

function Stop-WorkerProcesses {
    # 逐条按 PID 结束整棵进程树，兼容 trampoline 的“启动器 + 子进程”结构。
    $procs = Get-WorkerProcesses
    if ($procs.Count -eq 0) { return $false }
    # 树杀父进程会顺带结束子进程，后续对子进程再 taskkill 会报“找不到进程”；
    # 这是正常情况，必须局部放宽错误偏好，避免 stderr 被 Stop 升级为终止错误而中断重启流程。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        foreach ($proc in $procs) {
            Write-Host "正在停止 worker 进程 PID $($proc.ProcessId) ..."
            & taskkill /PID $proc.ProcessId /T /F 2>&1 | Out-Null
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return $true
}

$healthy = Test-WorkerPing
if ($healthy -and -not $Restart) {
    Write-Host "Celery worker 已在运行（inspect ping 收到 pong），无需重复启动。"
    $existing = Get-ActualWorkerProcess
    if ($existing) {
        Set-Content -LiteralPath $PidFile -Value $existing.ProcessId -Encoding ASCII
        Write-Host "记录 PID：$($existing.ProcessId)"
    } else {
        Write-Host "未能定位 worker 进程 PID（可能由其它终端手动启动），如需精确重启请先手动确认。"
    }
    Write-Host "日志：$OutLog"
    Pop-Location
    exit 0
}

if ($Restart) {
    $stopped = Stop-WorkerProcesses
    if ($stopped) {
        Start-Sleep -Seconds 2
    } elseif ($healthy) {
        Write-Warning "worker 在响应 ping，但无法定位其进程，未做停止。请人工确认后再重启。"
        Pop-Location
        exit 1
    }
    if (Test-Path $PidFile) { Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue }
}

if (Test-WorkerPing) {
    Write-Error "已有 worker 在响应 ping，但非本脚本管理。请勿直接覆盖，先确认其启动方式。"
    Pop-Location
    exit 1
}

# Windows 本地必须 solo 池，且不能带 -B（内嵌 beat 不支持）。
$startArgs = @(
    "-m", "celery",
    "-A", $AppTarget,
    "worker",
    "--loglevel=INFO",
    "-Q", $Queues,
    "--pool=solo"
)
Write-Host "启动 worker：$Python $($startArgs -join ' ')"
Write-Host "工作目录：$ApiDir"

# 本机回访地址必须绕过代理，否则 worker 侧到本机服务的请求会被代理转发而失败。
$noProxyValue = "127.0.0.1,localhost,::1"
$env:NO_PROXY = $noProxyValue
$env:no_proxy = $noProxyValue

$process = Start-Process -FilePath $Python `
    -ArgumentList $startArgs `
    -WorkingDirectory $ApiDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "已提交启动（启动器 PID $($process.Id)），等待 worker 响应 ping ..."

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 1000
    if ($process.HasExited) {
        Write-Error "worker 进程已退出（ExitCode=$($process.ExitCode)），请查看错误日志：$ErrLog"
        Pop-Location
        exit 1
    }
    if (Test-WorkerPing) { $ready = $true; break }
}

if (-not $ready) {
    Write-Error "在 $WaitSeconds 秒内未收到 worker 的 pong。请查看日志：$ErrLog"
    Pop-Location
    exit 1
}

# 记录真实 worker 子进程 PID（而非 trampoline 启动器），保证 -Restart 精确。
$actual = Get-ActualWorkerProcess
$realPid = if ($actual) { $actual.ProcessId } else { $process.Id }
Set-Content -LiteralPath $PidFile -Value $realPid -Encoding ASCII

Write-Host "worker 就绪（PID $realPid），消费队列：$Queues"
Write-Host "标准输出日志：$OutLog"
Write-Host "错误日志：$ErrLog"
Write-Host "PID 文件：$PidFile"
Pop-Location
exit 0
