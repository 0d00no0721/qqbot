# V2 搜索接线（报告骨架 —— 边做边写盘）

- 任务：把已交付的 `scripts/web_search.py` / `scripts/search_gate.py` 接进回复链（不改后端）。
- 备份目录：`qq_native_re/patches/search_plumbing_backup_20261013-064101/`
  （decision_engine.py / model_primary.py / model_fallback.py / reverse_bot.py /
    config/group_features.json / config/search.json）
- 状态：进行中（骨架已建）

## 待填

1. 四个文件改动位置
2. 三条硬回归门结果
3. 逐字证明结果
4. 资料块格式 + 顺序断言
5. stdin 契约打桩
6. 端到端回复原文
7. 生效条件（下次重启）
