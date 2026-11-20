#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
洛谷题解爬取工具
================================================================
爬取指定题号 / 指定难度 / 全部题目的题解，保存为
    luogu_<题号>_<题解编号>_<题解编写人>.<md|tex>

用法示例
----------------------------------------------------------------
    # 指定题号
    python luogu_solution_spider.py --pid P1000 P1001

    # 指定难度（1=入门 2=普及- 3=普及/提高- 4=普及+/提高
    #           5=提高+/省选- 6=省选/NOI- 7=NOI/NOI+/CTSC）
    python luogu_solution_spider.py --difficulty 2

    # 全部题目（P1000~P9999，极慢，慎用）
    python luogu_solution_spider.py --all --page-limit 3

    # 只要 LaTeX
    python luogu_solution_spider.py --pid P1000 --format latex

输出
----------------------------------------------------------------
    --format markdown   luogu_P1000_abcde_username.md   原样 Markdown
    --format latex      luogu_P1000_abcde_username.tex  完整 LaTeX 转义
    --format both       两种都生成

Cookie
----------------------------------------------------------------
    洛谷题解列表需要登录。三种提供方式（优先级从高到低）：
      1. 命令行  --cookie "_uid=xxx; __client_id=yyy"
      2. 环境变量 LG_COOKIE
      3. 脚本同目录 cookie.txt（跳过 # 开头的注释行）
    C3VK 不必提供，脚本自动续期。
"""

from __future__ import annotations

import argparse
import gzip
import html
import http.client
import json
import os
import random
import re
import sys
import time
import urllib.parse
import zlib

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

HOST = "www.luogu.com.cn"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:160.0) "
    "Gecko/20100101 Firefox/160.0"
)

# 洛谷八档难度（内部数值 → 中文名）
DIFFICULTY_NAMES = {
    0: "暂无评定",
    1: "入门",
    2: "普及-",
    3: "普及/提高-",
    4: "普及+/提高",
    5: "提高+/省选-",
    6: "省选/NOI-",
    7: "NOI/NOI+/CTSC",
}

# 题解列表每页条数（服务端固定为 10）
SOLUTION_PER_PAGE = 10

# 题目列表每页条数（服务端固定为 50）
PROBLEM_PER_PAGE = 50

# 每次请求之间的随机等待区间（秒）
DELAY_MIN = 3.0
DELAY_MAX = 10.0

# 登录校验用的探针题号（P1000 A+B Problem，题解很多，一定有数据）
PROBE_PID = "P1000"


# ----------------------------------------------------------------------------
# HTTP 会话（手动维护 Cookie，用于通过洛谷的 C3VK 反爬重定向）
# ----------------------------------------------------------------------------


class HttpSession:
    """极简 HTTP 会话。

    洛谷对无 Cookie 的请求会返回 302，并附带 Set-Cookie: C3VK=...，然后重定向
    到同一个 URL。因此必须自己维护 Cookie jar，否则会陷入无限重定向。
    """

    def __init__(self, cookie_text: str = "", timeout: int = 30):
        self.cookies: dict[str, str] = {}
        self.timeout = timeout
        if cookie_text:
            self.add_cookie_string(cookie_text)

    def add_cookie_string(self, cookie_text: str) -> None:
        for part in cookie_text.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name, value = name.strip(), value.strip()
            if name:
                self.cookies[name] = value

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def get(self, path: str, extra_headers: dict[str, str] | None = None):
        """GET 请求，自动跟随重定向并累积 Cookie。"""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if extra_headers:
            headers.update(extra_headers)

        url = path
        for _hop in range(8):
            if self.cookies:
                headers["Cookie"] = self.cookie_header()
            conn = http.client.HTTPSConnection(HOST, timeout=self.timeout)
            try:
                conn.request("GET", url, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
                status = resp.status
                encoding = (resp.getheader("Content-Encoding") or "").lower()
                location = resp.getheader("Location")
                set_cookies = [
                    v for k, v in resp.getheaders() if k.lower() == "set-cookie"
                ]
            finally:
                conn.close()

            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except zlib.error:
                    raw = zlib.decompress(raw)

            for item in set_cookies:
                pair = item.split(";", 1)[0]
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    self.cookies[name.strip()] = value.strip()

            if status in (301, 302, 303, 307, 308) and location:
                parts = urllib.parse.urlsplit(location)
                url = parts.path or "/"
                if parts.query:
                    url += "?" + parts.query
                continue

            return status, raw.decode("utf-8", "replace")

        return 0, ""


def extract_lentille(html_text: str) -> dict | None:
    """从页面 HTML 里抠出 lentille-context 的 JSON。"""
    match = re.search(
        r'<script\s+id="lentille-context"\s+type="application/json">(.*?)</script>',
        html_text,
        re.S,
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------------
# 洛谷接口封装
# ----------------------------------------------------------------------------


class LuoguClient:
    def __init__(self, session: HttpSession, delay_min: float, delay_max: float):
        self.session = session
        self.delay_min = delay_min
        self.delay_max = delay_max
        self._last_request_at = 0.0
        self.request_count = 0

    # -- 节流 ---------------------------------------------------------------

    def _throttle(self) -> None:
        if self._last_request_at > 0:
            target = random.uniform(self.delay_min, self.delay_max)
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < target:
                time.sleep(target - elapsed)
        self._last_request_at = time.monotonic()

    def _get_json(
        self, path: str, what: str, quiet_status: tuple[int, ...] = ()
    ) -> dict | None:
        self._throttle()
        self.request_count += 1
        status, body = self.session.get(path)
        if status != 200:
            if status not in quiet_status:
                print(f"  [!] {what} 返回 HTTP {status}", file=sys.stderr)
            else:
                print(f"  [i] {what} 返回 HTTP {status}")
            return None
        context = extract_lentille(body)
        if context is None:
            print(f"  [!] {what} 页面里没有 lentille-context", file=sys.stderr)
            return None
        data = context.get("data") or {}
        if isinstance(data.get("errorCode"), int) and data.get("errorCode"):
            message = data.get("errorMessage") or ""
            print(
                f"  [!] {what} 接口报错 {data.get('errorCode')}: {message}",
                file=sys.stderr,
            )
            return None
        return data

    # -- 校验登录 ----------------------------------------------------------

    def check_login(self) -> str | None:
        """校验登录态。

        直接打题解列表接口：未登录时它返回 errorCode=401，
        登录后返回 data.solutions。这是最可靠的判断依据，
        因为题目页的顶层 user 字段在部分版本里不再下发。
        """
        self._throttle()
        self.request_count += 1
        status, body = self.session.get(f"/problem/solution/{PROBE_PID}")
        if status != 200:
            print(f"  [!] 登录校验返回 HTTP {status}", file=sys.stderr)
            return None
        context = extract_lentille(body)
        if context is None:
            print("  [!] 登录校验页面里没有 lentille-context", file=sys.stderr)
            return None
        data = context.get("data") or {}
        if data.get("errorCode"):
            message = data.get("errorMessage") or ""
            if message:
                print(f"  [!] 登录校验：{message}", file=sys.stderr)
            return None
        solutions = data.get("solutions")
        if isinstance(solutions, dict):
            return "已登录"
        return None

    # -- 题目列表（按难度） ------------------------------------------------

    def list_problems(self, difficulty: int, page_limit: int | None = None):
        """枚举某个难度下的全部题号。"""
        page = 1
        seen: list[str] = []
        total = None
        while True:
            path = (
                f"/problem/list?difficulty={difficulty}&page={page}&_contentOnly=1"
            )
            data = self._get_json(path, f"题目列表 difficulty={difficulty} 第 {page} 页")
            if data is None:
                break
            problems = data.get("problems") or {}
            if total is None:
                total = int(problems.get("count") or 0)
                pages = (total + PROBLEM_PER_PAGE - 1) // PROBLEM_PER_PAGE
                print(
                    f"  难度 {difficulty}（{DIFFICULTY_NAMES.get(difficulty, '?')}）"
                    f"共 {total} 道题，{pages} 页列表"
                )
            result = problems.get("result") or []
            if not result:
                break
            for item in result:
                pid = item.get("pid")
                if pid:
                    seen.append(str(pid))
            if page_limit is not None and page >= page_limit:
                break
            if total is not None and page * PROBLEM_PER_PAGE >= total:
                break
            page += 1
        return seen

    # -- 题解列表 ----------------------------------------------------------

    def list_solutions(self, pid: str):
        """返回 (题解摘要列表, 该题题解总数, 题目元信息)。"""
        page = 1
        items: list[dict] = []
        total = 0
        problem = {}
        while True:
            path = f"/problem/solution/{pid}?page={page}"
            data = self._get_json(path, f"{pid} 题解列表第 {page} 页")
            if data is None:
                break
            solutions = data.get("solutions") or {}
            if page == 1:
                total = int(solutions.get("count") or 0)
                problem = data.get("problem") or {}
            result = solutions.get("result") or []
            if not result:
                break
            items.extend(result)
            if len(items) >= total or len(result) < SOLUTION_PER_PAGE:
                break
            page += 1
        return items, total, problem

    def get_problem_difficulty(self, pid: str) -> int | None:
        """单独查一道题的难度（0~7），查不到返回 None。"""
        data = self._get_json(f"/problem/{pid}", f"{pid} 题目页")
        if data is None:
            return None
        problem = data.get("problem") or {}
        value = problem.get("difficulty")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # -- 题解正文 ----------------------------------------------------------

    def fetch_article(self, lid: str, fallback: str = "") -> tuple[str | None, str]:
        """抓取单篇题解的完整 Markdown 正文。

        返回 (正文, 来源标记)，来源为 "article" 或 "fallback"。
        403 常见于作者被封禁的题解，此时退回列表里带的 content。
        """
        data = self._get_json(
            f"/article/{lid}", f"题解正文 {lid}", quiet_status=(403,)
        )
        if data is None:
            if fallback:
                print(f"        改用列表页摘录内容（{len(fallback)} 字符）")
                return fallback, "fallback"
            return None, ""
        article = data.get("article") or {}
        content = article.get("content")
        if not content:
            if fallback:
                print(f"        正文为空，改用列表页摘录内容（{len(fallback)} 字符）")
                return fallback, "fallback"
            return None, ""
        # contentFull=False 表示服务端只给了预览，此时也优先用列表里的备份
        if article.get("contentFull") is False and fallback:
            print("        正文不完整，改用列表页摘录内容")
            return fallback, "fallback"
        return str(content), "article"


# ----------------------------------------------------------------------------
# LaTeX 完整转义
# ----------------------------------------------------------------------------

# 反斜杠必须最先替换，否则会把后面插入的命令再转义一遍
_LATEX_ESCAPES = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
    ("<", r"\textless{}"),
    (">", r"\textgreater{}"),
    ("|", r"\textbar{}"),
]


def escape_latex(text: str) -> str:
    """把任意文本转成可以直接插进 LaTeX 正文的安全文本。

    先把待插入的控制序列（占位符）挖出来，转义完再放回去，
    否则反斜杠会被二次转义成 \\textbackslash{}。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")

    stash: list[str] = []

    def stash_command(match: re.Match) -> str:
        stash.append(match.group(0))
        return f"\x01{len(stash) - 1}\x01"

    # 已经生成好的 LaTeX 命令：\textbf{...} \textit{...} \sout{...} 等
    text = re.sub(r"\\[A-Za-z]+\*?(\{[^{}]*\})*", stash_command, text)

    for raw, escaped in _LATEX_ESCAPES:
        text = text.replace(raw, escaped)

    def restore_command(match: re.Match) -> str:
        return stash[int(match.group(1))]

    return re.sub(r"\x01(\d+)\x01", restore_command, text)


_LATEX_VERBATIM_DELIMITERS = ["%##%", "&@@&", "!~~!", "|^^|", "?$$?", "#**#"]

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")


def _pick_delimiter(text: str) -> str:
    for candidate in _LATEX_VERBATIM_DELIMITERS:
        if candidate not in text:
            return candidate
    raise RuntimeError("找不到可用的 verbatim 分隔符")


def _verbatim(text: str) -> str:
    delimiter = _pick_delimiter(text)
    return f"\\verb{delimiter}{text}{delimiter}"


def markdown_to_latex(markdown_text: str) -> str:
    """把洛谷的 Markdown 题解转成工整的 LaTeX 文档。

    转换策略：
      * 代码围栏  -> verbatim 环境（内部内容原样保留，只处理了字体）
      * 行内代码  -> \\verb
      * 标题      -> \\section / \\subsection / \\subsubsection
      * 加粗斜体  -> \\textbf / \\textit
      * 链接图片  -> \\href / \\url
      * 数学公式  -> 原样保留（不做转义）
      * 其余文字  -> 全量 LaTeX 转义
    """
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    out: list[str] = []
    body: list[str] = []

    in_code = False
    code_lines: list[str] = []
    in_math_block = False
    math_lines: list[str] = []
    list_depth = 0
    in_quote = False

    def flush_code() -> None:
        nonlocal code_lines
        out.append("\\begin{verbatim}")
        out.extend(code_lines)
        out.append("\\end{verbatim}")
        code_lines = []

    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence and not in_math_block:
            if not in_code:
                in_code = True
                code_lines = []
            else:
                in_code = False
                flush_code()
            continue

        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()

        # 数学块 $$ ... $$
        if stripped.startswith("$$"):
            if not in_math_block and stripped == "$$":
                in_math_block = True
                math_lines = []
                continue
            if in_math_block and stripped == "$$":
                out.append("\\[")
                out.extend(math_lines)
                out.append("\\]")
                in_math_block = False
                math_lines = []
                continue
            if not in_math_block and len(stripped) > 4 and stripped.endswith("$$"):
                inner = stripped[2:-2]
                out.append("\\[" + inner + "\\]")
                continue

        if in_math_block:
            math_lines.append(line)
            continue

        if not stripped:
            out.append("")
            continue

        # 引用块
        if stripped.startswith(">"):
            out.append("% 引用：" + stripped.lstrip("> ").strip())
            continue

        # 标题
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            title = _inline_to_latex(heading.group(2).strip())
            # 原文可能本身就是「拉取到的一级标题」，最高只降到 subsection，避免重复
            command = {1: "subsection", 2: "subsection", 3: "subsection"}.get(
                level, "subsubsection"
            )
            out.append(f"\\{command}*{{{title}}}")
            continue

        # 分隔线
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            out.append("\\par\\noindent\\rule{\\linewidth}{0.4pt}")
            continue

        # 有序 / 无序列表
        ordered = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
        unordered = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        item_text = None
        indent = 0
        if ordered is not None:
            indent = len(ordered.group(1))
            item_text = ordered.group(3)
        elif unordered is not None:
            indent = len(unordered.group(1))
            item_text = unordered.group(2)
        if item_text is not None:
            depth = indent // 2 + 1
            while list_depth < depth:
                out.append("\\begin{itemize}")
                list_depth += 1
            while list_depth > depth:
                out.append("\\end{itemize}")
                list_depth -= 1
            out.append("  \\item " + _inline_to_latex(item_text.strip()))
            continue

        while list_depth > 0:
            out.append("\\end{itemize}")
            list_depth -= 1

        out.append(_inline_to_latex(stripped) + "\\par")

    if in_code:
        flush_code()
    if in_math_block:
        out.append("\\[")
        out.extend(math_lines)
        out.append("\\]")
    while list_depth > 0:
        out.append("\\end{itemize}")
        list_depth -= 1

    body_text = "\n".join(out)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

    return body_text


_MATH_SPAN_RE = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+?\$)", re.S)


def _inline_to_latex(text: str) -> str:
    """处理行内元素（数学公式、行内代码、加粗斜体、链接）并转义其余部分。"""
    # 先把数学公式和行内代码挖出来占位，避免被转义
    stash: list[str] = []

    def stash_math(match: re.Match) -> str:
        stash.append(match.group(0))
        return f"\x00{len(stash) - 1}\x00"

    text = _MATH_SPAN_RE.sub(stash_math, text)

    def stash_code(match: re.Match) -> str:
        stash.append(_verbatim(match.group(1)))
        return f"\x00{len(stash) - 1}\x00"

    text = _INLINE_CODE_RE.sub(stash_code, text)

    # 图片 ![alt](url) —— 必须排在链接之前，否则 ! 会被留在外面
    def repl_image(match: re.Match) -> str:
        alt, url = match.group(1), match.group(2)
        note = alt.strip() or "无描述"
        stash.append(f"\\textit{{[图片: {escape_latex(note)}]}}")
        return f"\x00{len(stash) - 1}\x00"

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", repl_image, text)

    # 链接 [text](url)
    def repl_link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        stash.append(f"\\href{{{escape_latex(url)}}}{{{label}}}")
        return f"\x00{len(stash) - 1}\x00"

    text = re.sub(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)", repl_link, text)

    # 加粗 / 斜体 / 删除线
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\\textbf{\\textit{\1}}", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\\textit{\1}", text)
    text = re.sub(r"__(.+?)__", r"\\textbf{\1}", text)
    text = re.sub(r"~~(.+?)~~", r"\\sout{\1}", text)

    # 剩下的全部转义
    text = escape_latex(text)

    # 把占位符换成真内容
    def restore(match: re.Match) -> str:
        return stash[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", restore, text)


LATEX_PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{amsmath,amssymb}
\usepackage{hyperref}
\usepackage{ulem}
\usepackage{geometry}
\geometry{margin=2.5cm}
\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.55em}
\renewcommand{\arraystretch}{1.2}
"""


def build_latex_document(meta: dict, markdown_text: str) -> str:
    """把元信息 + Markdown 正文组装成完整的 .tex 文件内容。"""
    title = escape_latex(meta.get("title") or "")
    pid = escape_latex(meta.get("pid") or "")
    problem_name = escape_latex(meta.get("problem_name") or "")
    author = escape_latex(meta.get("author") or "")
    lid = escape_latex(meta.get("lid") or "")
    url = escape_latex(meta.get("url") or "")
    published = escape_latex(meta.get("time_text") or "")

    header = "\n".join(
        [
            r"\begin{center}",
            r"{\Large\bfseries " + title + r"}\\[0.6em]",
            r"\begin{tabular}{ll}",
            r"题目 & " + f"{pid} {problem_name}" + r" \\",
            r"作者 & " + author + r" \\",
            r"题解编号 & " + lid + r" \\",
            r"发布时间 & " + published + r" \\",
            r"原链接 & \url{" + url + r"} \\",
            r"\end{tabular}",
            r"\end{center}",
            r"\hrule",
            "",
        ]
    )

    return LATEX_PREAMBLE + "\n\\begin{document}\n\n" + header + "\n" + markdown_text + "\n\n\\end{document}\n"


# ----------------------------------------------------------------------------
# 文件名与落盘
# ----------------------------------------------------------------------------


_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def safe_filename_part(text: str, fallback: str = "unknown", max_len: int = 60) -> str:
    """把任意字符串变成合法的文件名片段（保留中文）。"""
    cleaned = _ILLEGAL_CHARS.sub("_", text).strip().strip(".")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def build_basename(pid: str, lid: str, author: str) -> str:
    """luogu_<题号>_<题解编号>_<题解编写人>"""
    return (
        f"luogu_{safe_filename_part(pid, 'unknown')}"
        f"_{safe_filename_part(lid, 'unknown')}"
        f"_{safe_filename_part(author, 'anonymous')}"
    )


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def format_time(timestamp) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(timestamp)))
    except (TypeError, ValueError, OSError):
        return ""


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------


def resolve_cookie(cli_cookie: str | None) -> str:
    if cli_cookie:
        return cli_cookie.strip()
    env_cookie = os.environ.get("LG_COOKIE", "").strip()
    if env_cookie:
        return env_cookie
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt"),
        os.path.join(os.getcwd(), "cookie.txt"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        return line
        except OSError:
            continue
    return ""


def difficulty_dir_name(difficulty: int) -> str:
    """把难度数值变成子目录名，例如 2 -> 普及-。"""
    name = DIFFICULTY_NAMES.get(difficulty)
    if not name:
        return f"难度{difficulty}"
    return safe_filename_part(name, f"难度{difficulty}")


def build_target_dir(
    client: LuoguClient, pid: str, args, problem: dict, output_dir: str
) -> str:
    """根据 --group-by 计算一道题的输出目录，并确保目录存在。

    group_by 是列表，可以只含一个模式，也可以同时含 difficulty 和 problem。
    同时有时难度层在外、题号层在内，即 输出/普及-/P17046/。
    """
    modes = getattr(args, "group_by", None) or ["none"]
    if "none" in modes:
        return output_dir

    parts: list[str] = []

    if "difficulty" in modes:
        known = getattr(args, "pid_difficulty", {}).get(pid)
        difficulty = resolve_problem_difficulty(client, pid, known)
        if difficulty is None and problem.get("difficulty") is not None:
            try:
                difficulty = int(problem["difficulty"])
            except (TypeError, ValueError):
                difficulty = None
        if difficulty is None:
            parts.append("未知难度")
        else:
            parts.append(difficulty_dir_name(difficulty))

    if "problem" in modes:
        parts.append(safe_filename_part(pid, "题目"))

    target_dir = os.path.join(output_dir, *parts) if parts else output_dir
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def collect_targets(args, client: LuoguClient) -> list[str]:
    """根据命令行参数决定要爬哪些题号。

    同时记录每个题号来自哪个难度（来自 --pid 的记为 None），
    供 --group-by 分组输出使用。
    """
    targets: list[str] = []
    seen: set[str] = set()
    pid_difficulty: dict[str, int | None] = {}

    def add(pid: str, difficulty: int | None = None) -> None:
        pid = str(pid).strip()
        if not pid:
            return
        if pid not in seen:
            seen.add(pid)
            targets.append(pid)
        # 优先保留已知难度，避免先 --pid 后 --difficulty 时丢失分组信息
        if difficulty is not None and pid_difficulty.get(pid) is None:
            pid_difficulty[pid] = difficulty

    if args.pid:
        for item in args.pid:
            for piece in re.split(r"[,;\s]+", item):
                if piece:
                    add(piece.upper())

    if args.difficulty:
        for difficulty in args.difficulty:
            print(f"正在枚举难度 {difficulty} 的题目列表…")
            for pid in client.list_problems(difficulty, args.page_limit):
                add(pid, difficulty)

    if args.all:
        print("正在枚举全部题号 P1000~P9999…")
        start, end = 1000, 9999
        if args.page_limit:
            print(f"  （--page-limit 生效，仅枚举前 {args.page_limit} 页）")
            for pid in client.list_problems(0, args.page_limit):
                add(pid)
        else:
            for number in range(start, end + 1):
                add(f"P{number}")

    args.pid_difficulty = pid_difficulty
    return targets


def resolve_problem_difficulty(
    client: LuoguClient, pid: str, known: int | None
) -> int | None:
    """确定一道题的输出分组难度。

    优先级：命令行枚举时已知的难度 > 题解列表里的 solutionFor.difficulty
    """
    if known is not None:
        return known
    data = client.get_problem_difficulty(pid)
    return data


def crawl_problem(
    client: LuoguClient,
    pid: str,
    args,
    output_dir: str,
    stats: dict,
) -> None:
    print(f"\n=== {pid} ===")
    items, total, problem = client.list_solutions(pid)
    if not items:
        print(f"  {pid}：没有题解，或不开放题解")
        stats["no_solution_problems"] += 1
        return

    problem_name = str(problem.get("name") or "")

    target_dir = build_target_dir(client, pid, args, problem, output_dir)

    print(f"  题目：{problem_name}  题解共 {total} 篇")
    if target_dir != output_dir:
        print(f"  输出：{os.path.abspath(target_dir)}")

    for index, item in enumerate(items, 1):
        lid = str(item.get("lid") or "")
        author_info = item.get("author") or {}
        author = str(author_info.get("name") or "anonymous")
        title = str(item.get("title") or "")

        basename = build_basename(pid, lid, author)
        md_path = os.path.join(target_dir, basename + ".md")
        tex_path = os.path.join(target_dir, basename + ".tex")

        need_md = args.format in ("markdown", "both")
        need_tex = args.format in ("latex", "both")

        already = (need_md and os.path.isfile(md_path)) or (
            need_tex and os.path.isfile(tex_path)
        )
        if already and not args.overwrite:
            print(f"  [{index}/{len(items)}] {basename} 已存在，跳过")
            stats["skipped"] += 1
            continue

        content, source = client.fetch_article(
            lid, fallback=str(item.get("content") or "")
        )
        if not content:
            print(f"  [{index}/{len(items)}] {basename} 正文抓取失败")
            stats["failed"] += 1
            continue
        if source == "fallback":
            stats["fallback"] += 1

        meta = {
            "pid": pid,
            "lid": lid,
            "title": title,
            "author": author,
            "problem_name": problem_name,
            "url": f"https://www.luogu.com.cn/article/{lid}",
            "time_text": format_time(item.get("time")),
        }

        if need_md:
            write_text(md_path, content.rstrip() + "\n")
            stats["written_md"] += 1
        if need_tex:
            latex_body = markdown_to_latex(content)
            write_text(tex_path, build_latex_document(meta, latex_body))
            stats["written_tex"] += 1

        print(f"  [{index}/{len(items)}] {basename}")
        stats["solutions"] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="洛谷题解爬取工具：按题号 / 难度 / 全部题号抓题解，保存为 md 或 tex",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python luogu_solution_spider.py --pid P1000 P1001\n"
            "  python luogu_solution_spider.py --difficulty 2 --page-limit 1\n"
            "  python luogu_solution_spider.py --difficulty 1 2 --group-by difficulty\n"
            "  python luogu_solution_spider.py --pid P1000 P17046 --group-by problem\n"
            "  python luogu_solution_spider.py --difficulty 2 --group-by difficulty problem\n"
            "  python luogu_solution_spider.py --all --page-limit 2 --format both\n"
        ),
    )
    parser.add_argument(
        "--pid",
        nargs="+",
        metavar="PID",
        help="题号，可写多个，例如 --pid P1000 P1001",
    )
    parser.add_argument(
        "--difficulty",
        nargs="+",
        type=int,
        choices=[1, 2, 3, 4, 5, 6, 7],
        metavar="N",
        help=(
            "难度：1=入门 2=普及- 3=普及/提高- 4=普及+/提高 "
            "5=提高+/省选- 6=省选/NOI- 7=NOI/NOI+/CTSC"
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="全部题号 P1000~P9999（极慢，建议配合 --page-limit 调试）",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "latex", "both"],
        default="markdown",
        help="输出格式，默认 markdown；latex 会做完整转义；both 两种都生成",
    )
    parser.add_argument(
        "--output",
        default="luogu_solutions",
        help="输出目录，默认 luogu_solutions",
    )
    parser.add_argument(
        "--group-by",
        nargs="+",
        choices=["none", "difficulty", "problem"],
        default=["none"],
        metavar="MODE",
        help=(
            "输出分目录方式：none=全部平铺（默认）；"
            "problem=按题号建子目录，例如 输出/P1000/...；"
            "difficulty=按难度建子目录，例如 输出/普及-/...；"
            "两个可以同时写，难度层在外、题号层在内"
        ),
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=None,
        help="枚举题目列表时最多翻几页（调试用，每页 50 道）",
    )
    parser.add_argument(
        "--cookie",
        default=None,
        help='Cookie 字符串，例如 "_uid=xxx; __client_id=yyy"',
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=DELAY_MIN,
        help=f"每次请求的最小间隔秒数，默认 {DELAY_MIN}",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=DELAY_MAX,
        help=f"每次请求的最大间隔秒数，默认 {DELAY_MAX}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的文件（默认跳过）",
    )
    parser.add_argument(
        "--no-login-check",
        action="store_true",
        help="跳过启动时的登录校验",
    )

    args = parser.parse_args(argv)

    if not (args.pid or args.difficulty or args.all):
        parser.print_help()
        print("\n请至少指定 --pid、--difficulty 或 --all 之一。", file=sys.stderr)
        return 2

    if args.delay_min > args.delay_max:
        print("--delay-min 不能大于 --delay-max", file=sys.stderr)
        return 2

    cookie = resolve_cookie(args.cookie)
    session = HttpSession(cookie)
    client = LuoguClient(session, args.delay_min, args.delay_max)

    print("=" * 62)
    print("洛谷题解爬取工具")
    print("=" * 62)
    print(f"Cookie：{'已提供' if cookie else '未提供'}")
    print(f"格式  ：{args.format}")
    print(f"输出  ：{os.path.abspath(args.output)}")
    if "difficulty" in args.group_by and "problem" in args.group_by:
        print("分组  ：按难度 + 题号两级子目录")
    elif "difficulty" in args.group_by:
        print("分组  ：按难度分子目录")
    elif "problem" in args.group_by:
        print("分组  ：按题号分子目录")
    print(f"间隔  ：{args.delay_min}~{args.delay_max} 秒随机")

    if not args.no_login_check:
        username = client.check_login()
        if username:
            print(f"登录  ：通过（{username}）")
        else:
            print(
                "登录  ：失败。洛谷题解列表需要登录态，"
                "请提供 --cookie 或 cookie.txt",
                file=sys.stderr,
            )
            return 1

    os.makedirs(args.output, exist_ok=True)

    targets = collect_targets(args, client)
    if not targets:
        print("没有找到任何题号。", file=sys.stderr)
        return 1

    print(f"\n共 {len(targets)} 道题待处理")

    stats = {
        "solutions": 0,
        "written_md": 0,
        "written_tex": 0,
        "skipped": 0,
        "failed": 0,
        "fallback": 0,
        "no_solution_problems": 0,
    }

    started = time.monotonic()
    try:
        for index, pid in enumerate(targets, 1):
            print(f"\n--- 进度 {index}/{len(targets)} ---")
            crawl_problem(client, pid, args, args.output, stats)
    except KeyboardInterrupt:
        print("\n已中断。")

    elapsed = time.monotonic() - started
    print("\n" + "=" * 62)
    print("完成")
    print("=" * 62)
    print(f"耗时        ：{elapsed / 60:.1f} 分钟")
    print(f"请求次数    ：{client.request_count}")
    print(f"题解总数    ：{stats['solutions']}")
    if args.format in ("markdown", "both"):
        print(f"写出 .md    ：{stats['written_md']}")
    if args.format in ("latex", "both"):
        print(f"写出 .tex   ：{stats['written_tex']}")
    print(f"已存在跳过  ：{stats['skipped']}")
    print(f"无题解题目  ：{stats['no_solution_problems']}")
    print(f"用摘录兜底  ：{stats['fallback']}")
    print(f"失败        ：{stats['failed']}")
    if args.group_by != ["none"]:
        print(f"输出根目录  ：{os.path.abspath(args.output)}")

    # 单篇失败不算整体失败；只有一篇都没写出来时才返回非零
    if stats["solutions"] == 0 and stats["skipped"] == 0 and targets:
        print("\n没有任何题解被写出，请检查上面的报错。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
