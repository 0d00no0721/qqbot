# W1 实弹测试：四段 live 调用（带 target / 不带 target × 主/备脚本）
# 用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; pwsh -File qq_native_re\findings\reply_fix\w1_live_runs.ps1
# 说明：model_primary.py / model_fallback.py 不调用 load_dotenv()，靠父进程环境继承。
#       机器人进程由 reverse_bot.py 的 load_dotenv() 注入，这里为复现同等条件，手动注入 .env。
#       密钥只注入子进程环境，绝不打印。

$ErrorActionPreference = "Stop"
$root = "E:\QQbot"
Set-Location $root

# ---- 注入 .env（不回显） ----
$envCount = 0
foreach ($line in Get-Content "$root\.env" -Encoding utf8) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    if ($t -notmatch "=") { continue }
    $i = $t.IndexOf("=")
    $k = $t.Substring(0, $i).Trim()
    $v = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    if ($k) { [System.Environment]::SetEnvironmentVariable($k, $v, "Process"); $envCount++ }
}
Write-Output "已注入 .env 变量 $envCount 项（值不打印）"
Write-Output ""

# ---- 故障现场还原的上下文（取自 10:25-10:26 真实群聊） ----
$ctx = @"
[UC]: 软的翻出去
[橘又青]: 我发现我关注的有点儿少了，有没有人能推荐点
[UC]: 硬的翻回来
[ㅤ]: 摇一摇开屏真是本世纪最阴间的发明，打个卡差点喜提三万，这波是广告商给你强制理财了嗝
[多喝热水]: 后台可以关允许摇晃感应
[爱莉不知道哦]: 怎么又一个哈耶克啊
"@ -replace "`r`n", "\n"

$persona = "你是群聊机器人，请用简短、口语化的中文回复。"

function Run-Case {
    param([string]$Title, [string]$Script, [string]$Json)
    Write-Output "==================== $Title ===================="
    Write-Output "脚本: $Script"
    Write-Output "--- stdin ---"
    Write-Output $Json
    Write-Output "--- stdout ---"
    $out = $Json | & ".\venv\Scripts\python" $Script 2> "$env:TEMP\w1_err.txt"
    $out | ForEach-Object { Write-Output $_ }
    Write-Output "--- stderr ---"
    if (Test-Path "$env:TEMP\w1_err.txt") { Get-Content "$env:TEMP\w1_err.txt" -Encoding utf8 | ForEach-Object { Write-Output $_ } }
    Write-Output ""
}

# ---- 用例 1：有 target（复现故障现场：要回应的是「今日深圳天气」） ----
$jsonWithTarget = '{"context":"' + $ctx + '","persona":"' + $persona + '","max_tokens":80,"target":{"nickname":"UC","uid":"10011","content":"今日深圳天气"}}'
Run-Case -Title "用例1 有 target / 主脚本 model_primary.py" -Script "scripts\model_primary.py" -Json $jsonWithTarget

# ---- 用例 2：无 target（基线，必须与旧版同构） ----
$jsonNoTarget = '{"context":"' + $ctx + '","persona":"' + $persona + '","max_tokens":80}'
Run-Case -Title "用例2 无 target / 主脚本 model_primary.py（基线）" -Script "scripts\model_primary.py" -Json $jsonNoTarget

# ---- 用例 3：有 target / 备脚本 ----
Run-Case -Title "用例3 有 target / 备脚本 model_fallback.py" -Script "scripts\model_fallback.py" -Json $jsonWithTarget

# ---- 用例 4：无 target / 备脚本 ----
Run-Case -Title "用例4 无 target / 备脚本 model_fallback.py（基线）" -Script "scripts\model_fallback.py" -Json $jsonNoTarget

# ---- 用例 5：有 target + avoid_recent（防复读注入，验证不报错且换说法） ----
$jsonAvoid = '{"context":"' + $ctx + '","persona":"' + $persona + '","max_tokens":80,"target":{"nickname":"UC","uid":"10011","content":"今日深圳天气"},"avoid_recent":["（尾巴一甩）摇一摇广告这玩意儿是真缺德，打个卡都能给你摇出三万块贷款，本鱼建议直接后台把传感器权限喂鱼算了","摇一摇广告就是当代赛博碰瓷，手指一动三万块差点进账"]}'
Run-Case -Title "用例5 有 target + avoid_recent / 主脚本" -Script "scripts\model_primary.py" -Json $jsonAvoid
