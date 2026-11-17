"""A small HTML document tree built with :mod:`html.parser`.

The original tool parsed captures with parse5/jsdom. This module provides the subset of
the DOM the extractor and the Readability port use, and a simplified version of the HTML
tree-construction rules on top of the standard library tokenizer: implicit ``html``,
``head`` and ``body``; void elements; optional end tags (``p``, ``li``, ``dt``/``dd``,
headings, table rows and cells, options); implicit ``tbody``/``tr``; foster parenting of
text and stray elements in tables; ``template`` contents kept out of the tree; raw-text
elements; and the dropped newline after ``<pre>``. Mis-nested formatting elements are
closed instead of being re-opened (no full adoption agency algorithm), which can differ
from a browser for broken markup but does not change the text.

Element count and nesting depth are bounded *while* parsing, before a large tree exists.
"""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import re

from .fileio import LIMITS, InputError

ELEMENT, TEXT, COMMENT, DOCUMENT, FRAGMENT = 1, 3, 8, 9, 11

VOID = frozenset("area base basefont bgsound br col embed frame hr image img input keygen link meta "
                 "param source track wbr".split())
SPECIAL = frozenset(
    "address applet area article aside base basefont bgsound blockquote body br button caption center "
    "col colgroup dd details dir div dl dt embed fieldset figcaption figure footer form frame frameset "
    "h1 h2 h3 h4 h5 h6 head header hgroup hr html iframe img input keygen li link listing main marquee "
    "menu meta nav noembed noframes noscript object ol p param plaintext pre script search section "
    "select source style summary table tbody td template textarea tfoot th thead title tr track ul "
    "wbr xmp".split())
CLOSES_P = frozenset(
    "address article aside blockquote center details dialog dir div dl fieldset figcaption figure "
    "footer header hgroup main menu nav ol p search section summary ul h1 h2 h3 h4 h5 h6 pre listing "
    "form hr table xmp plaintext li dd dt".split())
HEADINGS = frozenset("h1 h2 h3 h4 h5 h6".split())
FORMATTING = frozenset("a b big code em font i nobr s small strike strong tt u".split())
IMPLIED_END = frozenset("dd dt li optgroup option p rb rp rt rtc".split())
HEAD_TAGS = frozenset("base basefont bgsound link meta noframes script style template title noscript".split())
SCOPE = frozenset("applet caption html table td th marquee object template".split())
TABLE_CONTEXT = frozenset("table tbody tfoot thead tr".split())
TABLE_ALLOWED = frozenset("caption colgroup col tbody tfoot thead tr td th script style template form "
                          "input".split())
RAWTEXT = frozenset("xmp iframe noembed noframes".split())
RCDATA = frozenset("title textarea".split())
# Elements the running Python's tokenizer already treats as raw text / escapable raw text
# (newer patch releases handle all of them; older ones only script and style).
_BUILTIN_RAW = frozenset(HTMLParser.CDATA_CONTENT_ELEMENTS)
_BUILTIN_RCDATA = frozenset(getattr(HTMLParser, "RCDATA_CONTENT_ELEMENTS", ()))
ASCII_WS = " \t\n\f\r"


class Node:
    __slots__ = ("type", "tag", "attrs", "data", "parent", "children", "content", "readability",
                 "data_table", "depth", "character_set")

    def __init__(self, node_type: int, tag: str = "", attrs: dict | None = None, data: str = ""):
        self.type = node_type
        self.tag = tag
        self.attrs = attrs if attrs is not None else {}
        self.data = data
        self.parent: Node | None = None
        self.children: list[Node] = []
        self.content: Node | None = None
        self.readability: dict | None = None
        self.data_table: bool | None = None
        self.depth = 0
        self.character_set = ""

    def __repr__(self):
        return f"<Node {self.tag or self.type}>"

    # --- tree navigation -------------------------------------------------------------
    @property
    def element_children(self) -> list["Node"]:
        return [child for child in self.children if child.type == ELEMENT]

    def _index(self) -> int:
        siblings = self.parent.children
        for index, child in enumerate(siblings):
            if child is self:
                return index
        raise ValueError("node is not a child of its parent")

    @property
    def next_sibling(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        index = self._index() + 1
        return siblings[index] if index < len(siblings) else None

    @property
    def previous_sibling(self):
        if self.parent is None:
            return None
        index = self._index()
        return self.parent.children[index - 1] if index else None

    @property
    def first_child(self):
        return self.children[0] if self.children else None

    @property
    def last_child(self):
        return self.children[-1] if self.children else None

    @property
    def first_element_child(self):
        for child in self.children:
            if child.type == ELEMENT:
                return child
        return None

    @property
    def next_element_sibling(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        for child in siblings[self._index() + 1:]:
            if child.type == ELEMENT:
                return child
        return None

    @property
    def parent_element(self):
        parent = self.parent
        return parent if parent is not None and parent.type == ELEMENT else None

    def iter_elements(self):
        """Descendant elements in tree order (template contents excluded)."""
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            if node.type == ELEMENT:
                yield node
                if node.children:
                    stack.extend(reversed(node.children))

    def iter_nodes(self):
        """Descendant nodes of any type in tree order (template contents excluded)."""
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            yield node
            if node.children:
                stack.extend(reversed(node.children))

    def get_elements_by_tag_name(self, *tags: str) -> list["Node"]:
        if tags == ("*",):
            return list(self.iter_elements())
        wanted = set(tags)
        return [node for node in self.iter_elements() if node.tag in wanted]

    @property
    def text_content(self) -> str:
        if self.type in (TEXT, COMMENT):
            return self.data
        return "".join(node.data for node in self.iter_nodes() if node.type == TEXT)

    # --- attributes ------------------------------------------------------------------
    def get_attribute(self, name: str):
        return self.attrs.get(name)

    def has_attribute(self, name: str) -> bool:
        return name in self.attrs

    def set_attribute(self, name: str, value: str) -> None:
        self.attrs[name.lower()] = value

    @property
    def class_name(self) -> str:
        return self.attrs.get("class", "")

    @property
    def id(self) -> str:
        return self.attrs.get("id", "")

    def style(self, prop: str) -> str:
        """The last declared value of an inline ``style`` property, lower-cased ('' if none)."""
        declared = self.attrs.get("style")
        if not declared:
            return ""
        value = ""
        for declaration in declared.split(";"):
            name, sep, raw = declaration.partition(":")
            if sep and name.strip(ASCII_WS).lower() == prop:
                raw = re.sub(r"\s*!\s*important\s*$", "", raw, flags=re.IGNORECASE)
                value = raw.strip(ASCII_WS).lower()
        return value

    # --- mutation --------------------------------------------------------------------
    def remove(self) -> None:
        if self.parent is not None:
            self.parent.children.pop(self._index())
            self.parent = None

    def append_child(self, node: "Node") -> "Node":
        node.remove()
        node.parent = self
        self.children.append(node)
        return node

    def insert_before(self, node: "Node", reference: "Node | None") -> "Node":
        if reference is None:
            return self.append_child(node)
        node.remove()
        node.parent = self
        self.children.insert(reference._index(), node)
        return node

    def replace_child(self, new: "Node", old: "Node") -> "Node":
        if new is old:
            return old
        new.remove()
        index = old._index()
        self.children[index] = new
        new.parent = self
        old.parent = None
        return old

    def remove_child(self, node: "Node") -> "Node":
        node.remove()
        return node


def create_element(tag: str, attrs: dict | None = None) -> Node:
    return Node(ELEMENT, tag.lower(), dict(attrs or {}))


def create_text(data: str) -> Node:
    return Node(TEXT, data=data)


def document_title(document: Node) -> str:
    """``document.title``: the first ``title`` element's child text, whitespace collapsed."""
    for element in document.iter_elements():
        if element.tag == "title":
            text = "".join(child.data for child in element.children if child.type == TEXT)
            return re.sub(f"[{ASCII_WS}]+", " ", text).strip(ASCII_WS)
    return ""


def body_of(document: Node):
    html = next((child for child in document.children if child.type == ELEMENT and child.tag == "html"), None)
    if html is None:
        return None
    return next((child for child in html.children if child.type == ELEMENT and child.tag == "body"), None)


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.document = Node(DOCUMENT)
        self.stack: list[Node] = []
        self.html: Node | None = None
        self.head: Node | None = None
        self.body: Node | None = None
        self.count = 0
        self.skip_newline = False
        self.raw_tag: str | None = None

    # --- helpers ---------------------------------------------------------------------
    def _new(self, tag: str, attrs: dict) -> Node:
        self.count += 1
        if self.count > LIMITS["elements"]:
            raise InputError(f"HTML exceeds {LIMITS['elements']} elements")
        return create_element(tag, attrs)

    @staticmethod
    def _target(parent: Node) -> Node:
        if parent.type == ELEMENT and parent.tag == "template":
            if parent.content is None:
                parent.content = Node(FRAGMENT)
                parent.content.depth = parent.depth + 1
                parent.content.parent = None
            return parent.content
        return parent

    def _attach(self, node: Node, parent: Node, before: Node | None = None) -> None:
        parent = self._target(parent)
        node.depth = parent.depth + 1
        if node.depth > LIMITS["depth"]:
            raise InputError(f"HTML nesting exceeds {LIMITS['depth']} levels")
        if before is None:
            node.parent = parent
            parent.children.append(node)
        else:
            parent.insert_before(node, before)

    def _ensure_html(self) -> None:
        if self.html is None:
            self.html = self._new("html", {})
            self._attach(self.html, self.document)
            self.stack = [self.html]

    def _ensure_head(self) -> None:
        self._ensure_html()
        if self.head is None:
            self.head = self._new("head", {})
            self._attach(self.head, self.html)

    def _start_body(self, attrs: dict | None = None) -> None:
        self._ensure_head()
        if self.body is None:
            self.stack = [self.html]
            self.body = self._new("body", attrs or {})
            self._attach(self.body, self.html)
            self.stack.append(self.body)
        elif attrs:
            for name, value in attrs.items():
                self.body.attrs.setdefault(name, value)

    @property
    def current(self) -> Node:
        return self.stack[-1] if self.stack else self.document

    def _in_scope(self, tags, extra=frozenset()) -> bool:
        for node in reversed(self.stack):
            if node.tag in tags:
                return True
            if node.tag in SCOPE or node.tag in extra:
                return False
        return False

    def _pop_until(self, tags) -> None:
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node.tag in tags:
                return

    def _close_p(self) -> None:
        if self._in_scope({"p"}, {"button"}):
            self._pop_until({"p"})

    def _inside_table(self) -> bool:
        return any(node.tag == "table" for node in self.stack)

    def _in_head_context(self) -> bool:
        return self.body is None and not any(node.tag == "template" for node in self.stack)

    def _clear_to(self, tags) -> None:
        while len(self.stack) > 1 and self.current.tag not in tags and self.current.tag != "html":
            self.stack.pop()

    def _insertion(self, foster: bool) -> tuple[Node, Node | None]:
        current = self.current
        if foster and current.tag in TABLE_CONTEXT:
            for node in reversed(self.stack):
                if node.tag == "table":
                    if node.parent is not None:
                        return node.parent, node
                    break
        return current, None

    def _insert(self, tag: str, attrs: dict, *, push: bool = True, foster: bool = False) -> Node:
        element = self._new(tag, attrs)
        parent, before = self._insertion(foster)
        self._attach(element, parent, before)
        if push and tag not in VOID:
            self.stack.append(element)
        return element

    def _insert_text(self, text: str) -> None:
        if not text:
            return
        foster = text.strip(ASCII_WS) != ""
        parent, before = self._insertion(foster)
        target = self._target(parent)
        siblings = target.children
        if before is None:
            if siblings and siblings[-1].type == TEXT:
                siblings[-1].data += text
                return
        else:
            index = before._index()
            if index and siblings[index - 1].type == TEXT:
                siblings[index - 1].data += text
                return
        self._attach(create_text(text), parent, before)

    # --- tokenizer callbacks -----------------------------------------------------------
    def handle_starttag(self, tag, attr_list):
        attrs: dict[str, str] = {}
        for name, value in attr_list:
            attrs.setdefault(name, "" if value is None else value.replace("\x00", "�"))
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attr_list):
        attrs: dict[str, str] = {}
        for name, value in attr_list:
            attrs.setdefault(name, "" if value is None else value)
        foreign = any(node.tag in ("svg", "math") for node in self.stack)
        self._start(tag, attrs, self_closing=foreign)

    def _start(self, tag: str, attrs: dict, self_closing: bool) -> None:
        self.skip_newline = False
        if tag == "image":
            tag = "img"
        if tag == "html":
            self._ensure_html()
            for name, value in attrs.items():
                self.html.attrs.setdefault(name, value)
            return
        if self._in_head_context():
            if tag == "head":
                self._ensure_head()
                if self.head not in self.stack:
                    self.stack.append(self.head)
                return
            if tag in HEAD_TAGS:
                self._ensure_head()
                parent = self.current if self.current is not self.html else self.head
                element = self._new(tag, attrs)
                self._attach(element, parent)
                if tag not in VOID and not self_closing:
                    self.stack.append(element)
                    self._enter_text_mode(tag)
                return
            if tag == "body":
                self._start_body(attrs)
                return
            if tag == "frameset":
                return
            self._start_body()
        if tag == "body":
            for name, value in attrs.items():
                self.body.attrs.setdefault(name, value)
            return
        if tag in ("head", "frameset"):
            return
        foreign = any(node.tag in ("svg", "math") for node in self.stack)
        if foreign:
            self._insert(tag, attrs, push=not self_closing)
            return
        if self._inside_table() and self._table_start(tag, attrs):
            return
        if tag in ("caption", "colgroup", "col", "tbody", "thead", "tfoot", "tr", "td", "th"):
            return  # table parts outside a table are ignored
        if tag in CLOSES_P:
            if tag == "li":
                self._close_list_item({"li"})
            elif tag in ("dd", "dt"):
                self._close_list_item({"dd", "dt"})
            self._close_p()
        if tag in HEADINGS and self.current.tag in HEADINGS:
            self.stack.pop()
        if tag == "a" and any(node.tag == "a" for node in self.stack):
            self._pop_until({"a"})
        if tag == "button" and self._in_scope({"button"}):
            self._pop_until({"button"})
        if tag == "option" and self.current.tag == "option":
            self.stack.pop()
        if tag == "optgroup" and self.current.tag in ("option", "optgroup"):
            self.stack.pop()
        if tag == "nobr" and self._in_scope({"nobr"}):
            self._pop_until({"nobr"})
        foster = tag not in TABLE_ALLOWED
        self._insert(tag, attrs, foster=foster)
        if tag in VOID:
            return
        if tag in ("pre", "listing", "textarea"):
            self.skip_newline = True
        self._enter_text_mode(tag)

    def _enter_text_mode(self, tag: str) -> None:
        if tag in RAWTEXT or tag in RCDATA or tag in ("script", "style"):
            self.raw_tag = tag
            if tag not in _BUILTIN_RAW and tag not in _BUILTIN_RCDATA:
                self.set_cdata_mode(tag)

    def _close_list_item(self, tags) -> None:
        for node in reversed(self.stack):
            if node.tag in tags:
                self._pop_until({node.tag})
                return
            if node.tag in SPECIAL and node.tag not in ("address", "div", "p"):
                return

    def _table_start(self, tag: str, attrs: dict) -> bool:
        if tag == "caption":
            self._clear_to({"table", "template"})
            self._insert(tag, attrs)
            return True
        if tag == "colgroup":
            self._clear_to({"table", "template"})
            self._insert(tag, attrs)
            return True
        if tag == "col":
            if self.current.tag != "colgroup":
                self._clear_to({"table", "template"})
                self._insert("colgroup", {})
            self._insert(tag, attrs, push=False)
            return True
        if tag in ("tbody", "thead", "tfoot"):
            self._clear_to({"table", "template"})
            self._insert(tag, attrs)
            return True
        if tag == "tr":
            if self._in_scope({"td", "th"}):
                self._pop_until({"td", "th"})
            self._clear_to({"tbody", "thead", "tfoot", "table", "template"})
            if self.current.tag == "table":
                self._insert("tbody", {})
            self._insert(tag, attrs)
            return True
        if tag in ("td", "th"):
            if self._in_scope({"td", "th"}):
                self._pop_until({"td", "th"})
            self._clear_to({"tr", "tbody", "thead", "tfoot", "table", "template"})
            if self.current.tag == "table":
                self._insert("tbody", {})
            if self.current.tag in ("tbody", "thead", "tfoot"):
                self._insert("tr", {})
            self._insert(tag, attrs)
            return True
        if tag == "table" and self.current.tag in TABLE_CONTEXT:
            self._pop_until({"table"})
            self._insert(tag, attrs, foster=True)
            return True
        return False

    def handle_endtag(self, tag):
        self.skip_newline = False
        if self.raw_tag is not None and tag == self.raw_tag:
            self.raw_tag = None
        if tag in ("html", "body"):
            return
        if tag == "head":
            if self.head is not None and self.head in self.stack:
                self._pop_until({"head"})
            return
        if tag == "br":
            self._start("br", {}, self_closing=False)
            return
        if tag == "p":
            if not self._in_scope({"p"}, {"button"}):
                if self.body is None:
                    self._start_body()
                self._insert("p", {})
            self._pop_until({"p"})
            return
        if tag == "li":
            if self._in_scope({"li"}, {"ol", "ul"}):
                self._pop_until({"li"})
            return
        if tag in ("dd", "dt"):
            if self._in_scope({tag}):
                self._pop_until({tag})
            return
        if tag in HEADINGS:
            if self._in_scope(HEADINGS):
                self._pop_until(HEADINGS)
            return
        if tag in FORMATTING:
            if any(node.tag == tag for node in self.stack):
                self._pop_until({tag})
            return
        if tag in ("table", "tr", "td", "th", "tbody", "thead", "tfoot", "caption", "colgroup"):
            if any(node.tag == tag for node in self.stack):
                self._pop_until({tag})
            return
        for node in reversed(self.stack[1:]):
            if node.tag == tag:
                self._pop_until({tag})
                return
            if node.tag in SPECIAL:
                return

    def handle_data(self, data):
        if self.raw_tag in RCDATA and self.raw_tag not in _BUILTIN_RCDATA:
            data = unescape(data)
        if self.raw_tag is None:
            data = data.replace("\x00", "")
        if self.skip_newline:
            self.skip_newline = False
            if data.startswith("\n"):
                data = data[1:]
        if not data:
            return
        if self._in_head_context():
            current = self.current
            if self.stack and current is not self.html and current is not self.head:
                self._insert_text(data)  # inside title, style, script... in the head
                return
            stripped = data.lstrip(ASCII_WS)
            if not stripped:
                return
            self._start_body()
            data = stripped
        self._insert_text(data)

    def handle_comment(self, data):
        self._attach(Node(COMMENT, data=data), self.current)

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def parse_html(text: str, character_set: str = "UTF-8") -> Node:
    """Build a document from decoded HTML text (CR/LF normalized like a browser)."""
    builder = _Builder()
    builder.feed(text.replace("\r\n", "\n").replace("\r", "\n"))
    builder.close()
    builder._ensure_head()
    if builder.body is None:
        builder._start_body()
    document = builder.document
    document.character_set = character_set
    return document
