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
from review_channels import resolve, evidence, child_environment
from review_chunks import chunk_stage, chunk_file, required_files, source_labels
from review_materials import build_registry, mcp_config

ROOT = Path(__file__).resolve().parents[5]
CORE = ROOT / "core" / "REVIEW_PRINCIPLES.md"
DISCIPLINE = ROOT / "core" / "CLI_DISCIPLINE.md"
COMPAT = ROOT / "compat.json"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def compat_warning(version_text, executor="claude"):
    """Warn (never block) when the CLI version is not in the tested list."""
    match = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", version_text or "")
    if not match:
        return "unrecognized " + executor + " CLI version: " + repr(version_text)
    try:
        tested = json.loads(COMPAT.read_text(encoding="utf-8"))["tested"][executor]
    except (OSError, ValueError, KeyError, TypeError):
        return "CLI compatibility list unavailable: " + str(COMPAT)
    if match.group(0) not in tested:
        return (executor + " CLI " + match.group(0) + " is not in the tested list " + json.dumps(tested)
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


def make_prompt(mode, required, rules, scale, sources, registry=None):
    lines = [
        "你是独立的跨模型评审者。",
        "只读下列材料。先用 Read 完整读取 Required Read files 的每个文件及块（包括尾空行），不能省略任何块。Grep/Glob 不能替代 Read。按来源映射的顺序拼读分块；块本身无额外标题。不要尝试 shell、写入或越界。",
        "直接照抄下列短相对路径。仅材料目录内 Read 缺文件可纠正，每阶段累计最多三次；必须随后成功读取必读文件并完整读完全部材料。不得猜读外部路径。",
        "Required Read files: " + json.dumps(required, ensure_ascii=False),
        "Source mapping: " + json.dumps(sources, ensure_ascii=False),
        "项目部署规模：" + scale,
        rules,
    ]
    if registry is not None:
        labels = []
        for mid, item in registry['materials'].items():
            source_name, block = next((name, block) for name, source in sources.items()
                                      for block in source['blocks'] if block['path'] == item['path'])
            labels.append({'id': mid, 'source': source_name,
                           'start_line': block['start_line'], 'end_line': block['end_line']})
        lines = [lines[0],
            '先用 mcp__materials__read_material 完整读取每个必读编号，参数只能是 {"id":"编号"}。逐块按顺序阅读，包括尾空行。来源路径仅为评审位置标签，不能用作工具参数；不得尝试 shell、写入或任意文件访问。',
            'Required material IDs: ' + json.dumps(list(registry['materials'])),
            'Material source mapping: ' + json.dumps(labels, ensure_ascii=False),
            '项目部署规模：' + scale, rules]
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
    result.add_argument("--channel", choices=("claude", "qwen", "qoder"))
    result.add_argument("--qwen-executor", choices=("claude", "codex"))
    result.add_argument("--responses-base-url")
    result.add_argument("--model", default=None, help="Claude aliases: " +
                        ','.join(sorted(families or MODEL_FAMILIES)) + "; Qoder: kimi-k3,kmodel_latest")
    result.add_argument("--timeout", type=int, default=None)
    result.add_argument("--idle-timeout", type=int, default=900)
    result.add_argument("--max-output-bytes", type=int, default=None)
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
    if min(limits) <= 0 or args.timeout > 3600 or args.idle_timeout > 900 or args.max_output_bytes > 8 * 1024 * 1024:
        parser_instance.error("invalid bounded resource limits")


def prepare(args, repo, output, coretext, families, channel):
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
        "status": "preparing", "mode": args.mode, **evidence(channel),
        "model_family_pattern": channel["pattern"].pattern,
        "limits": {"timeout": args.timeout, "idle_timeout": args.idle_timeout, "max_output_bytes": args.max_output_bytes},
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
        config = load_config()
        families = model_families(config, MODEL_FAMILIES)
    except ConfigError as exc:
        print("review preparation failed: " + str(exc), file=sys.stderr)
        return 2
    argument_parser = parser(families)
    args = argument_parser.parse_args()
    output = None
    manifest = None
    warnings = []
    try:
        if not (CORE.is_file() and DISCIPLINE.is_file() and os.access(CORE, os.R_OK) and os.access(DISCIPLINE, os.R_OK)):
            raise MaterialError("core authority files unavailable")
        coretext = CORE.read_text(encoding="utf-8")
        rules = core_sections(coretext, args.mode)
        repo = git_root(args.repo)
        channel = resolve(config, args.channel, args.model, repo, families, args.qwen_executor, args.responses_base_url)
        codex_executor = channel.get("executor") == "codex"
        args.timeout = args.timeout if args.timeout is not None else (1800 if channel["channel"] == "qwen" else 600)
        args.max_output_bytes = args.max_output_bytes if args.max_output_bytes is not None else (8 if channel["channel"] == "qwen" else 2) * 1024 * 1024
        check_arguments(args, argument_parser)
        output = safe_out(args.out, repo)
        output, logs, inventory, review, manifest, files, context = prepare(args, repo, output, coretext, families, channel)
        initial_mode = "inventory" if args.mode == "plan" else "diff"
        initial_sources = manifest["sources"]["inventory" if args.mode == "plan" else "review"]
        initial_required = required_files(initial_sources)
        initial_stage = inventory if args.mode == 'plan' else review
        initial_registry_path = None
        initial_registry = None
        manifest['material_transport'] = 'material_ids' if channel['channel'] in ('qwen', 'qoder') else 'filesystem_read'
        if channel['channel'] in ('qwen', 'qoder'):
            initial_registry_path = logs / (initial_stage.name + '.registry.json')
            initial_registry = build_registry(initial_stage, initial_sources, initial_registry_path)
            manifest['material_registries'] = {initial_stage.name: initial_registry}
            if codex_executor:
                from review_codex import write_config
                write_config(channel, initial_registry_path, logs / (initial_stage.name + '.codex-config.json'))
            else:
                write_json(logs / (initial_stage.name + '.mcp.json'), mcp_config(initial_registry_path))
        initial_prompt = make_prompt(initial_mode, initial_required, rules, args.scale, initial_sources, initial_registry)
        prompt_name = "inventory.prompt.txt" if args.mode == "plan" else "review.prompt.txt"
        (logs / prompt_name).write_text(initial_prompt, encoding="utf-8")
        manifest["prompt_sha256"] = {prompt_name: sha(initial_prompt.encode("utf-8"))}
        write_json(output / "manifest.json", manifest)
        if args.prepare_only:
            manifest["status"] = "prepared"
            write_json(output / "manifest.json", manifest)
            write_json(output / "status.json", {"status": "prepared", "completed": False, "out": str(output), "material_transport": manifest["material_transport"], **evidence(channel)})
            print(output)
            return 0
        env = child_environment(channel)
        stage_families = {channel["cli_model"]: channel["pattern"]}
        expected = channel["configured_model"] if channel["channel"] in ("qwen", "qoder") else None
        executor = "codex" if codex_executor else "claude"
        stage_run, preflight = run_stage, capability
        if channel['channel'] == 'qoder':
            from review_qoder import run_stage as stage_run, capability as preflight
            executor = 'qodercli'
        if codex_executor:
            from functools import partial
            from review_codex import run_stage as codex_run, capability as preflight
            stage_run = partial(codex_run, channel=channel)
            warnings.append("Codex Responses 模型证据仅为 requested_only；actual_model=null，服务端路由身份未证实。")
        claude = shutil.which(executor)
        if not claude:
            raise RunError(executor + " not found on PATH")
        manifest["capabilities"] = preflight(claude, logs, env=env, material_config=mcp_config(initial_registry_path) if initial_registry_path else None)
        if codex_executor:
            warnings.extend(manifest["capabilities"].get("warnings", []))
        manifest["warnings"] = warnings
        manifest["cli_version"] = manifest["capabilities"]["cli_version"]
        warning = compat_warning(manifest["cli_version"], 'qoder' if executor == 'qodercli' else executor)
        if warning:
            warnings.append(warning)
            manifest["warnings"] = warnings
            print("warning: " + warning, file=sys.stderr)
        manifest["stages"] = {}
        write_json(output / "manifest.json", manifest)
        if args.mode == "plan":
            inventory_required = initial_required
            first = stage_run(claude, inventory, initial_prompt, logs / "inventory.jsonl", channel["cli_model"], args.timeout, args.idle_timeout, args.max_output_bytes, stage_families, env=env, expected_model=expected, required=source_labels(initial_sources), registry_path=initial_registry_path)
            require_full_reads(first, inventory_required, initial_sources)
            if snapshot_hashes(inventory) != manifest["snapshots"]["inventory"]:
                raise RunError("inventory snapshot changed during review")
            manifest["stages"]["inventory"] = {k: v for k, v in first.items() if k not in ("result", "complete_reads")}
            manifest["inventory_sha256"] = sha(first["result"].encode("utf-8"))
            (review / "inventory.md").write_text(first["result"], encoding="utf-8")
            manifest["sources"]["review"]["inventory.md"] = chunk_file(review, "inventory.md", len(manifest["sources"]["review"]) + 1)
            manifest["snapshots"]["review"] = snapshot_hashes(review)
            write_json(output / "manifest.json", manifest)
            review_sources = manifest["sources"]["review"]
            review_required = required_files(review_sources)
            review_registry_path = None
            review_registry = None
            if channel['channel'] in ('qwen', 'qoder'):
                review_registry_path = logs / 'review.registry.json'
                review_registry = build_registry(review, review_sources, review_registry_path)
                manifest['material_registries']['review'] = review_registry
                if codex_executor:
                    write_config(channel, review_registry_path, logs / 'review.codex-config.json')
                else:
                    write_json(logs / 'review.mcp.json', mcp_config(review_registry_path))
            review_prompt = make_prompt("plan", review_required, rules, args.scale, review_sources, review_registry)
            manifest["prompt_sha256"]["review.prompt.txt"] = sha(review_prompt.encode("utf-8"))
            write_json(output / "manifest.json", manifest)
            second = stage_run(claude, review, review_prompt, logs / "review.jsonl", channel["cli_model"], args.timeout, args.idle_timeout, args.max_output_bytes, stage_families, env=env, expected_model=expected, required=source_labels(review_sources), registry_path=review_registry_path)
            require_full_reads(second, review_required, review_sources)
            report = "# 独立清单（原样）\n\n" + first["result"] + "\n\n# 方案评审\n\n" + second["result"]
        else:
            required = initial_required
            second = stage_run(claude, review, initial_prompt, logs / "review.jsonl", channel["cli_model"], args.timeout, args.idle_timeout, args.max_output_bytes, stage_families, env=env, expected_model=expected, required=source_labels(initial_sources), registry_path=initial_registry_path)
            require_full_reads(second, required, initial_sources)
            report = second["result"]
        if snapshot_hashes(review) != manifest["snapshots"]["review"]:
            raise RunError("review snapshot changed during review")
        manifest["stages"]["review"] = {k: v for k, v in second.items() if k not in ("result", "complete_reads")}
        read_recoveries = {stage: item["read_recoveries"] for stage, item in manifest["stages"].items()}
        recovery_warnings = [stage + " 阶段已纠正 " + str(len(items)) + " 次 Read 缺文件错误"
                             for stage, items in read_recoveries.items() if items]
        if warnings or recovery_warnings:
            report = "".join("> 警告：" + item + "\n" for item in warnings + recovery_warnings) + "\n" + report
        (output / "report.md").write_text(report, encoding="utf-8")
        manifest.update({"status": "completed", "actual_model": second["model"], "exit_reason": "verified result", "finished_at": time.time()})
        write_json(output / "manifest.json", manifest)
        write_json(output / "status.json", {"status": "completed", "completed": True, "out": str(output),
                                            "warnings": warnings, **evidence(channel), "actual_model": second["model"],
                                            "limits": manifest["limits"], "material_transport": manifest["material_transport"], "read_recoveries": read_recoveries,
                                            "read_recovery_counts": {stage: len(items) for stage, items in read_recoveries.items()}})
        print(output)
        return 0
    except (MaterialError, RunError, OSError, ValueError, TypeError, KeyboardInterrupt) as exc:
        if output:
            if manifest:
                manifest.update({"status": "failed", "exit_reason": str(exc), "finished_at": time.time()})
                write_json(output / "manifest.json", manifest)
            write_json(output / "status.json", {"status": "failed", "completed": False, "error": str(exc),
                                                "failure_kind": getattr(exc, "kind", "preparation_failed"),
                                                "warnings": warnings, **evidence(channel)})
            print(output)
        else:
            print("review preparation failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
