"""CSS selectors for ``querySelector``-style lookups on :mod:`.dom` trees.

Supported: type and universal selectors, ``#id``, ``.class``, attribute selectors
(``[a]``, ``=``, ``~=``, ``|=``, ``^=``, ``$=``, ``*=``, with an optional ``i`` flag),
the descendant, ``>``, ``+`` and ``~`` combinators, selector lists, and the
pseudo-classes ``:not() :is() :where() :has() :first-child :last-child :only-child
:first-of-type :last-of-type :only-of-type :nth-child() :nth-last-child()
:nth-of-type() :nth-last-of-type() :empty :root``. Anything else raises
:class:`SelectorError`.
"""
from __future__ import annotations

import re

from .dom import ELEMENT, Node


class SelectorError(ValueError):
    pass


_IDENT = re.compile(r"-?(?:[A-Za-z_\u00a0-\U0010ffff]|\\.)(?:[\w\-\u00a0-\U0010ffff]|\\.)*|--(?:[\w\-\u00a0-\U0010ffff]|\\.)*")
_WS = " \t\n\r\f"


def _unescape(text: str) -> str:
    def replace(match):
        value = match.group(1)
        if re.fullmatch(r"[0-9A-Fa-f]{1,6}\s?", value):
            code = int(value.strip(), 16)
            return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "\ufffd"
        return value
    return re.sub(r"\\([0-9A-Fa-f]{1,6}\s?|.)", replace, text)


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def error(self, message: str):
        raise SelectorError(f"'{self.text}' is not a valid selector ({message})")

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def skip_ws(self) -> bool:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in _WS:
            self.pos += 1
        return self.pos > start

    def ident(self) -> str:
        match = _IDENT.match(self.text, self.pos)
        if not match:
            self.error("expected an identifier")
        self.pos = match.end()
        return _unescape(match.group())

    def selector_list(self, nested: bool = False) -> list:
        selectors = []
        while True:
            self.skip_ws()
            selectors.append(self.complex())
            self.skip_ws()
            if self.peek() == ",":
                self.pos += 1
                continue
            if nested and self.peek() == ")":
                return selectors
            if self.pos < len(self.text):
                self.error(f"unexpected '{self.peek()}'")
            return selectors

    def complex(self) -> list:
        parts = [(None, self.compound())]
        while True:
            had_ws = self.skip_ws()
            char = self.peek()
            if char in (">", "+", "~"):
                self.pos += 1
                self.skip_ws()
                parts.append((char, self.compound()))
            elif had_ws and char and char not in ",)":
                parts.append((" ", self.compound()))
            else:
                return parts

    def compound(self) -> list:
        tests = []
        char = self.peek()
        if char == "*":
            self.pos += 1
            tests.append(("any",))
        elif char and (char.isalpha() or char in "_-\\" or ord(char) > 0x7F):
            tests.append(("tag", self.ident().lower()))
        while True:
            char = self.peek()
            if char == "#":
                self.pos += 1
                tests.append(("id", self.ident()))
            elif char == ".":
                self.pos += 1
                tests.append(("class", self.ident()))
            elif char == "[":
                self.pos += 1
                tests.append(self.attribute())
            elif char == ":":
                self.pos += 1
                tests.append(self.pseudo())
            else:
                break
        if not tests:
            self.error("expected a selector")
        return tests

    def attribute(self):
        self.skip_ws()
        name = self.ident().lower()
        self.skip_ws()
        char = self.peek()
        if char == "]":
            self.pos += 1
            return ("attr", name, None, None, False)
        operator = None
        for candidate in ("~=", "|=", "^=", "$=", "*=", "="):
            if self.text.startswith(candidate, self.pos):
                operator = candidate
                self.pos += len(candidate)
                break
        if operator is None:
            self.error("invalid attribute selector")
        self.skip_ws()
        char = self.peek()
        if char in ("'", '"'):
            end = self.pos + 1
            while end < len(self.text) and self.text[end] != char:
                end += 2 if self.text[end] == "\\" else 1
            if end >= len(self.text):
                self.error("unterminated string")
            value = _unescape(self.text[self.pos + 1:end])
            self.pos = end + 1
        else:
            value = self.ident()
        self.skip_ws()
        insensitive = False
        if self.peek() in ("i", "I", "s", "S"):
            insensitive = self.peek() in "iI"
            self.pos += 1
            self.skip_ws()
        if self.peek() != "]":
            self.error("expected ']'")
        self.pos += 1
        return ("attr", name, operator, value, insensitive)

    def pseudo(self):
        if self.peek() == ":":
            self.error("pseudo-elements are not supported")
        name = self.ident().lower()
        if self.peek() == "(":
            self.pos += 1
            if name in ("not", "is", "where", "has"):
                inner = self.selector_list(nested=True)
                self.pos += 1
                return ("pseudo-list", name, inner)
            if name in ("nth-child", "nth-last-child", "nth-of-type", "nth-last-of-type"):
                end = self.text.find(")", self.pos)
                if end < 0:
                    self.error("expected ')'")
                formula = self.text[self.pos:end].strip(_WS).lower()
                self.pos = end + 1
                return ("nth", name, _nth(formula, self))
            self.error(f"unsupported pseudo-class :{name}()")
        if name in ("first-child", "last-child", "only-child", "first-of-type", "last-of-type",
                    "only-of-type", "empty", "root"):
            return ("pseudo", name)
        self.error(f"unsupported pseudo-class :{name}")


def _nth(formula: str, parser: _Parser) -> tuple[int, int]:
    if formula == "odd":
        return 2, 1
    if formula == "even":
        return 2, 0
    match = re.fullmatch(r"([+-]?\d*)n\s*(?:([+-])\s*(\d+))?|([+-]?\d+)", formula)
    if not match:
        parser.error("invalid :nth-* formula")
    if match.group(4) is not None:
        return 0, int(match.group(4))
    step = match.group(1)
    a = -1 if step == "-" else 1 if step in ("", "+") else int(step)
    b = int(match.group(3)) * (-1 if match.group(2) == "-" else 1) if match.group(3) else 0
    return a, b


def _siblings(node: Node) -> list[Node]:
    return node.parent.element_children if node.parent is not None else [node]


def _nth_match(a: int, b: int, position: int) -> bool:
    if a == 0:
        return position == b
    return (position - b) % a == 0 and (position - b) // a >= 0


def _test(node: Node, test) -> bool:
    kind = test[0]
    if kind == "any":
        return True
    if kind == "tag":
        return node.tag == test[1]
    if kind == "id":
        return node.attrs.get("id") == test[1]
    if kind == "class":
        return test[1] in node.attrs.get("class", "").split()
    if kind == "attr":
        _, name, operator, value, insensitive = test
        actual = node.attrs.get(name)
        if actual is None:
            return False
        if operator is None:
            return True
        if insensitive:
            actual, value = actual.lower(), value.lower()
        if operator == "=":
            return actual == value
        if operator == "~=":
            return bool(value) and value in actual.split()
        if operator == "|=":
            return actual == value or actual.startswith(value + "-")
        if operator == "^=":
            return bool(value) and actual.startswith(value)
        if operator == "$=":
            return bool(value) and actual.endswith(value)
        return bool(value) and value in actual
    if kind == "pseudo":
        name = test[1]
        if name == "root":
            return node.parent is not None and node.parent.type != ELEMENT and node.tag == "html"
        if name == "empty":
            return not any(child.type == ELEMENT or (child.type == 3 and child.data) for child in node.children)
        siblings = _siblings(node)
        if name.endswith("of-type"):
            siblings = [sibling for sibling in siblings if sibling.tag == node.tag]
        if name.startswith("first"):
            return siblings[0] is node
        if name.startswith("last"):
            return siblings[-1] is node
        return len(siblings) == 1
    if kind == "nth":
        _, name, (a, b) = test
        siblings = _siblings(node)
        if "of-type" in name:
            siblings = [sibling for sibling in siblings if sibling.tag == node.tag]
        index = next(i for i, sibling in enumerate(siblings) if sibling is node)
        position = len(siblings) - index if "last" in name else index + 1
        return _nth_match(a, b, position)
    if kind == "pseudo-list":
        _, name, inner = test
        if name == "has":
            return any(_matches_list(descendant, inner) for descendant in node.iter_elements())
        found = _matches_list(node, inner)
        return not found if name == "not" else found
    return False


def _compound(node: Node, tests) -> bool:
    return all(_test(node, test) for test in tests)


def _matches_complex(node: Node, parts, index: int) -> bool:
    combinator, tests = parts[index]
    if not _compound(node, tests):
        return False
    if index == 0:
        return True
    if combinator == ">":
        parent = node.parent_element
        return parent is not None and _matches_complex(parent, parts, index - 1)
    if combinator == " ":
        parent = node.parent_element
        while parent is not None:
            if _matches_complex(parent, parts, index - 1):
                return True
            parent = parent.parent_element
        return False
    siblings = _siblings(node)
    position = next(i for i, sibling in enumerate(siblings) if sibling is node)
    if combinator == "+":
        return position > 0 and _matches_complex(siblings[position - 1], parts, index - 1)
    return any(_matches_complex(sibling, parts, index - 1) for sibling in siblings[:position])


def _matches_list(node: Node, selectors) -> bool:
    return any(_matches_complex(node, parts, len(parts) - 1) for parts in selectors)


def compile_selector(text: str):
    return _Parser(text).selector_list()


def query_all(root: Node, selector: str) -> list[Node]:
    selectors = compile_selector(selector)
    return [node for node in root.iter_elements() if _matches_list(node, selectors)]


def query(root: Node, selector: str) -> Node | None:
    selectors = compile_selector(selector)
    for node in root.iter_elements():
        if _matches_list(node, selectors):
            return node
    return None
