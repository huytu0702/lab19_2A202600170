import json
import os
import re
from typing import Iterable

from neo4j import GraphDatabase

from common import MODEL_NAME, call_llm, chunk_text, read_markdown_docs, write_json


NEO4J_URI = os.getenv("NEO4J_URI", "")
NEO4J_USER = os.getenv("username", "")
NEO4J_PASSWORD = os.getenv("password", "")


def extract_triples(chunk: str) -> tuple[list[tuple[str, str, str]], dict]:
    prompt = (
        "Extract knowledge triples from the text. Output ONLY valid JSON array of objects "
        'with keys: subject, predicate, object. Keep predicate in UPPER_SNAKE_CASE. '
        "Do not include markdown, prose, or code fences.\\n\\n"
        f"Text:\\n{chunk}"
    )
    res = call_llm(prompt, model=MODEL_NAME)

    triples: list[tuple[str, str, str]] = []
    text = res.text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = []

    if not isinstance(data, list):
        data = []

    for item in data:
        if not isinstance(item, dict):
            continue
        s = str(item.get("subject", "")).strip()
        p = str(item.get("predicate", "")).strip().upper()
        o = str(item.get("object", "")).strip()
        if not re.fullmatch(r"[A-Z0-9_]{2,64}", p):
            continue
        if s and p and o:
            triples.append((s, p, o))

    usage = {
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "total_tokens": res.total_tokens,
        "latency_ms": res.latency_ms,
        "estimated_cost_usd": res.estimated_cost_usd,
    }
    return triples, usage


def normalize_entity(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def unique_triples(triples: Iterable[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for s, p, o in triples:
        key = (normalize_entity(s), p, normalize_entity(o))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def upsert_triples_neo4j(triples: list[tuple[str, str, str]]) -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    query = (
        "MERGE (s:Entity {name: $subject}) "
        "MERGE (o:Entity {name: $object}) "
        "MERGE (s)-[r:RELATION {type: $predicate}]->(o)"
    )
    with driver.session() as session:
        session.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")
        for s, p, o in triples:
            session.run(query, subject=s, predicate=p, object=o)
    driver.close()


def main() -> None:
    if not NEO4J_URI or not NEO4J_USER or not NEO4J_PASSWORD:
        raise ValueError("Missing Neo4j configuration in .env")
    docs = read_markdown_docs("data")
    all_triples: list[tuple[str, str, str]] = []
    usage_rows: list[dict] = []

    for doc in docs:
        chunks = chunk_text(doc["text"])
        for idx, chunk in enumerate(chunks):
            triples, usage = extract_triples(chunk)
            usage_rows.append({"doc": doc["name"], "chunk": idx, **usage})
            all_triples.extend(triples)

    deduped = unique_triples(all_triples)
    upsert_triples_neo4j(deduped)

    total_input = sum(r["input_tokens"] for r in usage_rows)
    total_output = sum(r["output_tokens"] for r in usage_rows)
    total_tokens = sum(r["total_tokens"] for r in usage_rows)
    total_cost = sum(r["estimated_cost_usd"] for r in usage_rows)
    total_latency = sum(r["latency_ms"] for r in usage_rows)

    payload = {
        "model": MODEL_NAME,
        "docs": len(docs),
        "raw_triples": len(all_triples),
        "deduped_triples": len(deduped),
        "usage_summary": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_tokens,
            "estimated_cost_usd": total_cost,
            "latency_ms_total": total_latency,
            "latency_ms_avg": (total_latency / len(usage_rows)) if usage_rows else 0,
        },
        "chunks": usage_rows,
    }
    write_json("results/ingest_usage.json", payload)
    print("Done ingest. Wrote results/ingest_usage.json")


if __name__ == "__main__":
    main()
