# sync_to_github.ps1 — QQbot 公开仓库同步脚本
# ============================================================
# 由 DSH 会话生成。因为 .git 目录 ACL 拒绝写入 + 出网代理未运行，
# git 操作必须由你在【管理员 PowerShell 或普通控制台】执行。
#
# 用法（管理员 PowerShell）：
#   cd E:\QQbot
#   powershell -ExecutionPolicy Bypass -File .\sync_to_github.ps1
#
# 本脚本不会修改任何源码或运行时数据文件；只做：
#   重命名 1 个逆向工程文档（保留在本地） + 更新 .gitignore/.gitattributes
#   + 把运行时数据移出 git 跟踪（本地文件保留） + 4 个提交 + 推送 + 打 tag
# ============================================================

$ErrorActionPreference = 'Continue'
$root = 'E:\QQbot'
Set-Location $root

function Step($n, $msg) { Write-Host ""; Write-Host "=========== [$n] $msg ===========" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [警告] $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  [中止] $m" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------
Step '0/8' '环境前置检查'
# ------------------------------------------------------------
$canWrite = $false
try { [System.IO.File]::WriteAllText("$root\.git\_wtest.tmp", 'x'); $canWrite = $true; Remove-Item "$root\.git\_wtest.tmp" -Force }
catch { $canWrite = $false }
if (-not $canWrite) { Die ".git 不可写。请用【以管理员身份运行】的 PowerShell 重开本脚本（.git 的 ACL 只对 Administrators 放行写入）。" }
Ok ".git 可写"

$proxy = Get-NetTCPConnection -State Listen -LocalPort 7890 -ErrorAction SilentlyContinue
if (-not $proxy) {
    Warn "127.0.0.1:7890 没有监听 —— git config 里配了 http.proxy=http://127.0.0.1:7890，推送会失败。"
    Warn "请先启动你的代理软件；若不需要代理，可在推送前执行： git config --unset http.proxy && git config --unset https.proxy"
    $ans = Read-Host "  仍要继续（提交可以完成，推送会失败）？输入 y 继续，其它键中止"
    if ($ans -ne 'y') { Die '已中止，请先启动代理。' }
}
else { Ok "代理端口 7890 已监听，可推送" }

foreach ($f in @('README.md', '.gitignore', '.gitattributes', 'qq_native_re\README.md')) {
    if (-not (Test-Path $f)) { Die "缺少待提交文件：$f（请确认 DSH 已生成）" }
}
Ok "待提交的 4 个文件齐备"

# ------------------------------------------------------------
Step '1/8' '建立备份分支（可随时回退）'
# ------------------------------------------------------------
git branch -f backup_before_public_sync HEAD
if ($LASTEXITCODE -ne 0) { Die '创建备份分支失败' }
Ok "backup_before_public_sync -> $(git rev-parse --short HEAD)"
Write-Host "  回退命令：git reset --hard backup_before_public_sync"

# ------------------------------------------------------------
Step '2/8' '处理逆向工程材料（保留在本地，排除出仓库）'
# ------------------------------------------------------------
if (Test-Path 'qq_native_re\README.md') {
    if (-not (Test-Path 'qq_native_re\PROJECT_CONTEXT.md')) {
        git mv 'qq_native_re/README.md' 'qq_native_re/PROJECT_CONTEXT.md' 2>$null
        if ($LASTEXITCODE -ne 0) { Rename-Item 'qq_native_re\README.md' 'qq_native_re\PROJECT_CONTEXT.md' }
        Ok "qq_native_re/README.md -> qq_native_re/PROJECT_CONTEXT.md（已 gitignore，本地保留）"
    } else { Warn 'PROJECT_CONTEXT.md 已存在，跳过重命名' }
}
# 确认 samples（第三方二进制/反汇编）被忽略
git check-ignore -q 'qq_native_re/samples/track_B/QQ_official.exe'
if ($LASTEXITCODE -eq 0) { Ok 'qq_native_re/samples/ 已忽略（含腾讯 QQ 二进制与反汇编，不入公开仓库）' }
else { Warn 'samples/ 未被忽略，请检查 .gitignore' }
# 确认逆向工程材料（注入分析/绕过实验/第三方二进制）全部被忽略
$reExcluded = @(
  'qq_native_re/prompts/track_B_injection_internals.md',
  'qq_native_re/prompts/track_E_bypass_experiment.md',
  'qq_native_re/findings/track_B/REPORT.md',
  'qq_native_re/findings/track_E/REPORT.md',
  'qq_native_re/decisions/001_initial_strategy.md'
)
$notIgnored = @()
foreach ($r in $reExcluded) {
    git check-ignore -q $r
    if ($LASTEXITCODE -ne 0) { $notIgnored += $r }
}
if ($notIgnored.Count -eq 0) { Ok '逆向工程材料（prompts/track 报告/早期决策）已全部忽略' }
else { Die "以下逆向工程材料未被忽略，禁止推送：`n$($notIgnored -join "`n")" }

# ------------------------------------------------------------
Step '3/8' '把运行时数据移出 git 跟踪（本地文件保留）'
# ------------------------------------------------------------
foreach ($f in @('menu_data.json')) {
    git ls-files --error-unmatch $f *> $null
    if ($LASTEXITCODE -eq 0) {
        git rm --cached --quiet $f
        Ok "$f 已移出跟踪（本地文件完好保留）"
    } else { Warn "$f 本来就不在跟踪中，跳过" }
}

# 安全兜底：确认两个含真实密钥的 .env 备份不被跟踪
$envBackups = @(
  'qq_native_re/patches/model_migration_backup_20261012-074515/.env',
  'qq_native_re/patches/model_migration_backup_20261012-074556/.env.post_append'
)
foreach ($e in $envBackups) {
    git check-ignore -q $e
    if ($LASTEXITCODE -eq 0) { Ok "已忽略（含真实密钥）：$e" } else { Die "未忽略！$e —— 停下来检查 .gitignore" }
}

# ------------------------------------------------------------
Step '4/8' '提交 1/4：仓库卫生'
# ------------------------------------------------------------
# 按 .gitattributes 归一化行尾（只影响换行符，不改内容）
git add --renormalize . 2>$null
git add .gitignore .gitattributes
git add -u
git commit -q -m "chore(repo): public-repo hygiene - gitignore rewrite + untrack runtime data" `
  -m "menu_data.json moved out of tracking (real group/member QQ ids); runtime state, log streams and engineering raw artifacts excluded; .gitattributes normalizes line endings to LF."
if ($LASTEXITCODE -ne 0) { Die '提交 1 失败' }
Ok '已提交'

# ------------------------------------------------------------
Step '5/8' '提交 2/4：中央模型池'
# ------------------------------------------------------------
$pool = @(
  'scripts/llm_pool.py','scripts/test_llm_pool.py','scripts/test_pool_harden.py',
  'config/model_pool.json',
  'scripts/decision_engine.py','scripts/model_primary.py','scripts/model_fallback.py',
  'scripts/user_profile.py','scripts/memory_manager.py',
  'scripts/commands/_state.py','scripts/commands/_helpers.py','scripts/commands/zhouli.py',
  'scripts/commands/divination.py','scripts/commands/system.py'
)
git add -- $pool 2>$null
git commit -q -m "feat(model-pool): central LLM pool + local-gateway migration" `
  -m "New single model egress (scripts/llm_pool.py + config/model_pool.json): 5-tier priority failover, 4-way error triage (auth stops the pool / request errors never cool down / model_missing 600s / transient 60s), 20s-per-attempt + 45s total budget, max_tokens floor 512, sk-* redaction and JSONL audit. All 9 call sites rewired; no cloud fallback kept."
if ($LASTEXITCODE -ne 0) { Die '提交 2 失败' }
Ok '已提交'

# ------------------------------------------------------------
Step '6/8' '提交 3/4：角色、视觉模块、移除虚拟股市、工程档案'
# ------------------------------------------------------------
git add -A -- scripts/commands config/characters
git add -A -- scripts/virtual_stock standalone_bot.py test_vs.py test_virtual_stock.py test_integration.py 2>$null
git add -A -- '虚拟股.md' '虚拟股.pdf' 2>$null
foreach ($f in @('scripts/vision.py','scripts/image_utils.py','scripts/image_cache.py','qq_native_re')) {
    if (Test-Path $f) { git add -f -- $f }
}
git add -A -- '大肥鱼-persona.md' '贴吧鱼-persona.md' '技术方案对比_GitHub竞品.md' 'memory_manager_guide.md' 'multi_group_character_plan.md' 2>$null
git commit -q -m "feat(character,vision): character updates, vision modules; remove virtual stock" `
  -m "Adds scripts/{vision,image_utils,image_cache}.py and the two new character profiles; removes the virtual stock module, standalone_bot.py and its exploratory tests (kept in git history only). Includes the qq_native_re evidence archive (reports and decision logs only; raw artifacts, backups and reverse-engineering material stay local)."
if ($LASTEXITCODE -ne 0) { Die '提交 3 失败' }
Ok '已提交'

# ------------------------------------------------------------
Step '7/8' '提交 4/4：文档'
# ------------------------------------------------------------
git add README.md
git add -f -- qq_native_re/README.md
git add -- AGENTS.md PROJECT_INDEX.md 2>$null
$extraDocs = git status --porcelain | Where-Object { $_ -match '^\?\?.*\.md$' } | ForEach-Object { $_.Substring(3).Trim('"') }
if ($extraDocs) { git add -- $extraDocs }
git commit -q -m "docs: add README; correct stale model-gateway and version facts" `
  -m "New root README (positioning, architecture, three-layer decision funnel, model pool error triage, quick start, config reference, command list, known limits) and qq_native_re/README.md explaining the archive policy. AGENTS.md and PROJECT_INDEX.md corrected: single local-gateway egress, model_pool.json added, facade size, character/version state, removed virtual stock."
if ($LASTEXITCODE -ne 0) { Die '提交 4 失败' }
Ok '已提交'

# ------------------------------------------------------------
Step '8/8' '推送前验证 + 推送 + 打 tag'
# ------------------------------------------------------------
Write-Host "`n--- 必须为空的检查（跟踪文件中的敏感项）---"
$leak = git ls-files | Select-String -Pattern '^\.env$|^user_profiles\.json$|^menu_data\.json$|^scripts/sign_in_data\.json$|\.env\.post_append$|^qq_native_re/patches/|^qq_native_re/samples/|^qq_native_re/prompts/|^qq_native_re/WORKFLOW\.md$|^qq_native_re/PROJECT_CONTEXT\.md$|^qq_native_re/findings/track_[A-G]/'
if ($leak) { Die "仍被跟踪的敏感文件：`n$leak" }
Ok '无敏感文件被跟踪'

Write-Host "`n--- 关键文件必须在仓库中 ---"
foreach ($k in @('scripts/llm_pool.py','config/model_pool.json','README.md')) {
    git ls-files --error-unmatch $k *> $null
    if ($LASTEXITCODE -eq 0) { Ok $k } else { Warn "缺失：$k" }
}

Write-Host "`n--- 待推送提交 ---"
git log --oneline origin/main..HEAD

Write-Host "`n--- 导入自检 ---"
$env:PYTHONIOENCODING = 'utf-8'
& "$root\venv\Scripts\python.exe" -c "import reverse_bot; print('IMPORT OK, VERSION=', reverse_bot.VERSION)" 2>&1 | Select-String 'IMPORT OK|Error|Traceback'

$ans = Read-Host "`n确认推送到 https://github.com/0d00no0721/qqbot.git ？输入 y 推送"
if ($ans -ne 'y') { Warn '已跳过推送。提交已在本地完成，随时可执行 git push origin main'; exit 0 }

git push origin main
if ($LASTEXITCODE -ne 0) { Die '推送失败 —— 常见原因：代理未启动 / 凭据失效（git config credential.helper=manager 会弹窗）' }
Ok 'main 已推送'

git tag -f v2.4.0
$remoteTag = git ls-remote --tags origin v2.4.0 2>$null
if ($remoteTag) { git push -f origin v2.4.0 } else { git push origin v2.4.0 }
if ($LASTEXITCODE -eq 0) { Ok 'tag v2.4.0 已推送（指向本次文档提交）' } else { Warn 'tag 推送失败（不影响代码同步）' }

Write-Host "`n--- 本次同步产出的文件清单核对 ---"
Write-Host "  仓库内文件总数: $((git ls-files | Measure-Object).Count)"
Write-Host "  已忽略的敏感/大体积路径抽查:"
foreach ($p in @('.env','user_profiles.json','menu_data.json','logs/','NapCat/','venv/','qq_native_re/patches/','qq_native_re/samples/')) {
    git check-ignore -q $p
    if ($LASTEXITCODE -eq 0) { Write-Host "    [已忽略] $p" -ForegroundColor DarkGray }
    else { Write-Host "    [!! 未忽略] $p" -ForegroundColor Red }
}

Write-Host "`n================= 完成 ================= " -ForegroundColor Green
git log --oneline -6
Write-Host "`n远程与本地是否一致："
git rev-list --left-right --count main...origin/main
Write-Host "（左=本地领先, 右=远程领先；应为 0  0）"
Write-Host "`n查看仓库首页：gh repo view 0d00no0721/qqbot --web"
