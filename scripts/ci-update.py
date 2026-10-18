#!/usr/bin/env python3
"""
CI 生成 / 校验脚本 — 只读 src/，不会修改任何源文件。

用法:
  python scripts/ci-update.py                # 校验 + 生成子索引（src/ 只读）
  python scripts/ci-update.py --verify       # 只校验 sha256，不写任何文件
  python scripts/ci-update.py --no-verify    # 跳过 sha256 校验（离线/快速生成）
  python scripts/ci-update.py --check        # 仅检查是否有缺失的 sha256/size

校验规则：src/ 中记录的 sha256 视为“可信指纹”。远端文件若被替换，sha256 会不一致，
该版本会被整版剔除，不会进入生成的索引。
"""

import glob
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import (
    ROOT, load_yaml, dump_yaml,
    fill_download_urls, fill_archive, fill_files_checksums,
    recalc_model_size,
    should_skip_version,
    check_checksums, prune_bad_versions,
)

# ─── 源码目录 vs 输出目录 ────────────────────────────────
# 源码: src/rimes/*.yaml, src/plugins/v1/*.yaml, src/plugins/v2/*.yaml, src/models/*.yaml, src/layouts/*.yaml
# 输出: rimes/index.yaml, plugins/index.yaml, plugins/v2/index.yaml, models/index.yaml, layouts/index.yaml
SRC_DIR = os.path.join(ROOT, "src")
SKIP_FILES = {"index.yaml"}

# ─── 字段白名单（同 generate_index.py）───────────────────

SCHEMA_FIELDS = [
    "id", "name", "author", "description", "type",
    "tags", "homepage", "license", "dependencies",
    "appVersion", "warning", "currentVersion", "versions",
]
PLUGIN_FIELDS = [
    "id", "name", "author", "description", "type",
    "tags", "pluginType", "homepage", "license",
    "appVersion", "warning", "currentVersion", "versions",
]
# v2 插件索引（JS/QuickJS，宿主 >=3.0.0）：在旧字段上追加新插件系统的元数据
PLUGIN_V2_FIELDS = [
    "id", "name", "author", "description", "type",
    "tags", "pluginType", "icon", "activation",
    "minHostVersion", "platforms", "capabilities", "network",
    "homepage", "license",
    "appVersion", "warning", "currentVersion", "versions",
]
MODEL_FIELDS = [
    "id", "name", "author", "description", "category", "size",
    "type", "tags", "homepage", "license", "appVersion", "warning",
    "currentVersion", "versions",
]
LAYOUT_FIELDS = [
    "id", "name", "author", "description", "tags",
    "repo", "homepage", "license", "appVersion", "requiresSchemes",
    "screenshots", "warning", "currentVersion", "versions",
]


# ─── 检查是否有缺失 ──────────────────────────────────────


def check():
    any_needed = False
    for subdir in ("rimes", "plugins/v1", "plugins/v2", "models", "layouts"):
        src_dir = os.path.join(SRC_DIR, subdir)
        for fpath in sorted(glob.glob(os.path.join(src_dir, "*.yaml"))):
            basename = os.path.basename(fpath)
            data = load_yaml(fpath)
            if not data:
                continue
            eid = data.get("id", "?")
            for v in data.get("versions", []):
                ver = v.get("version", "?")
                if should_skip_version(ver):
                    continue
                for dl in v.get("downloadUrl", []):
                    url = dl.get("url", "")
                    if url and (not dl.get("sha256") or not dl.get("size")):
                        if url.endswith(".gram"):
                            continue  # .gram 文件不检查 sha256
                        print(f"  ⚠ src/{subdir}/{basename} ({eid} v{ver}): 缺 sha256/size")
                        any_needed = True
                if "archive" in v:
                    a = v["archive"]
                    if a.get("url") and (not a.get("sha256") or not a.get("size")):
                        print(f"  ⚠ src/{subdir}/{basename} ({eid} v{ver} archive): 缺 sha256/size")
                        any_needed = True
                for fi, f_item in enumerate(v.get("files", [])):
                    if f_item.get("url") and (not f_item.get("sha256") or not f_item.get("size")):
                        print(f"  ⚠ src/{subdir}/{basename} ({eid} v{ver} files[{fi}]): 缺 sha256/size")
                        any_needed = True
    if any_needed:
        sys.exit(1)


# ─── 补合并写回源文件 ──────────────────────────────────

# 输出子索引文件头
HEADERS = {
    "rimes": "# Xime 输入方案子索引\n# ⚠️ 此文件由 scripts/ci-update.py 自动生成，请勿手动编辑\n",
    "plugins": "# Xime 插件子索引（旧版 Lua/DEX，兼容旧版 App）\n# ⚠️ 此文件由 scripts/ci-update.py 自动生成，请勿手动编辑\n",
    "plugins/v2": "# Xime v2 插件子索引（JS/QuickJS 插件，宿主 >=3.0.0）\n# ⚠️ 此文件由 scripts/ci-update.py 自动生成，请勿手动编辑\n",
    "models": "# Xime 模型子索引\n# ⚠️ 此文件由 scripts/ci-update.py 自动生成，请勿手动编辑\n",
    "layouts": "# Xime 键盘布局子索引\n# ⚠️ 此文件由 scripts/ci-update.py 自动生成，请勿手动编辑\n",
}


def update_source(src_subdir, out_subdir, key, fields, filler, header, index_version=1, verify=True):
    """从 src/<src_subdir>/ 读源码 → 校验 sha256 → 补全 checksum → 输出 <out_subdir>/index.yaml。

    任何情况下都不修改 src/。校验不通过的版本会整版剔除，不进入索引。

    src 与 out 显式分离，便于归档：
      - src/plugins/v1 → plugins/index.yaml    旧版稳定 URL（旧 App 硬编码，不可改动）
      - src/plugins/v2 → plugins/v2/index.yaml v2 新索引
    """
    from datetime import date
    today = date.today().isoformat()

    src_dir = os.path.join(SRC_DIR, src_subdir)
    out_dir = os.path.join(ROOT, out_subdir)

    entries = []
    dropped_versions = 0
    unverifiable = 0
    for fpath in sorted(glob.glob(os.path.join(src_dir, "*.yaml"))):
        basename = os.path.basename(fpath)
        data = load_yaml(fpath)
        if not data:
            continue
        eid = os.path.splitext(basename)[0]
        if data.get("id") != eid:
            print(f"  ⚠ {basename}: id 不匹配，跳过")
            continue

        print(f"  📄 src/{src_subdir}/{basename}")
        filled = filler(data)  # 只补全内存数据，不写回 src/

        if verify:
            report = check_checksums(data)
            unverifiable += len(report["unverifiable"])
            for url in report["unverifiable"]:
                print(f"  ❔ 未记录 sha256，无法校验: {url[3]}")
            for vi, ver, kind, url, expected, actual, size_h in report["mismatch"]:
                print(f"  ⛔ {url}")
                print(f"     记录 sha256: {expected}")
                print(f"     实际 sha256: {actual}  ({size_h})")
            for vi, ver, kind, url, reason in report["error"]:
                print(f"  ⚠ {url} → {reason}（本次无法校验，仍会上架）")
            filled, _ = prune_bad_versions(filled, f"src/{src_subdir}/{basename}")
            dropped_versions += len(report["bad_versions"])

        if not filled.get("versions"):
            print(f"  🚫 {basename}: 所有版本均校验失败，条目已跳过")
            continue

        entry = {f: filled[f] for f in fields if f in filled and filled[f] is not None}
        entries.append(entry)

    # type: built-in 优先排在前面，其余按 id 排序
    entries.sort(
        key=lambda e: (
            e.get("type") != "built-in",
            e.get("id", ""),
        )
    )

    os.makedirs(out_dir, exist_ok=True)
    index = {"index_version": index_version, "updated_at": today, key: entries}
    yaml_str = yaml.dump(index, allow_unicode=True, default_flow_style=False, sort_keys=False)
    index_path = os.path.join(out_dir, "index.yaml")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(header + yaml_str)
    print(f"  ✓ 已重新生成 {out_subdir}/index.yaml ({len(entries)} 条目)")


def update(verify=True):
    print("=" * 50)
    print("🔄 校验 sha256 并生成子索引（src/ 只读）")
    print("=" * 50)
    if not verify:
        print("⚠ 已跳过 sha256 校验（--no-verify）")

    update_source("rimes", "rimes", "schemas", SCHEMA_FIELDS, fill_download_urls, HEADERS["rimes"], verify=verify)
    print()
    # 旧版插件：源归档在 src/plugins/v1，发布到旧 App 硬编码读取的 plugins/index.yaml
    update_source("plugins/v1", "plugins", "plugins", PLUGIN_FIELDS, fill_download_urls, HEADERS["plugins"], verify=verify)
    print()
    # v2 插件：源在 src/plugins/v2，发布到 plugins/v2/index.yaml
    update_source(
        "plugins/v2", "plugins/v2", "plugins", PLUGIN_V2_FIELDS, fill_download_urls,
        HEADERS["plugins/v2"], index_version=2, verify=verify,
    )
    print()
    update_source(
        "models", "models", "models", MODEL_FIELDS,
        lambda d: recalc_model_size(fill_files_checksums(fill_archive(d))), HEADERS["models"],
        verify=verify,
    )
    print()
    update_source("layouts", "layouts", "layouts", LAYOUT_FIELDS, fill_download_urls, HEADERS["layouts"], verify=verify)

    print(f"\n{'=' * 50}")
    print("✅ 子索引已重新生成（src/ 未被修改）")
    print(f"{'=' * 50}")


# ─── 只校验：检测远端文件是否被替换 ───────────────────────


def verify():
    print("=" * 50)
    print("🔍 校验 sha256 — 检测远端文件是否被改动")
    print("=" * 50)

    checked = 0
    mismatches = []
    errors = []
    unverifiable = []

    for subdir in ("rimes", "plugins/v1", "plugins/v2", "models", "layouts"):
        src_dir = os.path.join(SRC_DIR, subdir)
        for fpath in sorted(glob.glob(os.path.join(src_dir, "*.yaml"))):
            data = load_yaml(fpath)
            if not data:
                continue
            report = check_checksums(data)
            checked += report["checked"]
            mismatches += [(f"src/{subdir}/{os.path.basename(fpath)}",) + tuple(m) for m in report["mismatch"]]
            errors += [(f"src/{subdir}/{os.path.basename(fpath)}",) + tuple(e) for e in report["error"]]
            unverifiable += [(f"src/{subdir}/{os.path.basename(fpath)}",) + tuple(u) for u in report["unverifiable"]]
            for vi, ver, kind, url, expected_bytes, actual_bytes in report["size_warn"]:
                print(f"  ⚠ {os.path.basename(fpath)} v{ver} {kind}: sizeBytes 记录 {expected_bytes} ≠ 实际 {actual_bytes}")

    if mismatches:
        print(f"\n⛔ 发现 {len(mismatches)} 个文件与记录的 sha256 不一致（内容已被替换/改动）：")
        for path, vi, ver, kind, url, expected, actual, size_h in mismatches:
            print(f"  - {path} v{ver} {kind}")
            print(f"    url      : {url}")
            print(f"    记录值   : {expected}")
            print(f"    实际值   : {actual}  ({size_h})")
        print("\n这些版本不会进入索引，请确认上游内容，或在 src/ 中更新 sha256 并重新发版。")

    if errors:
        print(f"\n⚠ 有 {len(errors)} 个文件下载失败，本次无法校验：")
        for path, vi, ver, kind, url, reason in errors:
            print(f"  - {path} v{ver} {kind}: {url} → {reason}")

    if unverifiable:
        print(f"\n❔ 有 {len(unverifiable)} 个链接未记录 sha256，无法校验（建议补上 sha256 或改用不可变 URL）")

    print(f"\n{'=' * 50}")
    print(f"已校验 {checked} 个文件，sha256 不一致 {len(mismatches)} 个，下载失败 {len(errors)} 个，无法校验 {len(unverifiable)} 个")
    print(f"{'=' * 50}")

    if mismatches or errors:
        sys.exit(1)


def main():
    if "--verify" in sys.argv:
        verify()
    elif "--check" in sys.argv:
        check()
    else:
        update(verify="--no-verify" not in sys.argv)


if __name__ == "__main__":
    main()
