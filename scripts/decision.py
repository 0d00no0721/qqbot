import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import set_env

from transformers import pipeline

# 全局缓存分类器（模块级别只加载一次）
_classifier = None

# 候选标签（可根据需要扩展）
CANDIDATE_LABELS = ["日常生活", "严肃政治", "战争雷霆"]

# 各标签是否应回复的规则
# True 表示该标签下应回复，False 表示不应回复
REPLY_RULES = {
    "日常生活": True,
    "严肃政治": False,
    "战争雷霆": False,
}

# 置信度阈值
CONFIDENCE_THRESHOLD = 0.6


def _get_classifier():
    """懒加载分类器"""
    global _classifier
    if _classifier is None:
        print("正在加载零样本分类模型 ...", file=sys.stderr)
        _classifier = pipeline(
            "zero-shot-classification",
            model="cross-encoder/nli-MiniLM2-L6-H768",
        )
        print("模型加载成功！", file=sys.stderr)
    return _classifier


def should_reply(message_text: str) -> tuple[bool, str, float]:
    """
    判断一条群消息是否应该回复。

    参数:
        message_text: 消息文本

    返回:
        (should_reply: bool, best_label: str, confidence: float)
    """
    if not message_text or not message_text.strip():
        return False, "", 0.0

    classifier = _get_classifier()
    result = classifier(message_text, CANDIDATE_LABELS)

    best_label = result["labels"][0]
    confidence = result["scores"][0]

    if confidence < CONFIDENCE_THRESHOLD:
        # 置信度不足，保守地不回复
        return False, best_label, confidence

    should = REPLY_RULES.get(best_label, False)
    return should, best_label, confidence


def main():
    """命令行交互测试"""
    print("发言决策器 - 命令行测试")
    print("输入消息文本，程序判断是否应该回复。输入 q 退出。\n")

    # 预加载模型
    _get_classifier()
    print("")

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

        reply, label, conf = should_reply(text)
        action = "应该回复" if reply else "不回复"
        print(f"  结果: {action} (标签={label}, 置信度={conf:.4f})\n")


if __name__ == "__main__":
    main()
