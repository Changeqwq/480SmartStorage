param(
    [string]$Code,
    [string]$BaseUrl = "http://127.0.0.1:5000"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $repositoryRoot ".env"
$generatedKeyPath = Join-Path $repositoryRoot "instance\locker.key"
$lockerKey = $env:LOCKER_API_KEY

if ([string]::IsNullOrWhiteSpace($lockerKey) -and (Test-Path -LiteralPath $environmentPath)) {
    $keyLine = Get-Content -LiteralPath $environmentPath |
        Where-Object { $_ -match '^LOCKER_API_KEY=(.*)$' } |
        Select-Object -Last 1
    if ($keyLine -match '^LOCKER_API_KEY=(.*)$') {
        $lockerKey = $Matches[1].Trim()
    }
}

if ([string]::IsNullOrWhiteSpace($lockerKey) -and (Test-Path -LiteralPath $generatedKeyPath)) {
    $lockerKey = (Get-Content -LiteralPath $generatedKeyPath -Raw).Trim()
}

if ([string]::IsNullOrWhiteSpace($lockerKey)) {
    Write-Host "Local locker key has not been created yet." -ForegroundColor Red
    Write-Host "Start app.py once, then run this simulator again."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Code)) {
    $Code = Read-Host "Enter the exact code printed below the pickup or return QR"
}

$Code = $Code.Trim().ToUpperInvariant()
if ($Code -notmatch '^(D-\d+-\d+-\d+|[PR]-\d+-\d+)$') {
    Write-Host "Invalid code. Use D-ITEM_ID-STUDENT_ID-TA_ID, P-ITEM_ID-USER_ID, or R-ITEM_ID-USER_ID." -ForegroundColor Red
    exit 1
}

$headers = @{ "X-Locker-Key" = $lockerKey }
$body = @{ code = $Code } | ConvertTo-Json

try {
    $result = Invoke-RestMethod `
        -Uri "$($BaseUrl.TrimEnd('/'))/api/locker/scan" `
        -Method Post `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $body

    Write-Host "Scan accepted." -ForegroundColor Green
    Write-Host "Loan ID: $($result.loan_id)"
    Write-Host "New status: $($result.status)"
    Write-Host "Refresh the loan page to continue the workflow."
}
catch {
    $message = $_.ErrorDetails.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_.Exception.Message
    }
    Write-Host "Locker scan failed: $message" -ForegroundColor Red
    exit 1
}
