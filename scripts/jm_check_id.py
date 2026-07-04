"""
JMComic ID 有效性检查工具（QQbot 独立副本）
用法: python jm_check_id.py <数字ID>          # 检查本子
      python jm_check_id.py p<数字ID>          # 检查章节
      python jm_check_id.py <ID1> <ID2> ...    # 批量检查

退出码:
    0 - 至少一个 ID 有效
    1 - 用法错误
    2 - 所有 ID 均无效
    3 - 网络异常（代理不通、超时等）
"""

import sys
import os
from pathlib import Path

# jmcomic 配置文件路径
_OPTION_PATH = Path(__file__).parent / 'jm_option.yml'


def is_valid_id(id_str: str, option=None):
    """
    判断一个 ID 是否有效（能否下载）。

    参数:
        id_str: 纯数字本子ID (如 '123456') 或 p+数字章节ID (如 'p789012')
        option: 可选，已加载的 JmOption 对象，不传则自动从 option.yml 加载

    返回:
        ("valid", True)    = ID 有效
        ("invalid", False) = ID 无效（不存在）
        ("error", None)    = 网络异常
    """
    id_str = id_str.strip()

    # ── 本地快速校验 ─────────────────────────────────────
    is_photo = id_str.startswith('p')
    numeric_part = id_str[1:] if is_photo else id_str

    if not numeric_part.isdigit():
        return ("invalid", False)

    # ── 网络查询 ─────────────────────────────────────────
    from jmcomic import JmOption, MissingAlbumPhotoException

    if option is None:
        option = JmOption.from_file(str(_OPTION_PATH))

    try:
        client = option.build_jm_client()
        if is_photo:
            client.get_photo_detail(numeric_part)
        else:
            client.get_album_detail(id_str)
        return ("valid", True)
    except MissingAlbumPhotoException:
        return ("invalid", False)
    except Exception:
        # 网络错误（代理不通、超时）、重试耗尽等
        return ("error", None)


def main():
    if len(sys.argv) < 2:
        print("用法: python jm_check_id.py <ID1> [ID2 ...]")
        print("示例: python jm_check_id.py 123456")
        print("      python jm_check_id.py p789012")
        print("      python jm_check_id.py 123456 789012 p345678")
        sys.exit(1)

    ids = sys.argv[1:]

    # 延迟导入，避免校验时浪费时间
    from jmcomic import JmOption

    print(f"📂 使用配置: {_OPTION_PATH}")
    option = JmOption.from_file(str(_OPTION_PATH))

    print(f"🔍 正在检查 {len(ids)} 个 ID ...\n")

    valid_count = 0
    has_error = False
    for raw_id in ids:
        status, result = is_valid_id(raw_id, option)
        if status == "error":
            has_error = True
            mark = "⚠️ "
            status_text = "网络异常"
        elif result:
            mark = "✅"
            status_text = "有效"
            valid_count += 1
        else:
            mark = "❌"
            status_text = "无效"

        print(f"  {mark} {raw_id} → {status_text}")

    print(f"\n📊 结果: {valid_count}/{len(ids)} 个有效")

    # 退出码：有网络异常→3，全无效→2，有有效的→0
    if has_error:
        sys.exit(3)
    if valid_count == 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == '__main__':
    main()