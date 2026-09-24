#!/usr/bin/env python3
"""Fail-closed plan and scoped-diff Claude review entry (Python 3.9+)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from review_material import (MaterialError, diff_material, enforce_limit, full_git_metadata,
                             git_root, read_text, safe_out, selected, sha, validate_plan, write_snapshot)
from review_runner import MODEL_FAMILIES, RunError, capability, run_stage
from ai_anti_config import ConfigError, load as load_config, model_families
from review_chunks import chunk_stage, chunk_file, required_files, source_labels

ROOT = Path(__file__).resolve().parents[5]
CORE = ROOT / "core" / "REVIEW_PRINCIPLES.md"
DISCIPLINE = ROOT / "core" / "CLI_DISCIPLINE.md"
COMPAT = ROOT / "compat.json"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def compat_warning(version_text):
    """Warn (never block) when the CLI version is not in the tested list."""
    match = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", version_text or "")
    if not match:
        return "unrecognized claude CLI version: " + repr(version_text)
    try:
        tested = json.loads(COMPAT.read_text(encoding="utf-8"))["tested"]["claude"]
    except (OSError, ValueError, KeyError, TypeError):
        return "CLI compatibility list unavailable: " + str(COMPAT)
    if match.group(0) not in tested:
        return ("claude CLI " + match.group(0) + " is not in the tested list " + json.dumps(tested)
                + "; fail-closed gates still decide the result")
    return None


def core_sections(text, mode):
    labels = [
        "R1｜角色、边界与第二意见",
        "R4｜方案评审方式与输出" if mode == "plan" else "R5｜增量（diff）评审",
        "R6｜分诊、轮次与成本",
        "R7｜需求核对、终审与汇报的公共部分",
        "R8｜四问",
        "R10｜诚实边界",
    ]
    extracted = []
    for label in labels:
        start = text.find("## " + label)
        if start < 0:
            raise MaterialError("missing required core section: " + label)
        end = text.find("\n## ", start + 1)
        extracted.append(text[start:] if end < 0 else text[start:end])
    return "\n\n".join(extracted)


def snapshot_hashes(stage):
    hashes = {}
    size = 0
    for path in sorted(stage.rglob("*")):
        if path.is_file():
            data = read_text(path, str(path.relative_to(stage)))
            size += len(data)
            if size > 2 * 1024 * 1024:
                raise MaterialError("stage snapshot exceeds 2MiB")
            hashes[str(path.relative_to(stage))] = sha(data)
    return hashes


def require_full_reads(result, required, sources):
    missing = sorted(set(required) - result["complete_reads"])
    if missing:
        labels = source_labels(sources)
        raise RunError("mandatory material was not fully Read: " + ", ".join(
            labels.get(name, name) for name in missing), "incomplete_reads")


def make_prompt(mode, required, rules, scale, sources):
    lines = [
        "你是独立的跨模型评审者。",
        "只读下列材料。先用 Read 完整读取 Required Read files 的每个文件及块（包括尾空行），不能省略任何块。Grep/Glob 不能替代 Read。按来源映射的顺序拼读分块；块本身无额外标题。不要尝试 shell、写入或越界。",
        "Required Read files: " + json.dumps(required, ensure_ascii=False),
        "Source mapping: " + json.dumps(sources, ensure_ascii=False),
        "项目部署规模：" + scale,
        rules,
    ]
    if mode == "inventory":
        lines.append("先读 request 与冻结源码，不读方案。仅输出中文独立改动/检查清单。")
    elif mode == "plan":
        lines.append("先读 request，再读方案和源码。输出中文硬伤、差集、失败场景、必修/可登记；末尾逐行回答四问。")
        lines.append("原始独立清单在 inventory.md 对应的必读文件或块内，不得改写。")
    else:
        lines.append("评审冻结变更。输出中文 bug、边界、回归风险、位置、失败场景、必修/可登记；末尾逐行回答四问。")
    return "\n\n".join(lines)


def parser(families=None):
    result = argparse.ArgumentParser()
    result.add_argument("mode", choices=("plan", "diff"))
    result.add_argument("--repo", required=True)
    result.add_argument("--files", nargs="+", required=True)
    result.add_argument("--context", nargs="*", default=[])
    result.add_argument("--plan")
    result.add_argument("--scale", required=True)
    result.add_argument("--out", required=True)
    result.add_argument("--model", choices=sorted(families or MODEL_FAMILIES), default="opus")
    result.add_argument("--timeout", type=int, default=600)
    result.add_argument("--idle-timeout", type=int, default=300)
    result.add_argument("--max-output-bytes", type=int, default=2 * 1024 * 1024)
    result.add_argument("--prepare-only", action="store_true")
    return result


def check_arguments(args, parser_instance):
    if args.mode == "plan" and not args.plan:
        parser_instance.error("plan mode requires --plan")
    if args.mode == "diff" and args.plan:
        parser_instance.error("--plan is only valid in plan mode")
    if not args.scale.strip():
        parser_instance.error("--scale must not be empty")
    limits = (args.timeout, args.idle_timeout, args.max_output_bytes)
    if min(limits) <= 0 or args.timeout > 3600 or args.idle_timeout > 600 or args.max_output_bytes > 8 * 1024 * 1024:
        parser_instance.error("invalid bounded resource limits")


def prepare(args, repo, output, coretext, families):
    logs = output / "logs"
    logs.mkdir()
    metadata = full_git_metadata(repo)
    metadata_bytes = len(metadata["status"].encode("utf-8")) + sum(
        len(path.encode("utf-8")) + 1 for path in metadata["tracked"]
    )
    if metadata_bytes > 2 * 1024 * 1024:
        raise MaterialError("git inventory exceeds 2MiB")
    files = selected(repo, args.files, allow_missing=args.mode == "diff")
    context = selected(repo, args.context)
    file_names = {name for name, _path, _data in files}
    if file_names.intersection(name for name, _path, _data in context):
        raise MaterialError("--files and --context must not overlap")
    plan = request = None
    if args.mode == "plan":
        plan, plan_data, request, request_data = validate_plan(args.plan)
        aliases = {str(path.absolute()) for _name, path, _data in files + context}
        if str(plan.absolute()) in aliases or str(request.absolute()) in aliases:
            raise MaterialError("plan/request must not be selected material")
        enforce_limit(files, context, [("plan", plan, plan_data), ("request", request, request_data)])
    else:
        enforce_limit(files, context)
    inventory = output / "materials" / "inventory"
    review = output / "materials" / "review"
    inventory.mkdir(parents=True)
    review.mkdir(parents=True)
    manifest = {
        "status": "preparing", "mode": args.mode, "requested_model": args.model,
        "model_family_pattern": families[args.model].pattern,
        "scale": args.scale, "core_root": str(ROOT), "core_sha256": sha(coretext.encode("utf-8")),
        "discipline_sha256": sha(DISCIPLINE.read_bytes()), "repo": str(repo),
        "head": metadata["head"], "git_status": metadata["status"],
        "tracked_paths": metadata["tracked"], "created_at": time.time(),
    }
    manifest["selected"] = write_snapshot(inventory, files)
    manifest["context"] = write_snapshot(inventory, context)
    write_snapshot(review, files)
    write_snapshot(review, context)
    for stage in (inventory, review):
        (stage / "tracked-paths.txt").write_text("\n".join(metadata["tracked"]) + "\n", encoding="utf-8")
        (stage / "git-status.txt").write_text(metadata["status"], encoding="utf-8")
    if args.mode == "plan":
        (inventory / "request.md").write_bytes(request_data)
        (review / "request.md").write_bytes(request_data)
        (review / "plan.md").write_bytes(plan_data)
        manifest["plan"] = {"path": str(plan), "sha256": sha(plan_data)}
        manifest["request"] = {"path": str(request), "sha256": sha(request_data)}
    else:
        manifest["changes"] = diff_material(repo, files, review)
        (review / "scope-changes.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in manifest["changes"]) + "\n",
            encoding="utf-8",
        )
        if sum(len(path.read_bytes()) for path in review.rglob("*") if path.is_file()) > 2 * 1024 * 1024:
            raise MaterialError("diff snapshot materials exceed 2MiB")
    manifest["sources"] = {"inventory": chunk_stage(inventory), "review": chunk_stage(review)}
    manifest["snapshots"] = {"inventory": snapshot_hashes(inventory), "review": snapshot_hashes(review)}
    return output, logs, inventory, review, manifest, files, context


def main():
    try:
        families = model_families(load_config(), MODEL_FAMILIES)
    except ConfigError as exc:
        print("review preparation failed: " + str(exc), file=sys.stderr)
        return 2
    argument_parser = parser(families)
    args = argument_parser.parse_args()
    check_arguments(args, argument_parser)
    output = None
    manifest = None
    warnings = []
    try:
        if not (CORE.is_file() and DISCIPLINE.is_file() and os.access(CORE, os.R_OK) and os.access(DISCIPLINE, os.R_OK)):
            raise MaterialError("core authority files unavailable")
        coretext = CORE.read_text(encoding="utf-8")
        rules = core_sections(coretext, args.mode)
        repo = git_root(args.repo)
        output = safe_out(args.out, repo)
        output, logs, inventory, review, manifest, files, context = prepare(args, repo, output, coretext, families)
        initial_mode = "inventory" if args.mode == "plan" else "diff"
        initial_sources = manifest["sources"]["inventory" if args.mode == "plan" else "review"]
        initial_required = required_files(initial_sources)
        initial_prompt = make_prompt(initial_mode, initial_required, rules, args.scale, initial_sources)
        prompt_name = "inventory.prompt.txt" if args.mode == "plan" else "review.prompt.txt"
        (logs / prompt_name).write_text(initial_prompt, encoding="utf-8")
        manifest["prompt_sha256"] = {prompt_name: sha(initial_prompt.encode("utf-8"))}
        write_json(output / "manifest.json", manifest)
        if args.prepare_only:
            manifest["status"] = "prepared"
            write_json(output / "manifest.json", manifest)
            write_json(output / "status.json", {"status": "prepared", "completed": False, "out": str(output)})
            print(output)
            return 0
        claude = shutil.which("claude")
        if not claude:
            raise RunError("claude not found on PATH")
        manifest["capabilities"] = capability(claude, logs)
        manifest["cli_version"] = manifest["capabilities"]["cli_version"]
        warning = compat_warning(manifest["cli_version"])
        if warning:
            warnings.append(warning)
            manifest["warnings"] = warnings
            print("warning: " + warning, file=sys.stderr)
        manifest["stages"] = {}
        write_json(output / "manifest.json", manifest)
        if args.mode == "plan":
            inventory_required = initial_required
            first = run_stage(claude, inventory, initial_prompt, logs / "inventory.jsonl", args.model, args.timeout, args.idle_timeout, args.max_output_bytes, families)
            require_full_reads(first, inventory_required, initial_sources)
            if snapshot_hashes(inventory) != manifest["snapshots"]["inventory"]:
                raise RunError("inventory snapshot changed during review")
            manifest["stages"]["inventory"] = {k: v for k, v in first.items() if k not in ("result", "complete_reads")}
            manifest["inventory_sha256"] = sha(first["result"].encode("utf-8"))
            (review / "inventory.md").write_text(first["result"], encoding="utf-8")
            manifest["sources"]["review"]["inventory.md"] = chunk_file(review, "inventory.md")
            manifest["snapshots"]["review"] = snapshot_hashes(review)
            write_json(output / "manifest.json", manifest)
            review_sources = manifest["sources"]["review"]
            review_required = required_files(review_sources)
            review_prompt = make_prompt("plan", review_required, rules, args.scale, review_sources)
            manifest["prompt_sha256"]["review.prompt.txt"] = sha(review_prompt.encode("utf-8"))
            write_json(output / "manifest.json", manifest)
            second = run_stage(claude, review, review_prompt, logs / "review.jsonl", args.model, args.timeout, args.idle_timeout, args.max_output_bytes, families)
            require_full_reads(second, review_required, review_sources)
            report = "# 独立清单（原样）\n\n" + first["result"] + "\n\n# 方案评审\n\n" + second["result"]
        else:
            required = initial_required
            second = run_stage(claude, review, initial_prompt, logs / "review.jsonl", args.model, args.timeout, args.idle_timeout, args.max_output_bytes, families)
            require_full_reads(second, required, initial_sources)
            report = second["result"]
        if snapshot_hashes(review) != manifest["snapshots"]["review"]:
            raise RunError("review snapshot changed during review")
        manifest["stages"]["review"] = {k: v for k, v in second.items() if k not in ("result", "complete_reads")}
        if warnings:
            report = "".join("> 警告：" + item + "\n" for item in warnings) + "\n" + report
        (output / "report.md").write_text(report, encoding="utf-8")
        manifest.update({"status": "completed", "actual_model": second["model"], "exit_reason": "verified result", "finished_at": time.time()})
        write_json(output / "manifest.json", manifest)
        write_json(output / "status.json", {"status": "completed", "completed": True, "out": str(output),
                                            "warnings": warnings})
        print(output)
        return 0
    except (MaterialError, RunError, OSError, ValueError, TypeError, KeyboardInterrupt) as exc:
        if output:
            if manifest:
                manifest.update({"status": "failed", "exit_reason": str(exc), "finished_at": time.time()})
                write_json(output / "manifest.json", manifest)
            write_json(output / "status.json", {"status": "failed", "completed": False, "error": str(exc),
                                                "failure_kind": getattr(exc, "kind", "preparation_failed"),
                                                "warnings": warnings})
            print(output)
        else:
            print("review preparation failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
