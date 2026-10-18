# 键盘布局上架规范

布局（Layout）是「一套 `xime.custom.yaml` 键盘配置」，用户在 App 的**扩展商店 → 布局**里
一键应用。与方案/插件不同：**本索引只保存引用**，布局包、资源、截图都托管在**作者自己的仓库**，
索引通过不可变 URL + `sha256` 指向它们。

## 一、索引结构

- 源文件：`src/layouts/<id>.yaml`（文件名必须等于条目 `id`）
- 生成产物：`layouts/index.yaml`（由 `scripts/ci-update.py` 自动生成，勿手动编辑）
- 主索引 `index.yaml` 通过 `layouts.from` 引用它

字段说明：

```yaml
id: my_layout                 # 唯一，[a-z0-9_]，与文件名一致
name: 我的布局                 # 显示名称
author: yourname
description: 一段简短说明
tags: [布局]                   # 布局 | 配色 | 快捷键 | 输入法适配
repo: https://github.com/you/my-layout   # 作者仓库（内容来源）
homepage: https://...          # 可选
license: MIT                   # 必填：可再分发声明
appVersion: ">=3.0.0"          # 兼容的 App 版本
requiresSchemes: []            # 依赖的输入方案 id（如 [cangjie]），未安装则置灰
screenshots:                   # 1~5 张，仅详情页展示
  - https://.../01.png
warning: ""                    # 可选：应用前警告
currentVersion: "1.0.0"
versions:                      # 支持历史版本
  - version: "1.0.0"
    date: "2026-09-24"
    changelog: 初始版本
    downloadUrl:
      - url: https://...       # 见下方「交付形态」
        sha256: "..."          # 必填（缺失会被 CI 补全/拒绝）
        size: "12 KB"          # 可选
```

## 二、交付形态（二选一）

### A. 单文件（纯文本布局，无图片/字体）

`downloadUrl.url` 直接指向 `xime.custom.yaml`。**首选 GitHub Release 附件**（发布后不可变、地址永久有效）：

```
https://github.com/<owner>/<repo>/releases/download/<tag>/xime.custom.yaml
```

也可用固定在 tag/commit 上的 raw / jsDelivr（详见「五、URL 与校验」）：

```
https://raw.githubusercontent.com/<owner>/<repo>/<commit>/xime.custom.yaml
https://cdn.jsdelivr.net/gh/<owner>/<repo>@<commit>/xime.custom.yaml
```

### B. zip 包（含背景图/字体等资源）

`downloadUrl.url` 指向 zip；解压后**根目录**必须包含：

```
xime.custom.yaml              # 必填
themes/*.(png|jpg|webp|gif)   # 可选 → 部署到 rime/themes/
fonts/*.(ttf|otf)             # 可选 → 部署到 rime/fonts/
```

要求：

- zip 内**不能有顶层目录壳**（必须是根级 `xime.custom.yaml`，不能是 `my_layout/xime.custom.yaml`）。
- 离开根目录的路径（`..`、绝对路径）会被客户端拒绝。
- 其余文件（README、screenshot 等）不会被安装，可自行携带。

## 三、`xime.custom.yaml` 约定

- **资源引用只用相对路径**：背景图 `themes/x.png`、字体 `fonts/x.ttf`
  （客户端解析到 `files/rime/themes/`、`files/rime/fonts/`）。
  背景图写 `color_schemes.<id>.keyboard_background = { type: image, src: "themes/x.png", fit: cover }`；
  字体写 `keyboard.fonts.key_font = "fonts/x.ttf"`。
- **不要包含** `xime_index`、`style.color_scheme`、`color_schemes` 等与布局无关的样板，
  以免覆盖用户自己的索引地址与当前主题。
- 资源文件名用 `[a-z0-9_-]`，避免中文与空格。

## 四、截图规范

- 数量 1~5 张；竖屏键盘区截图（裁掉状态栏/导航栏）。
- 尺寸宽 ≥ 720px，建议比例 9:16；格式 png/jpg/webp；单张 ≤ 500KB。
- URL 必须可长期访问：放在作者仓库并固定到 **commit/tag**，或用可靠图床；**不要用分支名**（`@main`/`@master`）。
- 截图通常不进 Release 附件，固定到 tag/commit 的 raw / jsDelivr 即可。

## 五、URL 与校验

- 下载地址必须**不可变**，按可靠性排序：
  1. **GitHub Release 附件**（推荐）——附件不可增删改，且带官方 digest；
  2. 固定 **commit** 的 raw / jsDelivr——commit 内容不可变；
  3. 固定 **tag** 的 raw / jsDelivr——tag 可以被移动，次选。
- **禁止**指向分支名（`main`/`master`）——内容会变，`sha256` 随之失效。
- `raw.githubusercontent.com` 在部分网络（如国内）不可达，这类场景请用 Release 附件或 jsDelivr。
- `sha256` 必填；客户端下载后强校验，不匹配直接丢弃。
- 旧版本发布物**不要删除**，索引需要保留历史版本以便回滚。

### 推荐：用 Release 附件交付

在作者仓库放一个 workflow，push tag 时自动创建 Release 并上传 `xime.custom.yaml` 附件：

```yaml
name: Release
on:
  push:
    tags: ["*"]
permissions:
  contents: write
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: sha256sum xime.custom.yaml > xime.custom.yaml.sha256
      - env:
          GH_TOKEN: ${{ github.token }}
        run: |
          tag="${{ github.ref_name }}"
          gh release view "$tag" >/dev/null 2>&1 || gh release create "$tag" --title "$tag" --generate-notes
          gh release upload "$tag" xime.custom.yaml xime.custom.yaml.sha256 --clobber
```

之后发版只需 `git tag 1.0.0 && git push origin 1.0.0`，附件地址即

```
https://github.com/<owner>/<repo>/releases/download/1.0.0/xime.custom.yaml
```

其 sha256 可直接在 Release 页面的附件 digest 处读到（与索引里填的值必须一致）。

## 六、上架流程

1. 在自己的仓库准备好 `xime.custom.yaml`（或 zip 包 + 资源）与截图，发布到**不可变 URL**
   （推荐 Release 附件，见「五、URL 与校验」）。
2. 在 `xime-index` 新建 `src/layouts/<id>.yaml`（见「一、索引结构」）。
3. 本地运行 `python scripts/ci-update.py` 生成 `layouts/index.yaml` 并补全 `sha256`/`size`。
4. 提交 PR；CI 会校验结构、字段合法性与下载链接可达性。
5. 维护者核对 License、截图与内容后再合并。

## 七、用户侧行为

- 应用 = 下载包 → `sha256` 校验 → 解压/写入到 `files/rime/`（`xime.custom.yaml`、`themes/`、`fonts/`）
  → 立即重载键盘生效。
- 同一时刻只有一套布局生效；应用新的会覆盖上一套（应用前会提示，不保留备份）。
- 「恢复默认」会删除 `xime.custom.yaml` 及本布局引入的 `themes/`、`fonts/` 文件。