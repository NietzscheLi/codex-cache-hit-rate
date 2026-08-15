#!/usr/bin/env python3
"""Codex Stop hook that reports prompt cache usage for the completed turn."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int = 0
    model_calls: int = 1


UNKNOWN_MODEL = "未标识模型"


def _nonnegative_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return value


def _extract_usage(record: dict[str, Any]) -> Usage | None:
    """Read known usage shapes without inspecting transcript message content."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    info = payload.get("info")
    candidates: list[Any] = []
    if isinstance(info, dict):
        candidates.append(info.get("last_token_usage"))
        candidates.append(info.get("usage"))
    candidates.extend((payload.get("usage"), record.get("usage")))

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        input_tokens = _nonnegative_int(candidate.get("input_tokens"))
        details = candidate.get("input_tokens_details")
        if not isinstance(details, dict):
            details = {}

        cached_tokens = _nonnegative_int(
            candidate.get("cached_input_tokens"),
            _nonnegative_int(details.get("cached_tokens"), 0),
        )
        cache_write_tokens = _nonnegative_int(
            candidate.get("cache_write_input_tokens"),
            _nonnegative_int(details.get("cache_write_tokens"), 0),
        )

        if input_tokens is None or cached_tokens is None or cache_write_tokens is None:
            continue
        if cached_tokens > input_tokens:
            continue

        return Usage(input_tokens, cached_tokens, cache_write_tokens)

    return None


def _usage_marker(record: dict[str, Any]) -> tuple[int, int, int, int] | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    total = info.get("total_token_usage")
    if not isinstance(total, dict):
        return None

    values = (
        _nonnegative_int(total.get("input_tokens")),
        _nonnegative_int(total.get("cached_input_tokens")),
        _nonnegative_int(total.get("cache_write_input_tokens"), 0),
        _nonnegative_int(total.get("total_tokens")),
    )
    if any(value is None for value in values):
        return None
    return values  # type: ignore[return-value]


def _records(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(record, dict):
            yield record


def _record_model(record: dict[str, Any], fallback: str | None) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    info = payload.get("info")
    if not isinstance(info, dict):
        info = {}
    for candidate in (payload.get("model"), info.get("model"), record.get("model"), fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return UNKNOWN_MODEL


def _aggregate(usages: list[tuple[str, Usage]]) -> dict[str, Usage]:
    by_model: dict[str, Usage] = {}
    for model, usage in usages:
        previous = by_model.get(model, Usage(0, 0, 0, 0))
        by_model[model] = Usage(
            input_tokens=previous.input_tokens + usage.input_tokens,
            cached_input_tokens=(
                previous.cached_input_tokens + usage.cached_input_tokens
            ),
            cache_write_input_tokens=(
                previous.cache_write_input_tokens + usage.cache_write_input_tokens
            ),
            model_calls=previous.model_calls + usage.model_calls,
        )
    return by_model


def read_turn_usage(
    transcript_path: str,
    turn_id: str | None,
    fallback_model: str | None = None,
) -> dict[str, Usage]:
    """Aggregate current-turn model requests, grouped by model."""
    path = Path(transcript_path)
    if not path.is_file():
        return {}

    collecting = False
    found_turn = False
    current_model = fallback_model
    turn_usages: list[tuple[str, Usage]] = []
    latest_usage: tuple[str, Usage] | None = None
    seen_markers: set[tuple[int, int, int, int]] = set()

    with path.open("r", encoding="utf-8", errors="replace") as transcript:
        for record in _records(transcript):
            if record.get("type") == "turn_context":
                payload = record.get("payload")
                record_turn_id = payload.get("turn_id") if isinstance(payload, dict) else None
                context_model = payload.get("model") if isinstance(payload, dict) else None
                if isinstance(context_model, str) and context_model.strip():
                    current_model = context_model.strip()
                collecting = turn_id is not None and record_turn_id == turn_id
                if collecting:
                    found_turn = True
                    turn_usages = []
                    seen_markers = set()

            usage = _extract_usage(record)
            if usage is None:
                continue
            model = _record_model(record, current_model)
            latest_usage = (model, usage)
            if not collecting:
                continue

            marker = _usage_marker(record)
            if marker is not None:
                if marker in seen_markers:
                    continue
                seen_markers.add(marker)
            turn_usages.append((model, usage))

    if turn_usages:
        return _aggregate(turn_usages)

    # Codex documents the transcript as unstable. If the current turn marker is
    # unavailable, retain useful behavior by reporting the latest request only.
    if not found_turn and latest_usage is not None:
        return _aggregate([latest_usage])
    return {}


def _format_model_usage(model: str, usage: Usage) -> str:
    if usage.input_tokens == 0:
        return f"{model}：不可计算（输入 token 为 0）"

    rate = usage.cached_input_tokens / usage.input_tokens * 100
    message = (
        f"{model}：{rate:.2f}% · 缓存输入 "
        f"{usage.cached_input_tokens:,} / {usage.input_tokens:,} tokens"
    )
    if usage.cache_write_input_tokens:
        message += f" · 缓存写入 {usage.cache_write_input_tokens:,} tokens"
    message += f" · {usage.model_calls} 次调用"
    return message


def format_message(usages: dict[str, Usage]) -> str:
    if not usages:
        return "缓存命中率：暂无可用 token 统计"

    lines = [_format_model_usage(model, usage) for model, usage in usages.items()]
    if len(lines) == 1:
        return f"缓存命中率 · {lines[0]}"
    return "缓存命中率（按模型）：\n" + "\n".join(f"- {line}" for line in lines)


def build_hook_output(hook_input: dict[str, Any]) -> dict[str, Any]:
    if hook_input.get("hook_event_name") != "Stop":
        return {"continue": True}

    transcript_path = hook_input.get("transcript_path")
    turn_id = hook_input.get("turn_id")
    usages: dict[str, Usage] = {}
    if isinstance(transcript_path, str) and transcript_path:
        model = hook_input.get("model")
        usages = read_turn_usage(
            transcript_path,
            turn_id if isinstance(turn_id, str) else None,
            model if isinstance(model, str) else None,
        )
    return {"continue": True, "systemMessage": format_message(usages)}


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
        if not isinstance(hook_input, dict):
            hook_input = {}
        output = build_hook_output(hook_input)
    except Exception:
        # A hook must always emit valid JSON; token telemetry must never break a turn.
        output = {
            "continue": True,
            "systemMessage": "缓存命中率：暂无可用 token 统计",
        }
    json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
