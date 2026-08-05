# -*- coding: utf-8 -*-
"""Guard: API emotion analysis / Japanese translation must reuse build_client_prompt.

Run with the GPT-SoVITS runtime python:
  E:\GPT-SoVITS-v2pro-20250604-nvidia50\...\runtime\python.exe tests\check_prompt_consistency.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import emotion_analyzer, translator
from backend.manual_ai import build_client_prompt


def make_fake_client(captured):
    class Message:
        def __init__(self, content):
            self.content = content

    class Choice:
        def __init__(self, content):
            self.message = Message(content)

    class Response:
        def __init__(self, content):
            self.choices = [Choice(content)]

    class Completions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return Response(
                json.dumps(
                    [
                        {"index": 0, "emotion": "\u5fae\u7b11"},
                        {"index": 1, "emotion": "\u601d\u8003"},
                    ],
                    ensure_ascii=False,
                )
            )

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    return Client()


QIANZAO = "\u5343\u65e9\u7231\u97f3"  # QianZaoAiYin
PANGBAI = "\u65c1\u767d"  # narration
CHIHAYA = "\u3061\u306f\u3084\u3042\u306e\u3093"  # ChihayaAnon
SORYORIN = "\u305d\u3088\u308a\u3093"  # Soyorin

LINES = [
    {"index": 0, "character": QIANZAO, "text": "\u4f60\u597d\u554asoyorin"},
    {"index": 1, "character": PANGBAI, "text": "\u5979\u9732\u51fa\u5fae\u7b11\u3002"},
]
EMOTIONS = ["\u5fae\u7b11", "\u601d\u8003"]
READINGS = [
    {"zh": QIANZAO, "ja": CHIHAYA},
    {"zh": "soyorin", "ja": SORYORIN},
]


def system_prompt_of(captured):
    return captured["kwargs"]["messages"][0]["content"]


def run():
    captured = {}
    emotion_analyzer.create_ai_client = lambda api_key, base_url: make_fake_client(captured)

    emotion_analyzer.analyze_emotions(
        lines=LINES,
        api_key="test",
        base_url="http://localhost",
        model="deepseek-chat",
        lang="zh",
        emotions=EMOTIONS,
        name_readings=READINGS,
    )
    prompt = system_prompt_of(captured)
    expected = build_client_prompt(
        LINES, EMOTIONS, lang="zh", mode="analyze", name_readings=READINGS
    )
    assert prompt == expected, "analyze-zh prompt diverged from build_client_prompt"

    captured.clear()
    emotion_analyzer.analyze_emotions(
        lines=LINES,
        api_key="test",
        base_url="http://localhost",
        model="deepseek-chat",
        lang="ja",
        emotions=EMOTIONS,
        name_readings=READINGS,
    )
    prompt = system_prompt_of(captured)
    expected = build_client_prompt(
        LINES, EMOTIONS, lang="ja", mode="analyze", name_readings=READINGS
    )
    assert prompt == expected, "analyze-ja prompt diverged from build_client_prompt"

    captured.clear()
    translator.create_ai_client = lambda api_key, base_url: make_fake_client(captured)
    translator.translate_lines(
        lines=LINES,
        api_key="test",
        base_url="http://localhost",
        model="deepseek-chat",
        name_readings=READINGS,
    )
    prompt = system_prompt_of(captured)
    expected = build_client_prompt(
        LINES, [], lang="ja", mode="translate", name_readings=READINGS
    )
    assert prompt == expected, "translate prompt diverged from build_client_prompt"

    print("PASS: API prompts match client prompt builder")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as exc:
        print("FAIL: " + str(exc))
        sys.exit(1)
