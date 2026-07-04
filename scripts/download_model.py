"""
模型预下载脚本

在网络通畅的环境下运行此脚本，将 sentence-transformers 模型下载到本地缓存。
之后机器人在无网络环境中也能加载模型。

用法:
    python scripts/download_model.py
    python scripts/download_model.py --model paraphrase-MiniLM-L3-v2
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import set_env

from sentence_transformers import SentenceTransformer

_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_FALLBACK_MODEL = "paraphrase-MiniLM-L3-v2"


def download(model_name: str):
    print(f"[下载] 开始下载模型 {model_name} ...")
    try:
        model = SentenceTransformer(model_name)
        _ = model.encode(["测试消息"])
        print(f"[下载] 模型 {model_name} 下载完成！")
    except Exception as e:
        print(f"[下载] 失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    model = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--model" else _DEFAULT_MODEL
    download(model)

    if model == _DEFAULT_MODEL:
        print(f"[下载] 同时下载备用模型 {_FALLBACK_MODEL} ...")
        download(_FALLBACK_MODEL)

    print("\n[下载] 全部完成。现在可以断开网络运行机器人了。")
