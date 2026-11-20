# 洛谷题解爬取工具使用说明

本工具用于获取洛谷题解并保存为 Markdown、LaTeX，或两种格式。请遵守洛谷的使用规则，控制请求频率，不要将 Cookie 或其他账号凭据公开。

## 环境要求

- Python 3
- 可访问洛谷的网络连接
- 用于登录校验和访问题解列表的洛谷 Cookie

在项目目录打开 PowerShell，运行：

```powershell
python --version
python luogu_solution_spider.py --help
```

如果系统找不到 `python`，请改用本机 Python 的完整路径运行脚本。

## 配置 Cookie

题解列表可能需要登录后才能访问。请在本机设置 Cookie，不要把真实 Cookie 写进公开文档、代码、命令截图或 Git 提交记录。

### 推荐：使用环境变量

在当前 PowerShell 窗口设置：

```powershell
$env:LG_COOKIE = "_uid=你的_uid; __client_id=你的_client_id"
```

此设置仅对当前 PowerShell 窗口及其启动的程序有效。关闭窗口后需要重新设置。

### 使用本地文件

也可以在脚本所在目录创建 `cookie.txt`，文件中只放一行 Cookie：

```text
_uid=你的_uid; __client_id=你的_client_id
```

请确保该文件不会被提交到 GitHub。可以在仓库的 `.gitignore` 中添加：

```gitignore
cookie.txt
```

如果文件此前已经被 Git 跟踪，仅添加 `.gitignore` 并不会自动取消跟踪；提交前请检查 Git 状态和提交内容。

### 临时通过命令行指定

```powershell
python luogu_solution_spider.py --pid P1000 --cookie "_uid=你的_uid; __client_id=你的_client_id"
```

命令行参数可能留在终端历史记录中。日常使用优先选择环境变量或本地 Cookie 文件。

Cookie 获取方式：登录洛谷后，在浏览器开发者工具的 Application（应用）面板中查看洛谷站点的 Cookies，使用 `_uid` 和 `__client_id`。不要分享 Cookie；它可能允许他人以你的身份访问账户。

## 选择爬取范围

以下三种范围任选一种。

### 指定题号

```powershell
python luogu_solution_spider.py --pid P1000
```

可以一次指定多个题号：

```powershell
python luogu_solution_spider.py --pid P1000 P1001 P17046
```

也可以使用逗号分隔：

```powershell
python luogu_solution_spider.py --pid P1000,P1001,P17046
```

题号大小写不敏感，例如 `p1000` 会被识别为 `P1000`。

### 指定难度

```powershell
python luogu_solution_spider.py --difficulty 2
```

难度编号：

| 编号 | 难度 |
|---|---|
| 1 | 入门 |
| 2 | 普及- |
| 3 | 普及/提高- |
| 4 | 普及+/提高 |
| 5 | 提高+/省选- |
| 6 | 省选/NOI- |
| 7 | NOI/NOI+/CTSC |

可以同时指定多个难度：

```powershell
python luogu_solution_spider.py --difficulty 1 2
```

建议先用 `--page-limit` 小范围试运行。每页包含多少道题以脚本实际行为为准：

```powershell
python luogu_solution_spider.py --difficulty 2 --page-limit 1
```

### 全部题号

```powershell
python luogu_solution_spider.py --all --page-limit 1
```

全量模式可能会检查大量题号并发送许多请求，耗时较长。请先使用较小的 `--page-limit` 验证配置和输出，不要在不了解请求规模时直接运行完整范围。

## 输出格式和目录

默认输出 Markdown。使用 `--format` 选择格式：

```powershell
python luogu_solution_spider.py --pid P1000 --format markdown
python luogu_solution_spider.py --pid P1000 --format latex
python luogu_solution_spider.py --pid P1000 --format both
```

默认输出到当前工作目录下的 `luogu_solutions` 子目录。可以使用 `--output` 指定目录：

```powershell
python luogu_solution_spider.py --pid P1000 --output ".\backup"
```

路径按当前操作系统的格式填写。请勿把包含个人用户名或其他本机隐私的绝对路径写入公开文档。

可选的目录分组方式：

```powershell
python luogu_solution_spider.py --pid P1000 --group-by problem
python luogu_solution_spider.py --difficulty 2 --group-by difficulty
python luogu_solution_spider.py --difficulty 2 --group-by difficulty problem
```

未分组时，文件保存在输出目录中；分组后的目录结构取决于所选分组方式。

## 请求间隔

脚本默认在请求之间随机等待 3 到 10 秒。可以通过参数调整：

```powershell
python luogu_solution_spider.py --pid P1000 --delay-min 3 --delay-max 6
```

请保持合理的请求频率，不要设置过短间隔。过于频繁的请求可能导致访问受限，也会给服务带来不必要的负担。

## 断点续爬与其他选项

默认情况下，已存在的输出文件会跳过，适合中断后使用相同命令继续运行。需要重新生成文件时可使用：

```powershell
python luogu_solution_spider.py --pid P1000 --overwrite
```

其他选项：

| 参数 | 作用 |
|---|---|
| `--overwrite` | 覆盖已存在的输出文件 |
| `--no-login-check` | 跳过启动时的登录校验 |
| `-h`、`--help` | 查看命令行帮助 |

中断后重新运行相同命令即可继续处理；默认跳过已有文件。具体行为以当前脚本版本为准。

## PowerShell 完整示例

下面示例爬取一个难度页，生成 Markdown 和 LaTeX，并按难度分目录：

```powershell
$env:LG_COOKIE = "_uid=你的_uid; __client_id=你的_client_id"
python luogu_solution_spider.py --difficulty 2 --page-limit 1 --format both --group-by difficulty
```

如 PowerShell 中文输出乱码，可在当前窗口设置 UTF-8：

```powershell
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
```

## 常见问题

### 登录校验失败

检查 Cookie 是否完整、是否已过期，以及环境变量或 `cookie.txt` 是否被脚本正确读取。必要时在浏览器重新登录并更新本机 Cookie。

### 某篇题解正文返回 HTTP 403

部分题解正文可能无法访问。若脚本支持使用列表页内容兜底，它可能会改用题解列表中的摘要继续保存；这不代表正文已完整获取。请检查程序输出中的提示。

### 没有题解或题解无法访问

题目可能没有可见题解，或题解当前不可访问。可以在洛谷网页上确认题目和题解状态。

### LaTeX 文件无法编译

生成 `.tex` 文件不等于已在本机编译验证。编译需要安装 TeX Live、MiKTeX 等 LaTeX 环境；还应根据生成内容和使用的宏包排查编译错误。

## 上传到 GitHub 前检查

提交前请确认仓库中不包含：

- `cookie.txt` 或任何包含真实 Cookie 的文件
- 带真实 Cookie 的终端命令、日志、截图或示例
- 个人电脑用户名、隐私目录或其他不希望公开的本机信息
- 未经授权公开的个人数据或受版权保护的内容

建议只提交脚本、使用说明和不含凭据的示例配置。请遵守洛谷的服务条款、版权要求及适用法律；爬取到的题解应仅在获准范围内使用。