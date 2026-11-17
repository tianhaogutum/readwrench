# Portions of this file are a Python port of Mozilla Readability 0.6.0
# (https://github.com/mozilla/readability, src Readability.js),
# Copyright (c) 2010 Arc90 Inc and Mozilla contributors,
# licensed under the Apache License, Version 2.0 (licenses/Readability-Apache-2.0.txt).
# Changes: translated to Python over readwrench.lineage.dom; only the parts that decide
# which text is kept are ported (metadata title/byline, prepDocument, grabArticle with
# charThreshold 0, prepArticle, simplifyNestedElements). URL rewriting, class cleanup,
# lazy-image fixes, JSON-LD (captured scripts are removed earlier) and text-direction
# detection do not change extracted text and are omitted.
"""Article content selection: a port of Mozilla Readability 0.6.0's ``parse()``."""
from __future__ import annotations

import math
import re

from .dom import ELEMENT, TEXT, Node, body_of, create_element, document_title
from .jsutil import WS, js_len, js_trim

FLAG_STRIP_UNLIKELYS = 0x1
FLAG_WEIGHT_CLASSES = 0x2
FLAG_CLEAN_CONDITIONALLY = 0x4
N_TOP_CANDIDATES = 5
TAGS_TO_SCORE = ("section", "h2", "h3", "h4", "h5", "h6", "p", "td", "pre")
DEFAULT_CHAR_THRESHOLD = 500

UNLIKELY_CANDIDATES = re.compile(
    r"-ad-|ai2html|banner|breadcrumbs|combx|comment|community|cover-wrap|disqus|extra|footer|gdpr|header|"
    r"legends|menu|related|remark|replies|rss|shoutbox|sidebar|skyscraper|social|sponsor|supplemental|"
    r"ad-break|agegate|pagination|pager|popup|yom-remote", re.IGNORECASE)
OK_MAYBE_CANDIDATE = re.compile(r"and|article|body|column|content|main|mathjax|shadow", re.IGNORECASE)
POSITIVE = re.compile(r"article|body|content|entry|hentry|h-entry|main|page|pagination|post|text|blog|story",
                      re.IGNORECASE)
NEGATIVE = re.compile(
    r"-ad-|hidden|^hid$| hid$| hid |^hid |banner|combx|comment|com-|contact|footer|gdpr|masthead|media|meta|"
    r"outbrain|promo|related|scroll|share|shoutbox|sidebar|skyscraper|sponsor|shopping|tags|widget",
    re.IGNORECASE)
BYLINE = re.compile(r"byline|author|dateline|writtenby|p-author", re.IGNORECASE)
NORMALIZE = re.compile(f"{WS}{{2,}}")
VIDEOS = re.compile(r"//(www\.)?((dailymotion|youtube|youtube-nocookie|player\.vimeo|v\.qq|bilibili|"
                    r"live.bilibili)\.com|(archive|upload\.wikimedia)\.org|player\.twitch\.tv)", re.IGNORECASE)
SHARE_ELEMENTS = re.compile(r"(\b|_)(share|sharedaddy)(\b|_)", re.IGNORECASE | re.ASCII)
TOKENIZE = re.compile(r"\W+", re.ASCII)
WHITESPACE = re.compile(f"^{WS}*$")
HAS_CONTENT = re.compile(f"[^\t\n\u000b\u000c\r    -     　﻿]$")
HASH_URL = re.compile(r"^#.+")
COMMAS = re.compile(",|،|﹐|︐|︑|⹁|⸴|⸲|，")
AD_WORDS = re.compile(r"^(ad(vertising|vertisement)?|pub(licité)?|werb(ung)?|广告|Реклама|Anuncio)$",
                      re.IGNORECASE)
LOADING_WORDS = re.compile(r"^((loading|正在加载|Загрузка|chargement|cargando)(…|\.\.\.)?)$", re.IGNORECASE)
PROPERTY_PATTERN = re.compile(
    f"{WS}*(article|dc|dcterm|og|twitter){WS}*:{WS}*(author|creator|description|published_time|title|"
    f"site_name){WS}*", re.IGNORECASE)
NAME_PATTERN = re.compile(
    f"^{WS}*(?:(dc|dcterm|og|twitter|parsely|weibo:(article|webpage)){WS}*[-.:]{WS}*)?"
    f"(author|creator|pub-date|description|title|site_name){WS}*$", re.IGNORECASE)
TITLE_SEPARATORS = r"\|\-–—\\/>»"
SEPARATED = re.compile(f"{WS}[{TITLE_SEPARATORS}]{WS}")
HIERARCHICAL = re.compile(f"{WS}[\\\\/>»]{WS}")
LEADING_PART = re.compile(f"^[^{TITLE_SEPARATORS}]*[{TITLE_SEPARATORS}]", re.IGNORECASE)
END_SENTENCE = re.compile(r"\.( |$)")
IMAGE_EXTENSION = re.compile(r"\.(jpg|jpeg|png|webp)", re.IGNORECASE)
# ``new URL(value)`` succeeds for any string with a valid scheme.
URL_LIKE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)

UNLIKELY_ROLES = ("menu", "menubar", "complementary", "navigation", "alert", "alertdialog", "dialog")
DIV_TO_P_ELEMS = frozenset(("blockquote", "dl", "div", "img", "ol", "p", "pre", "table", "ul"))
ALTER_TO_DIV_EXCEPTIONS = ("div", "article", "section", "p", "ol", "ul")
PRESENTATIONAL_ATTRIBUTES = ("align", "background", "bgcolor", "border", "cellpadding", "cellspacing",
                             "frame", "hspace", "rules", "style", "valign", "vspace")
DEPRECATED_SIZE_ATTRIBUTE_ELEMS = ("table", "th", "td", "hr", "pre")
PHRASING_ELEMS = frozenset((
    "abbr", "audio", "b", "bdo", "br", "button", "cite", "code", "data", "datalist", "dfn", "em", "embed",
    "i", "img", "input", "kbd", "label", "mark", "math", "meter", "noscript", "object", "output",
    "progress", "q", "ruby", "samp", "script", "select", "small", "span", "strong", "sub", "sup",
    "textarea", "time", "var", "wbr"))
HTML_ESCAPE_MAP = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


class ReadabilityError(Exception):
    pass


def _ratio(numerator: float, denominator: float) -> float:
    if denominator:
        return numerator / denominator
    return math.nan if not numerator else math.inf


def _js_split_count(text: str, pattern: re.Pattern) -> int:
    return len(pattern.split(text))


class Readability:
    """Readability with ``charThreshold: 0``: the first grabArticle attempt is always kept."""

    def __init__(self, document: Node, *, max_elems_to_parse: int = 0,
                 link_density_modifier: float = 0, allowed_video_regex: re.Pattern = VIDEOS):
        self.doc = document
        self.max_elems = max_elems_to_parse
        self.link_density_modifier = link_density_modifier
        self.allowed_video = allowed_video_regex
        self.flags = FLAG_STRIP_UNLIKELYS | FLAG_WEIGHT_CLASSES | FLAG_CLEAN_CONDITIONALLY
        self.article_title = ""
        self.article_byline = None
        self.metadata: dict = {}

    # --- small DOM helpers ------------------------------------------------------------
    @staticmethod
    def _inner_text(node: Node, normalize: bool = True) -> str:
        text = js_trim(node.text_content)
        return NORMALIZE.sub(" ", text) if normalize else text

    def _flag(self, flag: int) -> bool:
        return bool(self.flags & flag)

    @staticmethod
    def _next_node(node: Node | None, ignore_self_and_kids: bool = False):
        if not ignore_self_and_kids and node.first_element_child is not None:
            return node.first_element_child
        sibling = node.next_element_sibling
        if sibling is not None:
            return sibling
        while True:
            node = node.parent
            if node is None or node.type != ELEMENT:
                return None
            sibling = node.next_element_sibling
            if sibling is not None:
                return sibling

    def _remove_and_get_next(self, node: Node):
        next_node = self._next_node(node, True)
        node.remove()
        return next_node

    @staticmethod
    def _skip_whitespace_nodes(node: Node | None):
        while node is not None and node.type != ELEMENT and WHITESPACE.match(node.text_content):
            node = node.next_sibling
        return node

    def _is_phrasing(self, node: Node) -> bool:
        if node.type == TEXT:
            return True
        if node.type != ELEMENT:
            return False
        return node.tag in PHRASING_ELEMS or (
            node.tag in ("a", "del", "ins") and all(self._is_phrasing(child) for child in node.children))

    @staticmethod
    def _is_whitespace(node: Node) -> bool:
        return (node.type == TEXT and not js_trim(node.text_content)) or (node.type == ELEMENT and node.tag == "br")

    def _set_node_tag(self, node: Node, tag: str) -> Node:
        replacement = create_element(tag)
        while node.children:
            replacement.append_child(node.children[0])
        node.parent.replace_child(replacement, node)
        if node.readability is not None:
            replacement.readability = node.readability
        for name, value in node.attrs.items():
            replacement.attrs[name] = value
        return replacement

    @staticmethod
    def _remove_nodes(nodes: list[Node], should_remove=None) -> None:
        for node in reversed(nodes):
            if node.parent is not None and (should_remove is None or should_remove(node)):
                node.parent.remove_child(node)

    def _class_weight(self, node: Node) -> int:
        if not self._flag(FLAG_WEIGHT_CLASSES):
            return 0
        weight = 0
        class_name = node.class_name
        if class_name:
            if NEGATIVE.search(class_name):
                weight -= 25
            if POSITIVE.search(class_name):
                weight += 25
        node_id = node.id
        if node_id:
            if NEGATIVE.search(node_id):
                weight -= 25
            if POSITIVE.search(node_id):
                weight += 25
        return weight

    def _link_density(self, element: Node) -> float:
        text_length = js_len(self._inner_text(element))
        if text_length == 0:
            return 0
        link_length = 0.0
        for link in element.get_elements_by_tag_name("a"):
            href = link.get_attribute("href")
            coefficient = 0.3 if href and HASH_URL.match(href) else 1
            link_length += js_len(self._inner_text(link)) * coefficient
        return link_length / text_length

    def _text_density(self, element: Node, tags) -> float:
        text_length = js_len(self._inner_text(element, True))
        if text_length == 0:
            return 0
        children_length = sum(js_len(self._inner_text(child, True))
                              for child in element.get_elements_by_tag_name(*tags))
        return children_length / text_length

    @staticmethod
    def _ancestors(node: Node, max_depth: int = 0) -> list[Node]:
        ancestors = []
        depth = 0
        while node.parent is not None:
            ancestors.append(node.parent)
            depth += 1
            if max_depth and depth == max_depth:
                break
            node = node.parent
        return ancestors

    @staticmethod
    def _has_ancestor_tag(node: Node, tag: str, max_depth: int = 3, keep=None) -> bool:
        depth = 0
        while node.parent is not None:
            if max_depth > 0 and depth > max_depth:
                return False
            parent = node.parent
            if parent.type == ELEMENT and parent.tag == tag and (keep is None or keep(parent)):
                return True
            node = parent
            depth += 1
        return False

    def _has_single_tag_inside(self, element: Node, tag: str) -> bool:
        children = element.element_children
        if len(children) != 1 or children[0].tag != tag:
            return False
        return not any(child.type == TEXT and HAS_CONTENT.search(child.text_content) for child in element.children)

    def _is_without_content(self, node: Node) -> bool:
        if node.type != ELEMENT or js_trim(node.text_content):
            return False
        children = node.element_children
        return not children or len(children) == len(node.get_elements_by_tag_name("br")) + \
            len(node.get_elements_by_tag_name("hr"))

    def _has_child_block(self, element: Node) -> bool:
        return any(child.type == ELEMENT and (child.tag in DIV_TO_P_ELEMS or self._has_child_block(child))
                   for child in element.children)

    @staticmethod
    def _is_probably_visible(node: Node) -> bool:
        return (node.style("display") != "none" and node.style("visibility") != "hidden"
                and not node.has_attribute("hidden")
                and (not node.has_attribute("aria-hidden") or node.get_attribute("aria-hidden") != "true"
                     or "fallback-image" in node.class_name))

    # --- metadata ---------------------------------------------------------------------
    @staticmethod
    def _unescape_entities(text):
        if not text:
            return text
        text = re.sub(r"&(quot|amp|apos|lt|gt);", lambda m: HTML_ESCAPE_MAP[m.group(1)], text)

        def numeric(match):
            hexa, decimal = match.group(1), match.group(2)
            number = int(hexa, 16) if hexa else int(decimal)
            if number == 0 or number > 0x10FFFF or 0xD800 <= number <= 0xDFFF:
                number = 0xFFFD
            return chr(number)
        return re.sub(r"&#(?:x([0-9a-f]+)|([0-9]+));", numeric, text, flags=re.IGNORECASE)

    def _article_title(self) -> str:
        cur = orig = js_trim(document_title(self.doc))
        had_hierarchical = False

        def word_count(text: str) -> int:
            return len(re.split(f"{WS}+", text))

        if SEPARATED.search(cur):
            had_hierarchical = bool(HIERARCHICAL.search(cur))
            separators = list(SEPARATED.finditer(orig))
            cur = orig[:separators[-1].start()]
            if word_count(cur) < 3:
                cur = LEADING_PART.sub("", orig)
        elif ": " in cur:
            trimmed = js_trim(cur)
            headings = self.doc.get_elements_by_tag_name("h1", "h2")
            if not any(js_trim(heading.text_content) == trimmed for heading in headings):
                cur = orig[orig.rfind(":") + 1:]
                if word_count(cur) < 3:
                    cur = orig[orig.find(":") + 1:]
                elif word_count(orig[:max(orig.find(":"), 0)]) > 5:
                    cur = orig
        elif js_len(cur) > 150 or js_len(cur) < 15:
            h_ones = self.doc.get_elements_by_tag_name("h1")
            if len(h_ones) == 1:
                cur = self._inner_text(h_ones[0])
        cur = NORMALIZE.sub(" ", js_trim(cur))
        count = word_count(cur)
        if count <= 4 and (not had_hierarchical or count != word_count(SEPARATED.sub("", orig)) - 1):
            cur = orig
        return cur

    def _metadata(self) -> dict:
        values: dict[str, str] = {}
        for element in self.doc.get_elements_by_tag_name("meta"):
            name = element.get_attribute("name")
            prop = element.get_attribute("property")
            content = element.get_attribute("content")
            if not content:
                continue
            matched = False
            if prop:
                match = PROPERTY_PATTERN.search(prop)
                if match:
                    matched = True
                    values[re.sub(f"{WS}", "", match.group(0).lower())] = js_trim(content)
            if not matched and name and NAME_PATTERN.search(name):
                key = re.sub(f"{WS}", "", name.lower()).replace(".", ":")
                values[key] = js_trim(content)
        title = (values.get("dc:title") or values.get("dcterm:title") or values.get("og:title")
                 or values.get("weibo:article:title") or values.get("weibo:webpage:title") or values.get("title")
                 or values.get("twitter:title") or values.get("parsely-title"))
        if not title:
            title = self._article_title()
        article_author = values.get("article:author")
        if article_author is not None and URL_LIKE.match(article_author):
            article_author = None
        byline = (values.get("dc:creator") or values.get("dcterm:creator") or values.get("author")
                  or values.get("parsely-author") or article_author)
        return {"title": self._unescape_entities(title), "byline": byline}

    # --- document preparation -----------------------------------------------------------
    def _remove_placeholder_images(self) -> None:
        for image in self.doc.get_elements_by_tag_name("img"):
            keep = False
            for name, value in image.attrs.items():
                if name in ("src", "srcset", "data-src", "data-srcset") or IMAGE_EXTENSION.search(value):
                    keep = True
                    break
            if not keep:
                image.remove()

    def _replace_brs(self, element: Node) -> None:
        for br in element.get_elements_by_tag_name("br"):
            following = br.next_sibling
            replaced = False
            while True:
                following = self._skip_whitespace_nodes(following)
                if following is None or not (following.type == ELEMENT and following.tag == "br"):
                    break
                replaced = True
                sibling = following.next_sibling
                following.remove()
                following = sibling
            if not replaced:
                continue
            paragraph = create_element("p")
            br.parent.replace_child(paragraph, br)
            following = paragraph.next_sibling
            while following is not None:
                if following.type == ELEMENT and following.tag == "br":
                    after = self._skip_whitespace_nodes(following.next_sibling)
                    if after is not None and after.type == ELEMENT and after.tag == "br":
                        break
                if not self._is_phrasing(following):
                    break
                sibling = following.next_sibling
                paragraph.append_child(following)
                following = sibling
            while paragraph.last_child is not None and self._is_whitespace(paragraph.last_child):
                paragraph.last_child.remove()
            if paragraph.parent.type == ELEMENT and paragraph.parent.tag == "p":
                self._set_node_tag(paragraph.parent, "div")

    def _prep_document(self) -> None:
        self._remove_nodes(self.doc.get_elements_by_tag_name("style"))
        body = body_of(self.doc)
        if body is not None:
            self._replace_brs(body)
        for font in self.doc.get_elements_by_tag_name("font"):
            self._set_node_tag(font, "span")

    # --- grabArticle ------------------------------------------------------------------------
    def _initialize(self, node: Node) -> None:
        score = 0
        if node.tag == "div":
            score += 5
        elif node.tag in ("pre", "td", "blockquote"):
            score += 3
        elif node.tag in ("address", "ol", "ul", "dl", "dd", "dt", "li", "form"):
            score -= 3
        elif node.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "th"):
            score -= 5
        node.readability = {"contentScore": score + self._class_weight(node)}

    def _is_valid_byline(self, node: Node, match_string: str) -> bool:
        rel = node.get_attribute("rel")
        itemprop = node.get_attribute("itemprop")
        length = js_len(js_trim(node.text_content))
        return (rel == "author" or bool(itemprop and "author" in itemprop) or bool(BYLINE.search(match_string))) \
            and 0 < length < 100

    @staticmethod
    def _text_similarity(text_a: str, text_b: str) -> float:
        tokens_a = [token for token in TOKENIZE.split(text_a.lower()) if token]
        tokens_b = [token for token in TOKENIZE.split(text_b.lower()) if token]
        if not tokens_a or not tokens_b:
            return 0
        unique_b = [token for token in tokens_b if token not in tokens_a]
        return 1 - js_len(" ".join(unique_b)) / js_len(" ".join(tokens_b))

    def _header_duplicates_title(self, node: Node) -> bool:
        if node.tag not in ("h1", "h2"):
            return False
        return self._text_similarity(self.article_title, self._inner_text(node, False)) > 0.75

    def _grab_article(self) -> Node | None:
        page = body_of(self.doc)
        if page is None:
            return None
        elements_to_score: list[Node] = []
        node = next((child for child in self.doc.children if child.type == ELEMENT), None)
        should_remove_title_header = True
        strip_unlikely = self._flag(FLAG_STRIP_UNLIKELYS)
        while node is not None:
            match_string = f"{node.class_name} {node.id}"
            if not self._is_probably_visible(node):
                node = self._remove_and_get_next(node)
                continue
            if node.get_attribute("aria-modal") == "true" and node.get_attribute("role") == "dialog":
                node = self._remove_and_get_next(node)
                continue
            if not self.article_byline and not self.metadata.get("byline") and \
                    self._is_valid_byline(node, match_string):
                end_marker = self._next_node(node, True)
                following = self._next_node(node)
                item_name = None
                while following is not None and following is not end_marker:
                    itemprop = following.get_attribute("itemprop")
                    if itemprop and "name" in itemprop:
                        item_name = following
                        break
                    following = self._next_node(following)
                self.article_byline = js_trim((item_name or node).text_content)
                node = self._remove_and_get_next(node)
                continue
            if should_remove_title_header and self._header_duplicates_title(node):
                should_remove_title_header = False
                node = self._remove_and_get_next(node)
                continue
            if strip_unlikely:
                if UNLIKELY_CANDIDATES.search(match_string) and not OK_MAYBE_CANDIDATE.search(match_string) \
                        and not self._has_ancestor_tag(node, "table") and not self._has_ancestor_tag(node, "code") \
                        and node.tag not in ("body", "a"):
                    node = self._remove_and_get_next(node)
                    continue
                if node.get_attribute("role") in UNLIKELY_ROLES:
                    node = self._remove_and_get_next(node)
                    continue
            if node.tag in ("div", "section", "header", "h1", "h2", "h3", "h4", "h5", "h6") and \
                    self._is_without_content(node):
                node = self._remove_and_get_next(node)
                continue
            if node.tag in TAGS_TO_SCORE:
                elements_to_score.append(node)
            if node.tag == "div":
                paragraph = None
                child = node.first_child
                while child is not None:
                    following = child.next_sibling
                    if self._is_phrasing(child):
                        if paragraph is not None:
                            paragraph.append_child(child)
                        elif not self._is_whitespace(child):
                            paragraph = create_element("p")
                            node.replace_child(paragraph, child)
                            paragraph.append_child(child)
                    elif paragraph is not None:
                        while paragraph.last_child is not None and self._is_whitespace(paragraph.last_child):
                            paragraph.last_child.remove()
                        paragraph = None
                    child = following
                if self._has_single_tag_inside(node, "p") and self._link_density(node) < 0.25:
                    new_node = node.element_children[0]
                    node.parent.replace_child(new_node, node)
                    node = new_node
                    elements_to_score.append(node)
                elif not self._has_child_block(node):
                    node = self._set_node_tag(node, "p")
                    elements_to_score.append(node)
            node = self._next_node(node)

        candidates: list[Node] = []
        for element in elements_to_score:
            parent = element.parent
            if parent is None or parent.type != ELEMENT:
                continue
            inner_text = self._inner_text(element)
            length = js_len(inner_text)
            if length < 25:
                continue
            ancestors = self._ancestors(element, 5)
            if not ancestors:
                continue
            content_score = 1 + _js_split_count(inner_text, COMMAS) + min(length // 100, 3)
            for level, ancestor in enumerate(ancestors):
                if ancestor.type != ELEMENT or ancestor.parent is None or ancestor.parent.type != ELEMENT:
                    continue
                if ancestor.readability is None:
                    self._initialize(ancestor)
                    candidates.append(ancestor)
                divider = 1 if level == 0 else 2 if level == 1 else level * 3
                ancestor.readability["contentScore"] += content_score / divider

        top_candidates: list[Node] = []
        for candidate in candidates:
            score = candidate.readability["contentScore"] * (1 - self._link_density(candidate))
            candidate.readability["contentScore"] = score
            for index in range(N_TOP_CANDIDATES):
                existing = top_candidates[index] if index < len(top_candidates) else None
                if existing is None or score > existing.readability["contentScore"]:
                    top_candidates.insert(index, candidate)
                    if len(top_candidates) > N_TOP_CANDIDATES:
                        top_candidates.pop()
                    break

        top = top_candidates[0] if top_candidates else None
        created = False
        if top is None or top.tag == "body":
            top = create_element("div")
            created = True
            while page.children:
                top.append_child(page.children[0])
            page.append_child(top)
            self._initialize(top)
        else:
            alternatives = []
            for other in top_candidates[1:]:
                if _ratio(other.readability["contentScore"], top.readability["contentScore"]) >= 0.75:
                    alternatives.append(self._ancestors(other))
            minimum = 3
            if len(alternatives) >= minimum:
                parent = top.parent
                while parent is not None and parent.tag != "body":
                    containing = 0
                    for ancestors in alternatives:
                        if containing >= minimum:
                            break
                        containing += any(item is parent for item in ancestors)
                    if containing >= minimum:
                        top = parent
                        break
                    parent = parent.parent
            if top.readability is None:
                self._initialize(top)
            parent = top.parent
            last_score = top.readability["contentScore"]
            threshold = last_score / 3
            while parent is not None and parent.tag != "body":
                if parent.readability is None:
                    parent = parent.parent
                    continue
                parent_score = parent.readability["contentScore"]
                if parent_score < threshold:
                    break
                if parent_score > last_score:
                    top = parent
                    break
                last_score = parent.readability["contentScore"]
                parent = parent.parent
            parent = top.parent
            while parent is not None and parent.tag != "body" and len(parent.element_children) == 1:
                top = parent
                parent = top.parent
            if top.readability is None:
                self._initialize(top)

        article = create_element("div")
        sibling_threshold = max(10, top.readability["contentScore"] * 0.2)
        parent = top.parent
        siblings = parent.element_children
        index = 0
        while index < len(siblings):
            sibling = siblings[index]
            append = False
            if sibling is top:
                append = True
            else:
                bonus = 0
                if sibling.class_name == top.class_name and top.class_name != "":
                    bonus += top.readability["contentScore"] * 0.2
                if sibling.readability is not None and sibling.readability["contentScore"] + bonus >= sibling_threshold:
                    append = True
                elif sibling.tag == "p":
                    link_density = self._link_density(sibling)
                    content = self._inner_text(sibling)
                    length = js_len(content)
                    if length > 80 and link_density < 0.25:
                        append = True
                    elif 0 < length < 80 and link_density == 0 and END_SENTENCE.search(content):
                        append = True
            if append:
                if sibling.tag not in ALTER_TO_DIV_EXCEPTIONS:
                    sibling = self._set_node_tag(sibling, "div")
                article.append_child(sibling)
                siblings = parent.element_children
                continue
            index += 1

        self._prep_article(article)
        if created:
            top.attrs["id"] = "readability-page-1"
            top.attrs["class"] = "page"
        else:
            page_div = create_element("div", {"id": "readability-page-1", "class": "page"})
            while article.children:
                page_div.append_child(article.children[0])
            article.append_child(page_div)
        return article

    # --- prepArticle ------------------------------------------------------------------------
    def _clean_styles(self, element: Node) -> None:
        if element.tag == "svg":
            return
        for name in PRESENTATIONAL_ATTRIBUTES:
            element.attrs.pop(name, None)
        if element.tag in DEPRECATED_SIZE_ATTRIBUTE_ELEMS:
            element.attrs.pop("width", None)
            element.attrs.pop("height", None)
        for child in element.element_children:
            self._clean_styles(child)

    @staticmethod
    def _row_and_column_count(table: Node) -> tuple[int, int]:
        rows = columns = 0
        for row in table.get_elements_by_tag_name("tr"):
            rows += _js_int(row.get_attribute("rowspan")) or 1
            in_row = 0
            for cell in row.get_elements_by_tag_name("td"):
                in_row += _js_int(cell.get_attribute("colspan")) or 1
            columns = max(columns, in_row)
        return rows, columns

    def _mark_data_tables(self, root: Node) -> None:
        for table in root.get_elements_by_tag_name("table"):
            if table.get_attribute("role") == "presentation":
                table.data_table = False
                continue
            if table.get_attribute("datatable") == "0":
                table.data_table = False
                continue
            if table.get_attribute("summary"):
                table.data_table = True
                continue
            captions = table.get_elements_by_tag_name("caption")
            if captions and captions[0].children:
                table.data_table = True
                continue
            if any(table.get_elements_by_tag_name(tag) for tag in ("col", "colgroup", "tfoot", "thead", "th")):
                table.data_table = True
                continue
            if table.get_elements_by_tag_name("table"):
                table.data_table = False
                continue
            rows, columns = self._row_and_column_count(table)
            if columns == 1 or rows == 1:
                table.data_table = False
                continue
            if rows >= 10 or columns > 4:
                table.data_table = True
                continue
            table.data_table = rows * columns > 10

    def _clean(self, element: Node, tag: str) -> None:
        is_embed = tag in ("object", "embed", "iframe")

        def remove(node: Node) -> bool:
            if is_embed:
                if any(self.allowed_video.search(value) for value in node.attrs.values()):
                    return False
            return True
        self._remove_nodes(element.get_elements_by_tag_name(tag), remove)

    def _clean_matched_nodes(self, element: Node, matches) -> None:
        end_marker = self._next_node(element, True)
        following = self._next_node(element)
        while following is not None and following is not end_marker:
            if matches(following, f"{following.class_name} {following.id}"):
                following = self._remove_and_get_next(following)
            else:
                following = self._next_node(following)

    def _clean_conditionally(self, element: Node, tag: str) -> None:
        if not self._flag(FLAG_CLEAN_CONDITIONALLY):
            return

        def should_remove(node: Node) -> bool:
            is_list = tag in ("ul", "ol")
            if not is_list:
                list_length = sum(js_len(self._inner_text(item)) for item in node.get_elements_by_tag_name("ul", "ol"))
                is_list = _ratio(list_length, js_len(self._inner_text(node))) > 0.9
            if tag == "table" and node.data_table:
                return False
            if self._has_ancestor_tag(node, "table", -1, lambda table: bool(table.data_table)):
                return False
            if self._has_ancestor_tag(node, "code"):
                return False
            if any(table.data_table for table in node.get_elements_by_tag_name("table")):
                return False
            weight = self._class_weight(node)
            if weight < 0:
                return True
            if self._inner_text(node).count(",") >= 10:
                return False
            p = len(node.get_elements_by_tag_name("p"))
            img = len(node.get_elements_by_tag_name("img"))
            li = len(node.get_elements_by_tag_name("li")) - 100
            inputs = len(node.get_elements_by_tag_name("input"))
            heading_density = self._text_density(node, ("h1", "h2", "h3", "h4", "h5", "h6"))
            embed_count = 0
            for embed in node.get_elements_by_tag_name("object", "embed", "iframe"):
                if any(self.allowed_video.search(value) for value in embed.attrs.values()):
                    return False
                embed_count += 1
            inner_text = self._inner_text(node)
            if AD_WORDS.search(inner_text) or LOADING_WORDS.search(inner_text):
                return True
            content_length = js_len(inner_text)
            link_density = self._link_density(node)
            textish = ("span", "li", "td", *sorted(DIV_TO_P_ELEMS))
            text_density = self._text_density(node, textish)
            is_figure_child = self._has_ancestor_tag(node, "figure")
            failed = (
                (not is_figure_child and img > 1 and p / img < 0.5)
                or (not is_list and li > p)
                or (inputs > math.floor(p / 3))
                or (not is_list and not is_figure_child and heading_density < 0.9 and content_length < 25
                    and (img == 0 or img > 2) and link_density > 0)
                or (not is_list and weight < 25 and link_density > 0.2 + self.link_density_modifier)
                or (weight >= 25 and link_density > 0.5 + self.link_density_modifier)
                or ((embed_count == 1 and content_length < 75) or embed_count > 1)
                or (img == 0 and text_density == 0)
            )
            if is_list and failed:
                for child in node.element_children:
                    if len(child.element_children) > 1:
                        return failed
                if img == len(node.get_elements_by_tag_name("li")):
                    return False
            return failed
        self._remove_nodes(element.get_elements_by_tag_name(tag), should_remove)

    def _prep_article(self, article: Node) -> None:
        self._clean_styles(article)
        self._mark_data_tables(article)
        self._clean_conditionally(article, "form")
        self._clean_conditionally(article, "fieldset")
        for tag in ("object", "embed", "footer", "link", "aside"):
            self._clean(article, tag)
        for child in article.element_children:
            self._clean_matched_nodes(child, lambda node, match: bool(SHARE_ELEMENTS.search(match))
                                      and js_len(node.text_content) < DEFAULT_CHAR_THRESHOLD)
        for tag in ("iframe", "input", "textarea", "select", "button"):
            self._clean(article, tag)
        self._remove_nodes(article.get_elements_by_tag_name("h1", "h2"), lambda node: self._class_weight(node) < 0)
        self._clean_conditionally(article, "table")
        self._clean_conditionally(article, "ul")
        self._clean_conditionally(article, "div")
        for heading in article.get_elements_by_tag_name("h1"):
            self._set_node_tag(heading, "h2")
        self._remove_nodes(
            article.get_elements_by_tag_name("p"),
            lambda paragraph: not paragraph.get_elements_by_tag_name("img", "embed", "object", "iframe")
            and not self._inner_text(paragraph, False))
        for br in article.get_elements_by_tag_name("br"):
            following = self._skip_whitespace_nodes(br.next_sibling)
            if following is not None and following.type == ELEMENT and following.tag == "p":
                br.remove()
        for table in article.get_elements_by_tag_name("table"):
            body = table.first_element_child if self._has_single_tag_inside(table, "tbody") else table
            if self._has_single_tag_inside(body, "tr"):
                row = body.first_element_child
                if self._has_single_tag_inside(row, "td"):
                    cell = row.first_element_child
                    phrasing = all(self._is_phrasing(child) for child in cell.children)
                    cell = self._set_node_tag(cell, "p" if phrasing else "div")
                    if table.parent is not None:
                        table.parent.replace_child(cell, table)

    def _simplify_nested_elements(self, article: Node) -> None:
        node = article
        while node is not None:
            if node.parent is not None and node.tag in ("div", "section") and \
                    not node.id.startswith("readability"):
                if self._is_without_content(node):
                    node = self._remove_and_get_next(node)
                    continue
                if self._has_single_tag_inside(node, "div") or self._has_single_tag_inside(node, "section"):
                    child = node.element_children[0]
                    for name, value in node.attrs.items():
                        child.attrs[name] = value
                    node.parent.replace_child(child, node)
                    node = child
                    continue
            node = self._next_node(node)

    # --- entry point --------------------------------------------------------------------
    def parse(self) -> dict | None:
        if self.max_elems > 0:
            count = len(self.doc.get_elements_by_tag_name("*"))
            if count > self.max_elems:
                raise ReadabilityError(f"Aborting parsing document; {count} elements found")
        self._remove_placeholder_images()
        self._remove_nodes(self.doc.get_elements_by_tag_name("script", "noscript"))
        self._prep_document()
        self.metadata = self._metadata()
        self.article_title = self.metadata["title"] or ""
        article = self._grab_article()
        if article is None:
            return None
        self._simplify_nested_elements(article)
        return {"title": self.article_title, "byline": self.metadata.get("byline") or self.article_byline,
                "content": article}


def _js_int(value) -> int:
    """``parseInt(value, 10) || 0`` for an attribute value (NaN and absent become 0)."""
    if not value:
        return 0
    match = re.match(f"^{WS}*([+-]?[0-9]+)", value)
    return int(match.group(1)) if match else 0
