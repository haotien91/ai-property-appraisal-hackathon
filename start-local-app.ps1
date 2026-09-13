param([int]$Port = 8124)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '地價智審_AI_Offline_Candidate_v1/start-app.ps1') -Port $Port
