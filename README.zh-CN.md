<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo.svg" alt="readwrench" width="320" height="64">
  </picture>
</p>

<p align="center">
  <strong>找回记不清出处的引文，把批注带到修订版上，让你读的书、论文、字体和网页存档始终可信，全程离线。</strong><br>
  一个面向读者和批注者的命令行工具。<code>readwrench check</code> 只读地扫描一遍阅读文件夹，并告诉你每个问题该用哪个命令处理。
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue" alt="License: AGPL-3.0-or-later"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
</p>

<p align="center">
  <a href="README.md">English</a> · 简体中文
</p>

```sh
uvx readwrench check ~/Reading        # 或：pipx install readwrench  /  pip install readwrench
```

## 快速上手

`check` 接受任意文件夹或文件。它按内容（而不是文件名）识别 EPUB、PDF、字体、网页存档（HTML、MHTML、WARC）以及其他命令保存的审阅文件，运行所有无需你做决定的分析，并且不会写入任何文件：

```sh
pipx install 'readwrench[all]'                 # 安装全部命令和全部检查
readwrench check ~/Reading                     # EPUB 脚注、PDF 文字层、过期的引文定位……
readwrench check thesis/ --fail-on warning     # 提交或分享文件夹前的把关
readwrench check . --format markdown           # 生成可贴进笔记或 issue 的摘要
```

在仓库克隆中试用自带示例：

```sh
git clone https://github.com/tianhaogutum/readwrench && cd readwrench
pip install -e '.[all]'
readwrench check examples -v
```

退出码：`0` 表示没有达到 `--fail-on`（默认 `error`）级别的发现，`1` 表示有，`2` 表示用法错误或路径不存在。`--format json|sarif|markdown` 供程序和页面使用；`--only`/`--skip` 选择检查项，`--list-checks` 列出全部检查。某个检查崩溃时会被隔离并单独报告；缺少可选依赖的检查会变成一条 info 提示，附带安装命令。详见 [docs/check.md](docs/check.md)。

## `check` 能发现什么

| 你手上有 | `check` 报告 | 进一步查看或修复 |
| --- | --- | --- |
| **EPUB 电子书** | 没有返回链接的注释引用、失效或有歧义的注释链接；无法导入 concordance 的书（加密、损坏、无法解码） | `footnotes`、`concordance` |
| **PDF** | 没有文字层的文档或页码范围（这些地方永远找不到引文） | `quote` |
| **保存的引文** | 原书或论文已改动、已移动、或提取文本已变化的定位和审阅记录 | `quote verify` |
| **线索索引** | 已损坏，或与建索引时的文本不再一致的索引 | `clues build --replace` |
| **PDF 页面映射和批注审阅包** | 仍是草稿、批准后被修改、或对应 PDF 已变的映射；PDF 或证据图片已变的审阅包 | `pdf-pages`、`reanchor` |
| **字体验证报告** | 被拒绝的子集，以及验证之后子集、原字体或稿件发生的变化 | `subset-proof` |
| **网页存档** | 时间线清单中缺失、无法读取或属于其他网址的存档 | `lineage` |
| **任何文件** | 无法读取、为空或名不副实的文件（例如不是 PDF 的 `.pdf`） | – |

始终需要你来决定的事（找哪句引文、比较哪两个 PDF、字体要覆盖哪份稿件）交给下面的命令；运行过之后，`check` 每次都会重新核验它们保存的文件。

## 全部命令

| | 命令 | 作用 | 文档 |
| --- | --- | --- | --- |
| **检查** | `check` | 只读地扫描阅读文件夹，并为每个问题给出查看或修复的命令 | [check](docs/check.md) |
| **查找与比较** | `quote` | 在本地 TXT、Markdown、HTML、PDF 或 EPUB 中找回引文；逐一审阅每处出现及其上下文，再保存与文件绑定的定位 | [quote](docs/quote.md) |
| | `clues` | 找出同时包含两三句记得的话的资料，每条线索给出原文摘录和彼此间的距离 | [clues](docs/clues.md) |
| | `concordance` | 比较一个概念在三到五本 EPUB 书中的用法：在 Taguette 中逐条审阅，导出概念矩阵 | [concordance](docs/concordance.md) |
| **PDF** | `pdf-pages` | 先匹配两个 PDF 版本的对应页面再比较，插入一页不会让后面全部错位 | [pdf-pages](docs/pdf-pages.md) |
| | `reanchor` | 把旧 PDF 上的高亮和评论迁移到修订版，每一处移动都经你审阅 | [reanchor](docs/reanchor.md) |
| **EPUB 与字体** | `footnotes` | 审阅 EPUB 脚注的跳转路径，逐条确认，生成带精确返回链接的副本 | [footnotes](docs/footnotes.md) |
| | `subset-proof` | 证明子集字体仍能排出实际稿件中的文字和 OpenType 特性 | [subset-proof](docs/subset-proof.md) |
| **网页存档** | `lineage` | 把同一网页的多份本地存档整理成离线的段落时间线 | [lineage](docs/lineage.md) |

运行 `readwrench <命令> --help` 查看参数。输入旧工具名（`quote-locator`、`cluejoin`、`concept-concordance`、`pdf-page-bridge`、`pdf-reanchor`、`epub-footback`、`page-lineage`）会提示新命令；这些工具保存的文件可以继续使用。

## 安装

readwrench 需要 Linux 或 macOS 上的 Python 3.11+。基础安装**没有任何第三方依赖**：`check`、`clues` 和 `lineage` 只用标准库。其他命令各有一个可选依赖组（extra）；未安装时，命令会打印安装它的那一行。

```sh
pipx install readwrench                        # check、clues、lineage
pipx install 'readwrench[footnotes]'           # 再加一个命令（pip install 同理）
pipx install 'readwrench[all]'                 # 全部
```

| Extra | 命令 | 引入的依赖 |
| --- | --- | --- |
| `quote` | `quote` | reanchor、pypdf、EbookLib、Beautiful Soup、lxml、regex、RapidFuzz |
| `concordance` | `concordance` | Taguette 1.5、EbookLib、Beautiful Soup、html5lib、lxml、Jinja2、jsonschema |
| `pdf-pages` | `pdf-pages` | PyMuPDF、Py-PDF-Compare（`export` 需要 Python 3.12+）、Pillow、NumPy、SciPy、RapidFuzz、dhash、ReportLab |
| `reanchor` | `reanchor` | pdfanno 0.2、PyMuPDF、pydantic、RapidFuzz、regex（需要 Python 3.12+） |
| `footnotes` | `footnotes` | lxml |
| `subset-proof` | `subset-proof` | fontTools（含 WOFF/WOFF2）、uharfbuzz、regex |
| `all` | 以上全部 | |

## 原则

- **离线。** 任何命令都不会联网、上传文档或调用模型。
- **由你决定。** 凡是命令提出的建议（哪一处是你要找的引文、哪一页对应哪一页、批注移到哪里、注释从哪里返回），都要经你审阅后才会写入；不会替你确认任何事。
- **不动原件。** 命令只写新文件或新文件夹，除非你明确要求（`--force`、`--replace`）否则不会覆盖；`check` 不写任何文件。
- **绑定到具体文件。** 保存的审阅和定位记录了所描述的书、PDF 或字体的 SHA-256，换了版本会被发现，而不是悄悄错位。

## 文档

- [文档索引](docs/README.md)，每个命令一页（英文）
- [第三方来源与署名](THIRD_PARTY.md)

## 许可证

[AGPL-3.0-or-later](LICENSE)。其中几个命令原本是 MIT 许可的工具，其声明保留在 [licenses/](licenses/) 中，并与所有附带的示例和测试文件一起列在 [THIRD_PARTY.md](THIRD_PARTY.md)。
