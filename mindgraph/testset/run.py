"""测试集流程 CLI。

  # 离线跑通管线（合成语料 + StubLLM），验证 plumbing：
  python -m mindgraph.testset.run generate --demo --dry-run --out-dir ./_kg_out

  # 真实生成（judge/强模型，读 chunks.jsonl）：
  python -m mindgraph.testset.run generate --chunks chunks.jsonl --target 150 --out-dir ./_kg_out

  # 人工在 _kg_out/testset.draft.json 校验后冻结：
  python -m mindgraph.testset.run freeze --draft ./_kg_out/testset.draft.json --out ./_kg_out/testset.gold.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..models import Chunk
from .generator import TestsetGenerator
from .validate import validate
from . import review


def _load_chunks(path: str) -> list[Chunk]:
    p = Path(path)
    raw = p.read_text(encoding="utf-8-sig")
    if p.suffix == ".jsonl":
        return [Chunk.from_dict(json.loads(line)) for line in raw.splitlines() if line.strip()]
    return [Chunk.from_dict(d) for d in json.loads(raw)]


def _demo_chunks() -> list[Chunk]:
    """合成语料：6 章 × 每章若干段，供离线验证管线。"""
    sections = ["导论", "核心概念", "方法论", "常见误区", "应用案例", "总结"]
    chunks: list[Chunk] = []
    n = 0
    for si, sec in enumerate(sections):
        for j in range(6):
            n += 1
            chunks.append(Chunk(
                id=f"chunk_{n:03d}",
                text=f"（{sec} 第{j + 1}段）作者在此论述了关于{sec}的一个论断，"
                     f"并给出论据与适用条件，示例若干。",
                section=sec, order=j, doc_id="demo",
            ))
    return chunks


def _build_llm(dry_run: bool):
    if dry_run:
        from ..llm import StubLLM
        return StubLLM()
    from ..llm import judge_llm
    return judge_llm()


def cmd_generate(args: argparse.Namespace) -> int:
    chunks = _demo_chunks() if args.demo else _load_chunks(args.chunks)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    llm = _build_llm(args.dry_run)
    gen = TestsetGenerator(llm, concurrency=args.concurrency)
    items = asyncio.run(gen.generate(chunks, target=args.target,
                                     min_per_section=args.min_per_section))

    draft = out_dir / "testset.draft.json"
    review.save_json(items, draft)

    report = validate(items, target=args.target, min_per_section=args.min_per_section)
    (out_dir / "validate.report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    review.export_review_markdown(items, out_dir / "review.md")

    print(f"生成 {len(items)} 条 → {draft}")
    print(f"配额：{json.dumps(report.summary, ensure_ascii=False)}")
    print(f"结构校验 ok={report.ok}，item 错误 {sum(len(v) for v in report.item_errors.values())} 处")
    if report.quota_warnings:
        print("配额提醒：")
        for w in report.quota_warnings:
            print(f"  - {w}")
    print(f"人读校验单 → {out_dir / 'review.md'}")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    items = review.load_json(args.draft)
    report, frozen = review.freeze(items, args.out, target=args.target,
                                   min_per_section=args.min_per_section)
    print(f"已校验 {len(frozen)} 条 → {args.out}")
    print(f"结构校验 ok={report.ok}")
    for w in report.quota_warnings:
        print(f"  - {w}")
    return 0 if report.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="mindgraph.testset.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="生成测试集草稿")
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--chunks", help="chunks 文件（.jsonl 或 .json）")
    src.add_argument("--demo", action="store_true", help="用合成语料")
    g.add_argument("--dry-run", action="store_true", help="用 StubLLM 离线跑通")
    g.add_argument("--target", type=int, default=100)
    g.add_argument("--min-per-section", type=int, default=8)
    g.add_argument("--concurrency", type=int, default=4)
    g.add_argument("--out-dir", default="./_kg_out")
    g.set_defaults(func=cmd_generate)

    f = sub.add_parser("freeze", help="人工校验后冻结")
    f.add_argument("--draft", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--target", type=int, default=100)
    f.add_argument("--min-per-section", type=int, default=8)
    f.set_defaults(func=cmd_freeze)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
