import json
from pathlib import Path
from statistics import mean
from typing import Any

from ragas import EvaluationDataset, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
from langchain_openai import ChatOpenAI

from common import MODEL_NAME, OPENAI_API_BASE, OPENAI_API_KEY
from flat_rag import answer_question as answer_flat
from flat_rag import build_vectorstore
from graphrag import answer_question as answer_graph


def load_eval_dataset(path: str = "data/eval_dataset_20.json") -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(rows: list[dict[str, Any]], system: str) -> dict[str, Any]:
    selected = [r for r in rows if r["system"] == system]
    latencies = [r["usage"]["latency_ms"] for r in selected]
    return {
        "system": system,
        "questions": len(selected),
        "input_tokens": sum(r["usage"]["input_tokens"] for r in selected),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in selected),
        "total_tokens": sum(r["usage"]["total_tokens"] for r in selected),
        "estimated_cost_usd": sum(r["usage"]["estimated_cost_usd"] for r in selected),
        "latency_ms_avg": mean(latencies) if latencies else 0,
        "latency_ms_max": max(latencies) if latencies else 0,
    }


def evaluate_ragas(rows: list[dict[str, Any]], system: str) -> dict[str, Any]:
    selected = [r for r in rows if r["system"] == system]
    samples = [
        {
            "user_input": r["question"],
            "response": r["answer"],
            "retrieved_contexts": r.get("contexts", []),
            "reference": r["reference_answer"],
        }
        for r in selected
    ]

    eval_dataset = EvaluationDataset.from_list(samples)
    llm = ChatOpenAI(model=MODEL_NAME, api_key=OPENAI_API_KEY, base_url=OPENAI_API_BASE)
    ragas_llm = LangchainLLMWrapper(llm)

    result = evaluate(
        dataset=eval_dataset,
        metrics=[context_recall, context_precision, faithfulness, answer_relevancy],
        llm=ragas_llm,
    )
    scores = result.to_pandas().to_dict(orient="records")

    metric_avg: dict[str, float] = {
        "context_recall": mean([float(x.get("context_recall", 0) or 0) for x in scores]) if scores else 0,
        "context_precision": mean([float(x.get("context_precision", 0) or 0) for x in scores]) if scores else 0,
        "faithfulness": mean([float(x.get("faithfulness", 0) or 0) for x in scores]) if scores else 0,
        "answer_relevancy": mean([float(x.get("answer_relevancy", 0) or 0) for x in scores]) if scores else 0,
    }
    return {
        "system": system,
        "metric_avg": metric_avg,
        "per_question": scores,
    }


def write_markdown(payload: dict[str, Any], path: str) -> None:
    lines: list[str] = ["# Benchmark Report", "", "## System Summary", ""]

    for s in payload["summary"]:
        lines += [
            f"### {s['system']}",
            f"- Questions: {s['questions']}",
            f"- Total tokens: {s['total_tokens']}",
            f"- Estimated cost (USD): {s['estimated_cost_usd']:.6f}",
            f"- Avg latency (ms): {s['latency_ms_avg']:.2f}",
            "",
        ]

    lines += ["## RAGAS Comparison", ""]
    for r in payload["ragas"]:
        m = r["metric_avg"]
        lines += [
            f"### {r['system']}",
            f"- context_recall: {m['context_recall']:.4f}",
            f"- context_precision: {m['context_precision']:.4f}",
            f"- faithfulness: {m['faithfulness']:.4f}",
            f"- answer_relevancy: {m['answer_relevancy']:.4f}",
            "",
        ]

    lines += ["## Per Question Outputs", ""]
    for r in payload["results"]:
        lines += [
            f"### {r['system']} - Q{r['question_id']}",
            f"- Question: {r['question']}",
            f"- Answer: {r['answer']}",
            f"- Tokens: {r['usage']['total_tokens']}",
            f"- Cost (USD): {r['usage']['estimated_cost_usd']:.6f}",
            f"- Latency (ms): {r['usage']['latency_ms']:.2f}",
            "",
        ]

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    dataset = load_eval_dataset()
    vectorstore = build_vectorstore()

    rows: list[dict[str, Any]] = []
    for item in dataset:
        qid = item["id"]
        question = item["question"]
        reference = item["reference_answer"]

        g = answer_graph(question)
        g["question_id"] = qid
        g["reference_answer"] = reference
        rows.append(g)

        f = answer_flat(question, vectorstore)
        f["question_id"] = qid
        f["reference_answer"] = reference
        rows.append(f)

    ragas_graph = evaluate_ragas(rows, "graphrag")
    ragas_flat = evaluate_ragas(rows, "flat_rag")

    payload = {
        "dataset": "data/eval_dataset_20.json",
        "results": rows,
        "summary": [summarize(rows, "graphrag"), summarize(rows, "flat_rag")],
        "ragas": [ragas_graph, ragas_flat],
    }

    Path("results").mkdir(parents=True, exist_ok=True)
    Path("results/benchmark.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(payload, "results/benchmark.md")
    print("Wrote results/benchmark.json and results/benchmark.md")


if __name__ == "__main__":
    main()
