param(
    [Parameter(Mandatory = $true)][string]$YoloXDirectory,
    [Parameter(Mandatory = $true)][string]$DatasetDirectory,
    [string]$PythonExecutable = "python",
    [string]$OutputDirectory = "artifacts/yolox-training",
    [int]$Devices = 1,
    [int]$BatchSize = 16,
    [switch]$Fp16
)

$ErrorActionPreference = "Stop"
$expectedCommit = "6ddff4824372906469a7fae2dc3206c7aa4bbaee"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$yolox = (Resolve-Path $YoloXDirectory).Path
$dataset = (Resolve-Path $DatasetDirectory).Path
$output = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $workspace $OutputDirectory))
}
$experiment = Join-Path $workspace "training/yolox/dangjia_logo_nano.py"
$modelOutput = Join-Path $workspace "models/logos/dangjia/v1/dangjia_yolox_nano_v1.onnx"

$actualCommit = (& git -C $yolox rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $expectedCommit) {
    throw "YOLOX 必须固定在提交 $expectedCommit，当前为 $actualCommit"
}

& $PythonExecutable (Join-Path $workspace "scripts/validate_dangjia_dataset.py") $dataset
if ($LASTEXITCODE -ne 0) { throw "数据集校验失败" }

New-Item -ItemType Directory -Force -Path $output | Out-Null
$env:DANGJIA_DATASET_DIR = $dataset
$env:DANGJIA_OUTPUT_DIR = $output
$trainArguments = @(
    (Join-Path $yolox "tools/train.py"),
    "-f", $experiment,
    "-d", $Devices,
    "-b", $BatchSize
)
if ($Fp16) { $trainArguments += "--fp16" }

Push-Location $yolox
try {
    & $PythonExecutable @trainArguments
    if ($LASTEXITCODE -ne 0) { throw "YOLOX 训练失败" }
    $checkpoint = Join-Path $output "dangjia_logo_nano_v1/best_ckpt.pth"
    if (-not (Test-Path -LiteralPath $checkpoint -PathType Leaf)) {
        throw "未找到最佳权重：$checkpoint"
    }
    & $PythonExecutable (Join-Path $yolox "tools/export_onnx.py") `
        -f $experiment -c $checkpoint --output-name $modelOutput `
        --input images --output output --opset 11 --batch-size 1
    if ($LASTEXITCODE -ne 0) { throw "YOLOX ONNX 导出失败" }
}
finally {
    Pop-Location
}

$hash = (Get-FileHash -LiteralPath $modelOutput -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "ONNX: $modelOutput"
Write-Output "SHA256: $hash"
Write-Output "请先运行独立验证集验收；达到召回率 >=95%、精确率 >=98% 后再更新 manifest 状态和 SHA256。"
