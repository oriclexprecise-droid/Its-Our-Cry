# -*- coding: utf-8 -*-
"""AI 纯文本行式协议测试：API 不再要求 JSON，失败句仍真实上报，不生成兜底假数据。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import emotion_analyzer, translator
from backend.ai_ops import extract_text_results, extract_single_emotion, extract_single_translation

EMOTIONS = ["微笑", "悲伤", "思考", "生气"]


def make_scripted_client(responses, captured=None):
    state = {"calls": 0}

    class Message:
        def __init__(self, content):
            self.content = content

    class Choice:
        def __init__(self, content):
            self.message = Message(content)
            self.finish_reason = "stop"

    class Response:
        def __init__(self, content):
            self.choices = [Choice(content)]

    class Completions:
        def create(self, **kwargs):
            if captured is not None:
                captured.append(kwargs)
            item = responses[min(state["calls"], len(responses) - 1)]
            state["calls"] += 1
            if isinstance(item, Exception):
                raise item
            return Response(item)

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    return Client()


def test_extract_text_results_emotions():
    rows = extract_text_results("0|微笑\n1|悲伤", emotions=EMOTIONS)
    assert rows == [{"index": 0, "emotion": "微笑"}, {"index": 1, "emotion": "悲伤"}], rows

    row = extract_text_results("0|微笑|こんにちは", emotions=EMOTIONS)
    assert row == [{"index": 0, "emotion": "微笑", "translation": "こんにちは"}], row

    row = extract_text_results("[1]: 思考", emotions=EMOTIONS)
    assert row == [{"index": 1, "emotion": "思考"}], row


def test_extract_text_results_translate():
    rows = extract_text_results("0|こんにちは\n1|さようなら")
    assert rows == [{"index": 0, "translation": "こんにちは"}, {"index": 1, "translation": "さようなら"}], rows


def test_extract_text_results_tolerant_variants():
    rows = extract_text_results("0. 微笑\n1. 悲伤", emotions=EMOTIONS)
    assert [r["emotion"] for r in rows] == ["微笑", "悲伤"], rows

    rows = extract_text_results("[0] 微笑\n[1] 悲伤", emotions=EMOTIONS)
    assert [r["emotion"] for r in rows] == ["微笑", "悲伤"], rows

    rows = extract_text_results("0｜微笑\n1｜悲伤", emotions=EMOTIONS)
    assert [r["emotion"] for r in rows] == ["微笑", "悲伤"], rows

    rows = extract_text_results("0|微笑, 1|悲伤", emotions=EMOTIONS)
    assert [r["emotion"] for r in rows] == ["微笑", "悲伤"], rows

    rows = extract_text_results("0. こんにちは\n1. さようなら")
    assert [r["translation"] for r in rows] == ["こんにちは", "さようなら"], rows


def test_analyze_tolerant_single_line():
    emotion_analyzer.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["[0] 微笑"]
    )
    failed = []
    out = emotion_analyzer.analyze_emotions(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        lang="zh",
        emotions=EMOTIONS,
        failed_out=failed,
    )
    assert out == [{"index": 0, "emotion": "微笑"}], out
    assert failed == [], failed


def test_translate_tolerant_single_line():
    translator.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["0. こんにちは"]
    )
    out = translator.translate_lines(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        name_readings=[],
        failed_out=[],
    )
    assert out == [{"index": 0, "translation": "こんにちは"}], out


def test_extract_single_helpers():
    assert extract_single_emotion("这句话的情绪是微笑", EMOTIONS) == "微笑"
    assert extract_single_emotion("[0] 微笑", EMOTIONS) == "微笑"
    assert extract_single_translation("0. こんにちは") == "こんにちは"
    assert extract_single_translation("こんにちは") == "こんにちは"
    assert extract_single_translation("0|こんにちは") == "こんにちは"
    try:
        extract_single_emotion("完全不可解析", EMOTIONS)
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_analyze_zh_text_protocol():
    captured = []
    emotion_analyzer.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["0|微笑\n1|悲伤"], captured
    )
    out = emotion_analyzer.analyze_emotions(
        lines=[
            {"index": 0, "character": "千早爱音", "text": "你好"},
            {"index": 1, "character": "旁白", "text": "她笑了"},
        ],
        api_key="test",
        model="deepseek-v4-flash",
        lang="zh",
        emotions=EMOTIONS,
        failed_out=[],
    )
    assert [r["emotion"] for r in out] == ["微笑", "悲伤"], out
    prompt = captured[0]["messages"][0]["content"]
    assert "输出格式：每句一行" in prompt, prompt
    assert "只输出严格 JSON 数组" not in prompt, prompt
    assert "index|情绪" in prompt, prompt


def test_analyze_ja_emotion_only():
    emotion_analyzer.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["0|微笑|こんにちは"]
    )
    out = emotion_analyzer.analyze_emotions(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        lang="ja",
        emotions=EMOTIONS,
        failed_out=[],
    )
    assert out == [{"index": 0, "emotion": "微笑"}], out


def test_translate_text_protocol():
    captured = []
    translator.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["0|こんにちは\n1|さようなら"], captured
    )
    out = translator.translate_lines(
        lines=[
            {"index": 0, "character": "千早爱音", "text": "你好"},
            {"index": 1, "character": "旁白", "text": "再见"},
        ],
        api_key="test",
        model="deepseek-v4-flash",
        name_readings=[],
        failed_out=[],
    )
    assert [r["translation"] for r in out] == ["こんにちは", "さようなら"], out
    prompt = captured[0]["messages"][0]["content"]
    assert "输出格式：每句一行" in prompt, prompt
    assert "index|译文" in prompt, prompt


def test_single_line_natural_language_no_fabrication():
    emotion_analyzer.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["这句话的情绪是微笑"]
    )
    failed = []
    out = emotion_analyzer.analyze_emotions(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        lang="zh",
        emotions=EMOTIONS,
        failed_out=failed,
    )
    assert out == [{"index": 0, "emotion": "微笑"}], out
    assert failed == [], failed


def test_unparseable_single_line_still_fails():
    emotion_analyzer.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["完全无法解析的内容"]
    )
    failed = []
    out = emotion_analyzer.analyze_emotions(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        lang="zh",
        emotions=EMOTIONS,
        failed_out=failed,
    )
    assert out == [], out
    assert failed == [0], failed


def test_single_translate_natural_language():
    translator.create_ai_client = lambda api_key, base_url: make_scripted_client(
        ["こんにちは"]
    )
    out = translator.translate_lines(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        name_readings=[],
        failed_out=[],
    )
    assert out == [{"index": 0, "translation": "こんにちは"}], out


def test_unparseable_translate_still_fails():
    translator.create_ai_client = lambda api_key, base_url: make_scripted_client(
        [""]
    )
    failed = []
    out = translator.translate_lines(
        lines=[{"index": 0, "character": "千早爱音", "text": "你好"}],
        api_key="test",
        model="deepseek-v4-flash",
        name_readings=[],
        failed_out=failed,
    )
    assert out == [], out
    assert failed == [0], failed


def main():
    tests = [
        test_extract_text_results_emotions,
        test_extract_text_results_translate,
        test_extract_text_results_tolerant_variants,
        test_extract_single_helpers,
        test_analyze_tolerant_single_line,
        test_translate_tolerant_single_line,
        test_analyze_zh_text_protocol,
        test_analyze_ja_emotion_only,
        test_translate_text_protocol,
        test_single_line_natural_language_no_fabrication,
        test_unparseable_single_line_still_fails,
        test_single_translate_natural_language,
        test_unparseable_translate_still_fails,
    ]
    for t in tests:
        t()
        print("PASS " + t.__name__)
    print("ALL PASS")


if __name__ == "__main__":
    main()