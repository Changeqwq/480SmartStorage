param(
    [string]$Code,
    [string]$BaseUrl = "http://127.0.0.1:5000"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $repositoryRoot ".env"
$lockerKey = $env:LOCKER_API_KEY

if ([string]::IsNullOrWhiteSpace($lockerKey) -and (Test-Path -LiteralPath $environmentPath)) {
    $keyLine = Get-Content -LiteralPath $environmentPath |
        Where-Object { $_ -match '^LOCKER_API_KEY=(.*)$' } |
        Select-Object -Last 1
    if ($keyLine -match '^LOCKER_API_KEY=(.*)$') {
        $lockerKey = $Matches[1].Trim()
    }
}

if ([string]::IsNullOrWhiteSpace($lockerKey)) {
    Write-Host "Locker API is not configured." -ForegroundColor Red
    Write-Host "Add LOCKER_API_KEY to .env, then restart app.py."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Code)) {
    $Code = Read-Host "Enter the exact code printed below the pickup or return QR"
}

$Code = $Code.Trim().ToUpperInvariant()
if ($Code -notmatch '^[PR]-\d+-\d+$') {
    Write-Host "Invalid code. Use P-ITEM_ID-USER_ID or R-ITEM_ID-USER_ID." -ForegroundColor Red
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
