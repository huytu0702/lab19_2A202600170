import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

MODEL_NAME = os.getenv("LLM_MODEL", "gpt-5.4-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")

INPUT_PRICE_PER_1M = float(os.getenv("OPENAI_INPUT_PRICE_PER_1M", "0"))
OUTPUT_PRICE_PER_1M = float(os.getenv("OPENAI_OUTPUT_PRICE_PER_1M", "0"))


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    estimated_cost_usd: float


def get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is required")
    kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if OPENAI_API_BASE:
        kwargs["base_url"] = OPENAI_API_BASE
    return OpenAI(**kwargs)


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * INPUT_PRICE_PER_1M + (
        output_tokens / 1_000_000
    ) * OUTPUT_PRICE_PER_1M


def call_llm(prompt: str, model: str = MODEL_NAME) -> LLMResult:
    client = get_client()
    start = time.perf_counter()
    response = client.responses.create(model=model, input=prompt)
    latency_ms = (time.perf_counter() - start) * 1000

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(
        getattr(usage, "total_tokens", input_tokens + output_tokens)
        or (input_tokens + output_tokens)
    )

    text = getattr(response, "output_text", "") or ""
    if not text:
        text = str(response)

    return LLMResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        estimated_cost_usd=estimate_cost(input_tokens, output_tokens),
    )


def read_markdown_docs(data_dir: str = "data") -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    base = Path(data_dir)
    for p in sorted(base.glob("*.md")):
        docs.append(
            {"name": p.stem, "text": p.read_text(encoding="utf-8", errors="ignore")}
        )
    return docs


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(clean)


def write_json(path: str, payload: Any) -> None:
    target = Path(path)
    if target.is_absolute() or ".." in target.parts:
        raise ValueError("Invalid output path")
    base = Path("results").resolve()
    full = target.resolve()
    if full != base and base not in full.parents:
        raise ValueError("Output path must be inside results/")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
