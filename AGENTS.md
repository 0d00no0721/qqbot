# AGENTS.md

High-signal notes for OpenCode sessions. Trust the code over `CLAUDE.md` — it is stale (describes pre-v2.3.2 structure).

## Run

```powershell
# Main bot (WS server :8080, NapCat connects in). venv + UTF-8 flag are mandatory.
cd E:\QQbot && $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" reverse_bot.py

# Verify a change didn't break imports (no test runner — this is the main check):
cd E:\QQbot && $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" -c "import reverse_bot; print('OK')"
```

NapCat must already be running (`E:\QQbot\NapCat\NapCat.44498.Shell\NapCatWinBootMain.exe`). Windows-only deployment; paths use `\` and `.exe`.

## Architecture (post v2.3.2 refactor)

`scripts/command_handler.py` is now a **56-line facade** — not the 2160-line monolith `CLAUDE.md` describes. All 指令 logic lives in `scripts/commands/`:

- `_state.py` — shared globals, locks, constants, `init_handlers()`. Sub-modules access via `from . import _state` (attribute access, so `init_handlers` reassignment is visible).
- `_registry.py` — `@register(name, match_type, pattern)` decorator + `dispatch(ctx)`. Match types: `exact` / `prefix` (requires trailing space) / `substring` / `regex` (handler gets match obj) / `custom` (handler gets pattern's return value).
- 12 feature modules (cipher, daily, menu, yau, zhouli, pixelart, images, divination, piano, character, system, help) + `voice.py` + `_helpers.py`.
- `commands/__init__.py` imports all modules to trigger registration. Import order = dispatch priority for same-`priority` specs.

`reverse_bot.py` imports public symbols from `command_handler.py` (the facade re-exports them). **Do not break these re-exports**: `handle_command`, `process_voice_message`, `_mystery_queue`, `_dedup_lock`, `BOT_QQ`, `VERSION`, `init_handlers`, `processed_msg_ids`, `PROCESSED_MSG_MAX`, `known_groups`, `find_valid_number`.

Adding a command: create/edit a module in `commands/`, decorate with `@register`, ensure the module is imported in `commands/__init__.py`. No need to touch the facade or any dispatch chain.

## Gotchas

- **`VS_ENABLED` is hardcoded in two files** (`reverse_bot.py` and `scripts/command_handler.py`). To enable the virtual stock module, flip both to `True` and restart. They must stay in sync or imports break. Currently `False`.
- **`group_features.json` uses whitelist semantics**: unconfigured groups get *all features OFF* (not on). `_feature_on()` returns False for missing groups.
- **Subprocess model scripts** (`model_primary.py`, `model_fallback.py`) do **not** call `load_dotenv()` — they inherit the parent process env. Running them directly for debugging will get empty API keys.
- **`archive/` is gitignored.** Moving a file there = deleting it from git's view. Do not `git add` from inside `archive/`.
- **`*.backup` files are gitignored** but `*.backup_refactor` is not — clean up temp backups manually.
- **No pytest.** `scripts/test_*.py` are exploratory/debug scripts, not a suite. The real test files (`test_virtual_stock.py` etc.) were archived. Verify changes by import self-check and targeted dispatch tests.

## API Keys (`.env`)

Loaded once by `load_dotenv()` in `reverse_bot.py`. Three keys:

| Env var | Endpoint | Used by |
|---------|----------|---------|
| `DECISION_API_KEY` | `api.llm.ustc.edu.cn/v1` (中科大代理, free) | decision engine, 周礼 primary, user_profile primary |
| `DEEPSEEK_API_KEY` | `api.deepseek.com/v1` (paid) | Yau, 算卦, 记忆分类, fallback model, 周礼 fallback |
| `DIVINATION_API_KEY` | `api.deepseek.com/v1` | 算卦 (separate quota) |

`.env` is gitignored; see `.env.example` for template.

## Config files (`config/`)

| File | Written by | Read by |
|------|-----------|---------|
| `group_features.json` | `scripts/group_manager.py` (CLI) | `reverse_bot.py` (runtime, mtime-cached) |
| `group_characters.json` | `decision_engine` assign/unassign | `decision_engine` |
| `active_character.json` | `decision_engine`, `memory_manager.py` | `decision_engine`, `command_handler` |
| `active_model.json` | `decision_engine.set_active_model` | `decision_engine` (only `model` field; URL/key hardcoded to USTC proxy) |
| `decision_rules.json` | manual edit | `decision_engine._load_config` |
| `characters/*/persona.txt` | `memory_manager.py`, `@bot 设置人设` | `decision_engine._load_character` |

## Versioning

`VERSION` lives in `scripts/commands/_state.py` (was in `command_handler.py` pre-v2.3.2). Format: `主版本.次版本.修订号`. Current: `2.3.2`. Git tags follow `vX.Y.Z`.
