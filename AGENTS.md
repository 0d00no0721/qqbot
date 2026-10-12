# AGENTS.md

High-signal notes for OpenCode sessions.

## Run

```powershell
# Main bot (WS server :8080, NapCat connects in). venv + UTF-8 flag are mandatory.
cd E:\QQbot && $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" reverse_bot.py

# Verify a change didn't break imports (no test runner — this is the main check):
cd E:\QQbot && $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" -c "import reverse_bot; print('OK')"
```

NapCat must already be running (`E:\QQbot\NapCat\NapCat.44498.Shell\NapCatWinBootMain.exe`). Windows-only deployment; paths use `\` and `.exe`.

## Architecture (post v2.3.2 refactor, current release v2.4.0)

`scripts/command_handler.py` is now a **~72-line facade** — all 指令 logic lives in `scripts/commands/`:

- `_state.py` — shared globals, locks, constants, `init_handlers()`. Sub-modules access via `from . import _state` (attribute access, so `init_handlers` reassignment is visible).
- `_registry.py` — `@register(name, match_type, pattern)` decorator + `dispatch(ctx)`. Match types: `exact` / `prefix` (requires trailing space) / `substring` / `regex` (handler gets match obj) / `custom` (handler gets pattern's return value).
- 14 feature modules (cipher, daily, menu, yau, zhouli, pixelart, images, divination, piano, character, system, help, plus `voice.py` and `_helpers.py`).
- `commands/__init__.py` imports all modules to trigger registration. Import order = dispatch priority for same-`priority` specs.

Commands live in `scripts/commands/{cipher,daily,menu,yau,zhouli,pixelart,images,divination,piano,character,system,help}.py` plus `voice.py` (voice→text via NapCat HTTP) and `_helpers.py` (`call_deepseek`, now routed to the pool). Total registered commands: **33**.

`reverse_bot.py` imports public symbols from `command_handler.py` (the facade re-exports them). **Do not break these re-exports**: `handle_command`, `process_voice_message`, `_mystery_queue`, `_dedup_lock`, `BOT_QQ`, `VERSION`, `init_handlers`, `processed_msg_ids`, `PROCESSED_MSG_MAX`, `known_groups`, `find_valid_number`.

Adding a command: create/edit a module in `commands/`, decorate with `@register`, ensure the module is imported in `commands/__init__.py`. No need to touch the facade or any dispatch chain.

## Gotchas

- **`VS_ENABLED` flag is a trap.** `scripts/command_handler.py` still has `VS_ENABLED = False` guarding a `from scripts.virtual_stock import ...` block, but `scripts/virtual_stock/` was removed from the working copy. Flipping it to `True` will `ImportError`. The virtual stock module lives only in git history (`git show HEAD:scripts/virtual_stock/`).
- **`group_features.json` uses whitelist semantics**: unconfigured groups get *all features OFF* (not on). `_feature_on()` returns False for missing groups.
- **Subprocess model scripts** (`model_primary.py`, `model_fallback.py`) do **not** call `load_dotenv()` — they inherit the parent process env. Running them directly for debugging will get empty API keys.
- **`archive/` is gitignored.** Moving a file there = deleting it from git's view. Do not `git add` from inside `archive/`.
- **`*.backup` files are gitignored** but `*.backup_refactor` is not — clean up temp backups manually.
- **No pytest.** `scripts/test_*.py` are exploratory/debug scripts, not a suite. The real test files (`test_virtual_stock.py` etc.) were archived. Verify changes by import self-check and targeted dispatch tests.

## API Keys (`.env`)

Loaded once by `load_dotenv()` in `reverse_bot.py`; `llm_pool.py` also loads it with `override=False`.

**The single model egress is `scripts/llm_pool.py` → local gateway `http://127.0.0.1:8090/v1`.** No cloud fallback is kept (deliberate decision — see `qq_native_re/decisions/007_model_gateway_migration.md`).

| Env var | Used by |
|---------|---------|
| `LLM_POOL_BASE_URL` | gateway endpoint (default `http://127.0.0.1:8090/v1`) |
| `LLM_POOL_API_KEY` | gateway key — every call site ultimately resolves to this |
| `DECISION_API_KEY` / `DEEPSEEK_API_KEY` / `DIVINATION_API_KEY` | legacy cloud keys, **kept but unused**, as rollback anchors |

`.env` is gitignored; see `.env.example` for template. Never put a real key in a tracked file.

## Config files (`config/`)

| File | Written by | Read by |
|------|-----------|---------|
| `group_features.json` | `scripts/group_manager.py` (CLI) | `reverse_bot.py` (runtime, mtime-cached) |
| `group_characters.json` | `decision_engine` assign/unassign | `decision_engine` |
| `active_character.json` | `decision_engine`, `memory_manager.py` | `decision_engine`, `command_handler` |
| `active_model.json` | `decision_engine.set_active_model` | `llm_pool` + `decision_engine` — **only the `model` field**; URL/key always come from the pool config |
| `model_pool.json` | manual edit (mtime hot-reload) | `llm_pool._load_config` — priority tiers, timeouts, cooldowns, `min_max_tokens` |
| `decision_rules.json` | manual edit | `decision_engine._load_config` |
| `characters/*/persona.txt` | `memory_manager.py`, `@bot 设置人设` | `decision_engine._load_character` |

## Versioning

`VERSION` lives in `scripts/commands/_state.py` (was in `command_handler.py` pre-v2.3.2). Format: `主版本.次版本.修订号`. Current: `2.4.0`. Git tags follow `vX.Y.Z`.

## Git / pushing from this repo

`.git` is **not writable by non-elevated processes** on this machine (its ACL grants write only to `Administrators`), so `git add/commit/push` require an elevated console.

The machine's proxy on `127.0.0.1:7890` is usually not running; `.gitconfig` still points at it, so any network git command fails. Two working options:

```powershell
# A) use the proxy that is actually listening (:15715), with the OpenSSL TLS backend
#    (the schannel backend needs Windows credential-store access that sandboxed
#     processes do not have -> SEC_E_NO_CREDENTIALS)
git -c http.sslBackend=openssl -c http.proxy=http://127.0.0.1:15715 -c https.proxy=http://127.0.0.1:15715 ls-remote --heads origin

# B) clear the dead proxy permanently, then push normally
git config --unset http.proxy ; git config --unset https.proxy
```

`sync_to_github.ps1` (repo root, untracked local helper) performs the whole sync: hygiene commit, model-pool commit, character/vision commit, docs commit, smoke check, push and `v2.4.0` tag.
