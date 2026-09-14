[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$secretsDirectory = Join-Path $projectRoot "secrets"
New-Item -ItemType Directory -Force -Path $secretsDirectory | Out-Null

function New-RandomSecretFile {
    param([Parameter(Mandatory)][string]$Path)

    if (Test-Path -LiteralPath $Path) {
        Write-Host "Keeping existing secret file: $Path"
        return
    }

    $bytes = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $value = [Convert]::ToBase64String($bytes)
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($Path, $value, $encoding)
    Write-Host "Created secret file: $Path"
}

New-RandomSecretFile -Path (Join-Path $secretsDirectory "postgres_password.txt")
New-RandomSecretFile -Path (Join-Path $secretsDirectory "master_password.txt")

Write-Host "Secrets are ready. Their contents were not displayed."
