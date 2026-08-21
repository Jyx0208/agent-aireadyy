param(
    [int]$Port = 8000,
    [string]$ListenHost = "0.0.0.0",
    [ValidateRange(1, 16)]
    [int]$WorkerCount = 4,
    [string]$DataRoot = (Join-Path $env:ProgramData "PRIDEAgent"),
    [string]$MsconvertExecutable = "",
    [switch]$SkipBuild,
    [switch]$ForceReinstall
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$frontend = Join-Path $repoRoot "frontend\benchmark-review"
$serviceRoot = Join-Path $repoRoot ".service"
$logRoot = Join-Path $DataRoot "logs"
$runsRoot = Join-Path $DataRoot "runs"
$operationsRoot = Join-Path $DataRoot "operations"
$cacheRoot = Join-Path $DataRoot "cache\pride"
$winsw = Join-Path $serviceRoot "winsw.exe"
$winswUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
$winswSha256 = "05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA"
$webXml = Join-Path $serviceRoot "pride-agent-web.xml"
$workerXml = Join-Path $serviceRoot "pride-agent-worker.xml"
$webServiceExe = Join-Path $serviceRoot "pride-agent-web.exe"
$workerServiceExe = Join-Path $serviceRoot "pride-agent-worker.exe"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell window."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project virtual environment is missing: $python"
}

foreach ($directory in @(
    $serviceRoot,
    $logRoot,
    $runsRoot,
    $operationsRoot,
    $cacheRoot,
    (Join-Path $DataRoot "config")
)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

if (-not $SkipBuild) {
    Set-Location -LiteralPath $repoRoot
    & $python -m pip install -e ".[web]"
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed."
    }
    Push-Location -LiteralPath $frontend
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }
        npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend production build failed."
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $winsw -PathType Leaf)) {
    Write-Host "Downloading WinSW v2.12.0 from the official release..."
    Invoke-WebRequest -Uri $winswUrl -OutFile $winsw
}
$actualWinswHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $winsw).Hash
if ($actualWinswHash -ne $winswSha256) {
    throw "WinSW checksum mismatch. Expected $winswSha256, got $actualWinswHash."
}
foreach ($serviceExecutable in @($webServiceExe, $workerServiceExe)) {
    if (
        -not (Test-Path -LiteralPath $serviceExecutable -PathType Leaf) -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $serviceExecutable).Hash -ne $winswSha256
    ) {
        Copy-Item -LiteralPath $winsw -Destination $serviceExecutable -Force
    }
}

function ConvertTo-XmlValue {
    param([string]$Value)
    return [Security.SecurityElement]::Escape($Value)
}

$pythonXml = ConvertTo-XmlValue $python
$repoXml = ConvertTo-XmlValue $repoRoot
$srcXml = ConvertTo-XmlValue (Join-Path $repoRoot "src")
$runsXml = ConvertTo-XmlValue $runsRoot
$operationsXml = ConvertTo-XmlValue $operationsRoot
$cacheXml = ConvertTo-XmlValue $cacheRoot
$configXml = ConvertTo-XmlValue (Join-Path $DataRoot "config\llm_config.json")
$logXml = ConvertTo-XmlValue $logRoot
$listenXml = ConvertTo-XmlValue $ListenHost
$msconvertEnvironment = ""
if ($MsconvertExecutable) {
    if (-not (Test-Path -LiteralPath $MsconvertExecutable -PathType Leaf)) {
        throw "Configured msconvert executable is missing: $MsconvertExecutable"
    }
    $msconvertXml = ConvertTo-XmlValue (
        [IO.Path]::GetFullPath($MsconvertExecutable)
    )
    $msconvertEnvironment = (
        '  <env name="AGENT_MSCONVERT_EXECUTABLE" value="{0}" />' -f
        $msconvertXml
    )
}

$commonEnvironment = @"
  <env name="PYTHONPATH" value="$srcXml" />
  <env name="AGENT_RUNS_DIR" value="$runsXml" />
  <env name="AGENT_OPERATIONS_DIR" value="$operationsXml" />
  <env name="AGENT_OPERATIONS_DB" value="$operationsXml\operations.sqlite" />
  <env name="AGENT_QUEUE_DB" value="$operationsXml\queue.sqlite" />
  <env name="AGENT_OPERATIONS_ARTIFACTS" value="$operationsXml\artifacts" />
  <env name="AGENT_PRIDE_CACHE_DIR" value="$cacheXml" />
  <env name="AGENT_LLM_CONFIG_PATH" value="$configXml" />
  <env name="AGENT_DISCOVERY_WORKERS" value="$WorkerCount" />
  <env name="AGENT_WEB_FULL_WORKFLOW_ENABLED" value="true" />
  <env name="DOCKER_HOST" value="npipe:////./pipe/dockerDesktopLinuxEngine" />
$msconvertEnvironment
"@

$webConfiguration = @"
<service>
  <id>PRIDEAgentWeb</id>
  <name>PRIDE Agent Web</name>
  <description>PRIDE proteomics operations API and Carbon workbench.</description>
  <executable>$pythonXml</executable>
  <arguments>-m uvicorn --app-dir &quot;$srcXml&quot; agent.web.app:app --host $listenXml --port $Port --workers 1</arguments>
  <workingdirectory>$repoXml</workingdirectory>
$commonEnvironment
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <stoptimeout>30sec</stoptimeout>
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <logpath>$logXml\web</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@

$workerConfiguration = @"
<service>
  <id>PRIDEAgentWorker</id>
  <name>PRIDE Agent Operations Worker</name>
  <description>Durable Huey worker for PRIDE discovery and review jobs.</description>
  <executable>$pythonXml</executable>
  <arguments>-m huey.bin.huey_consumer agent.operations.queue.huey -k thread -w $WorkerCount</arguments>
  <workingdirectory>$repoXml</workingdirectory>
$commonEnvironment
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <stoptimeout>60sec</stoptimeout>
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <logpath>$logXml\worker</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"@

[IO.File]::WriteAllText($webXml, $webConfiguration, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($workerXml, $workerConfiguration, [Text.UTF8Encoding]::new($false))

$serviceDefinitions = @(
    @{ Name = "PRIDEAgentWeb"; Executable = $webServiceExe },
    @{ Name = "PRIDEAgentWorker"; Executable = $workerServiceExe }
)
foreach ($definition in $serviceDefinitions) {
    $existing = Get-Service -Name $definition.Name -ErrorAction SilentlyContinue
    if ($existing) {
        if (-not $ForceReinstall) {
            throw "Service $($definition.Name) already exists. Re-run with -ForceReinstall during a maintenance window."
        }
        & $definition.Executable stop
        & $definition.Executable uninstall
    }
    & $definition.Executable install
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install service $($definition.Name)."
    }
}

& $webServiceExe start
& $workerServiceExe start
Start-Sleep -Seconds 3
& (Join-Path $PSScriptRoot "check-platform-health.ps1") -Port $Port

Write-Host "Windows services installed."
Write-Host "Workbench: http://127.0.0.1:$Port/benchmark-review"
Write-Host "Persistent data: $DataRoot"
