"""
基于 sentence-transformers 的中文零样本分类器
使用 paraphrase-multilingual-MiniLM-L12-v2 多语言模型

功能：
  1) classify(text, candidate_labels=None) -> (label, score)
  2) 命令行交互模式：反复输入消息，实时分类
"""

import sys
import os
import json
import time

# 确保能导入上级目录的 set_env.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import set_env  # 设置 HF_HOME / TRANSFORMERS_CACHE

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# ---------- 代理配置（从环境变量读取） ----------
_HTTP_PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
_HTTPS_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""

if _HTTP_PROXY or _HTTPS_PROXY:
    os.environ.setdefault("HTTP_PROXY", _HTTP_PROXY)
    os.environ.setdefault("HTTPS_PROXY", _HTTPS_PROXY or _HTTP_PROXY)
    print(f"[代理] 检测到 HTTP_PROXY={_HTTP_PROXY}", file=sys.stderr)
    if _HTTPS_PROXY:
        print(f"[代理] 检测到 HTTPS_PROXY={_HTTPS_PROXY}", file=sys.stderr)

# ---------- 默认配置 ----------
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "labels.json",
)

_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_FALLBACK_MODEL = "paraphrase-MiniLM-L3-v2"


def load_labels(config_path=None):
    """从 JSON 配置文件读取候选标签列表"""
    path = config_path or _CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        labels = data.get("labels", [])
        if not labels:
            print(f"[警告] 配置文件 {path} 中 labels 为空", file=sys.stderr)
            labels = ["日常生活", "严肃政治", "战争雷霆"]
        return labels
    except FileNotFoundError:
        print(f"[警告] 配置文件 {path} 不存在，使用默认标签", file=sys.stderr)
        return ["日常生活", "严肃政治", "战争雷霆"]
    except json.JSONDecodeError as e:
        print(f"[警告] 配置文件解析失败: {e}，使用默认标签", file=sys.stderr)
        return ["日常生活", "严肃政治", "战争雷霆"]


class ZeroShotClassifier:
    """
    基于 sentence-transformers 的零样本分类器。
    模型实例在类级别共享（所有实例共用一个模型）。
    """

    # 类级别共享模型（避免 repeated loads across instances）
    _shared_model = None
    _shared_model_name = None
    _shared_load_failed = False
    _shared_load_time = None

    def __init__(self, model_name=None, cache_folder=None):
        self.model_name = model_name or _DEFAULT_MODEL
        self._cache_folder = cache_folder
        self._model = None
        self._load_failed = False
        self._load_time = None

        # 如果类级别已有加载好的模型，直接引用
        if self.__class__._shared_model is not None:
            self._model = self.__class__._shared_model
            self._load_failed = False
        elif self.__class__._shared_load_failed:
            self._load_failed = True

    def _load_model(self):
        """懒加载模型，优先使用本地缓存，失败时尝试在线加载（类级别单例）"""
        if self._model is not None:
            return
        if self._load_failed:
            return

        t0 = time.time()
        print(f"[分类器] 正在加载模型 {self.model_name} ...", file=sys.stderr)

        kwargs = {}
        if self._cache_folder:
            kwargs["cache_folder"] = self._cache_folder

        # ----- 第一轮：仅本地文件 -----
        try:
            model = SentenceTransformer(
                self.model_name,
                local_files_only=True,
                **kwargs,
            )
            self._model = model
        except Exception as e:
            print(f"[分类器] 本地缓存未找到模型 {self.model_name}: {e}", file=sys.stderr)
            print(f"[分类器] 尝试在线加载 ...", file=sys.stderr)

            # ----- 第二轮：在线加载 -----
            try:
                model = SentenceTransformer(
                    self.model_name,
                    local_files_only=False,
                    **kwargs,
                )
                self._model = model
            except Exception as e2:
                print(f"[分类器] 在线加载失败: {e2}", file=sys.stderr)

                # ----- 第三轮：降级到小模型 -----
                print(f"[分类器] 尝试降级到 {_FALLBACK_MODEL} ...", file=sys.stderr)
                try:
                    model = SentenceTransformer(
                        _FALLBACK_MODEL,
                        local_files_only=True,
                        **kwargs,
                    )
                    self._model = model
                    self.model_name = _FALLBACK_MODEL
                except Exception:
                    try:
                        model = SentenceTransformer(
                            _FALLBACK_MODEL,
                            local_files_only=False,
                            **kwargs,
                        )
                        self._model = model
                        self.model_name = _FALLBACK_MODEL
                    except Exception as e3:
                        print(f"[分类器] 所有模型加载均失败。", file=sys.stderr)
                        print(f"[分类器] 请先运行 python scripts/download_model.py 下载模型。", file=sys.stderr)
                        print(f"[分类器] 最终错误: {e3}", file=sys.stderr)
                        self._load_failed = True
                        self.__class__._shared_load_failed = True

        if self._model is not None:
            elapsed = time.time() - t0
            self._load_time = elapsed
            print(f"[分类器] 模型加载完成，耗时 {elapsed:.2f} 秒", file=sys.stderr)
            # 写入类级别共享
            self.__class__._shared_model = self._model
            self.__class__._shared_model_name = self.model_name
            self.__class__._shared_load_failed = False
            self.__class__._shared_load_time = elapsed

    def classify(self, text, candidate_labels=None):
        """
        对输入文本进行零样本分类。
        """
        self._load_model()

        if self._model is None:
            print(f"[分类器] 模型未加载，返回默认标签", file=sys.stderr)
            return ("日常生活", 0.0)

        if not text or not text.strip():
            return ("", 0.0)

        labels = candidate_labels or load_labels()

        t0 = time.time()
        text_emb = self._model.encode([text], convert_to_numpy=True)
        label_emb = self._model.encode(labels, convert_to_numpy=True)
        sims = cosine_similarity(text_emb, label_emb)[0]

        best_idx = int(np.argmax(sims))
        best_label = labels[best_idx]
        best_score = float(sims[best_idx])

        self._last_inference_ms = round((time.time() - t0) * 1000, 1)
        return (best_label, best_score)

    def classify_all_scores(self, text, candidate_labels=None):
        """返回所有标签的相似度分值，按降序排列"""
        self._load_model()

        if self._model is None:
            return []

        if not text or not text.strip():
            return []

        labels = candidate_labels or load_labels()

        t0 = time.time()
        text_emb = self._model.encode([text], convert_to_numpy=True)
        label_emb = self._model.encode(labels, convert_to_numpy=True)
        sims = cosine_similarity(text_emb, label_emb)[0]
        self._last_inference_ms = round((time.time() - t0) * 1000, 1)

        indices = np.argsort(sims)[::-1]
        return [(labels[i], float(sims[i])) for i in indices]

    def encode_batch(self, texts: list):
        """批量编码文本列表，返回 numpy 嵌入矩阵 (N, dim)。模型未加载时返回空数组。"""
        self._load_model()
        if self._model is None:
            return np.array([])
        if not texts:
            return np.array([])
        return self._model.encode(texts, convert_to_numpy=True)


# ---------- 命令行交互模式 ----------
def interactive_mode(classifier):
    print("\n" + "=" * 55)
    print("  零样本分类器 - 交互测试模式")
    print("=" * 55 + "\n")

    default_labels = load_labels()
    print(f"当前候选标签: {default_labels}\n")

    while True:
        try:
            text = input("消息 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if not text:
            continue
        if text.lower() == "q":
            break

        label, score = classifier.classify(text)

        all_scores = classifier.classify_all_scores(text)
        scores_str = " | ".join(f"{l}: {s:.4f}" for l, s in all_scores)

        ms = getattr(classifier, "_last_inference_ms", 0)

        print(f"  → 最佳: [{label}] (置信度: {score:.4f})")
        print(f"     全部: {scores_str}")
        print(f"     耗时: {ms:.1f} ms\n")


# ---------- 快捷函数 ----------
_global_classifier = None


def get_classifier():
    global _global_classifier
    if _global_classifier is None:
        _global_classifier = ZeroShotClassifier()
    return _global_classifier


def classify(text, candidate_labels=None):
    return get_classifier().classify(text, candidate_labels)


if __name__ == "__main__":
    clf = ZeroShotClassifier()
    interactive_mode(clf)
