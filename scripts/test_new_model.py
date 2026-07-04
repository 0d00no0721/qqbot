"""
测试 paraphrase-multilingual-MiniLM-L12-v2 模型的中文零样本分类效果
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import set_env  # 确保模型缓存到 E:\QQbot\models_cache

from scripts.zero_shot_classifier import ZeroShotClassifier


def run_tests():
    print("=" * 60)
    print("  中文零样本分类测试 — paraphrase-multilingual-MiniLM-L12-v2")
    print("=" * 60)

    # 初始化分类器（记录加载时间）
    clf = ZeroShotClassifier()

    # 候选标签
    default_labels = ["日常生活", "严肃政治", "战争雷霆"]
    print(f"\n候选标签: {default_labels}\n")

    # 测试用例: (消息, 期望标签, 说明)
    test_cases = [
        ("今天中午吃什么？",          "日常生活",  "日常闲聊-吃饭"),
        ("战争雷霆新版本太坑了",       "战争雷霆",  "游戏讨论"),
        ("政府这个政策我不理解",       "严肃政治",  "政治相关"),
        ("有人知道怎么修电脑吗",       "日常生活",  "日常求助"),
        ("今晚吃鸡吗？",              "日常生活",  "日常闲聊-约饭"),
        ("苏联T-34和德国虎式哪个好",  "战争雷霆",  "游戏内容"),
        ("人大代表提议修改法律",       "严肃政治",  "政治新闻"),
        ("明天天气怎么样",            "日常生活",  "日常询问"),
        ("这个游戏的平衡性太差了",     "战争雷霆",  "游戏吐槽"),
        ("房价还会涨吗",             "严肃政治",  "社会经济"),
    ]

    print(f"{'消息':<30} {'期望':<10} {'结果':<10} {'置信度':<8} {'耗时(ms)':<8}")
    print("-" * 70)

    total_time = 0
    correct = 0

    for i, (msg, expected, note) in enumerate(test_cases):
        t0 = time.time()
        label, score = clf.classify(msg)
        elapsed_ms = (time.time() - t0) * 1000

        total_time += elapsed_ms
        is_correct = (label == expected)
        if is_correct:
            correct += 1

        mark = "✓" if is_correct else "✗"
        msg_display = msg[:28] + ".." if len(msg) > 28 else msg
        print(f"{mark} {msg_display:<28} {expected:<10} {label:<10} {score:<8.4f} {elapsed_ms:<8.1f}")

    # 汇总
    avg_time = total_time / len(test_cases)
    print("-" * 70)
    print(f"总计: {len(test_cases)} 条, 正确: {correct}, 正确率: {correct/len(test_cases)*100:.1f}%")
    print(f"平均推理时间: {avg_time:.1f} ms (不含模型加载)")
    print(f"模型加载时间: {clf._load_time:.2f} 秒\n")

    # 显示全部相似度排名（选前 3 个用例做详细展示）
    print("=" * 60)
    print("  详细分值展示（前 3 条消息）")
    print("=" * 60)
    for msg, expected, note in test_cases[:3]:
        all_scores = clf.classify_all_scores(msg)
        scores_str = " | ".join(f"{l}: {s:.4f}" for l, s in all_scores)
        print(f"\n消息: {msg}  (期望: {expected})")
        print(f"  排序: {scores_str}")


if __name__ == "__main__":
    run_tests()
