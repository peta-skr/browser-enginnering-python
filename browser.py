import socket
import ssl
import ctypes
import math
import sys
import sdl2
import skia
import OpenGL.GL
import urllib.parse
import dukpy


SESSIONS = {}
COOKIE_JAR = {}

class URL:
    def __init__(self, url):
        # スキーム（http or https）とそれ以降のURLを分割
        self.scheme, url = url.split("://", 1)
        assert self.scheme in ["http", "https"]

        # パスが省略されている場合は "/" を補完
        if "/" not in url:
            url = url + "/"

        # ホスト名とパスを分割
        self.host, url = url.split("/", 1)
        self.path = "/" + url

        # スキームに応じてデフォルトポートを設定
        if self.scheme == "http":
            self.port = 80
        elif self.scheme == "https":
            self.port = 443

        # ホスト名にポート番号が含まれている場合は上書き（例: localhost:8080）
        if ":" in self.host:
            self.host, port = self.host.split(":", 1)
            self.port = int(port)

    def request(self, referrer, payload=None):
        # TCPソケットを作成
        s = socket.socket(
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        s.connect((self.host, self.port))

        # HTTPSの場合はTLSでソケットをラップ
        if self.scheme == "https":
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(s, server_hostname=self.host)

        # HTTPリクエストを組み立てて送信
        method = "POST" if payload else "GET"
        request = "{} {} HTTP/1.0\r\n".format(method, self.path)
        request += "Host: {}\r\n".format(self.host)
        if self.host in COOKIE_JAR:
            cookie, params = COOKIE_JAR[self.host]
            allow_cookie = True
            if referrer and params.get("samesite", "none") == "lax":
                if method != "GET":
                    allow_cookie = self.host == referrer.host
            if allow_cookie:
                request += "Cookie: {}\r\n".format(cookie)
        if payload:
            length = len(payload.encode("utf8"))
            request += "Content-Length: {}\r\n".format(length)
        request += "\r\n"
        if payload:
            request += payload
        s.send(request.encode("utf8"))

        # レスポンスをテキストモードで読み込む
        response = s.makefile("r", encoding="utf8", newline="\r\n")

        # ステータスライン（例: "HTTP/1.0 200 OK"）を解析
        statusline = response.readline()
        version, status, explanation = statusline.split(" ", 2)

        # レスポンスヘッダーを辞書に格納（キーは小文字に正規化）
        response_headers = {}
        while True:
            line = response.readline()
            if line == "\r\n": break  # 空行でヘッダー終了
            header, value = line.split(":", 1)
            response_headers[header.casefold()] = value.strip()

        if "set-cookie" in response_headers:
            cookie = response_headers["set-cookie"]
            params = {}
            if ";" in cookie:
                cookie, rest = cookie.split(";", 1)
                for param in rest.split(";"):
                    if '=' in param:
                        param, value = param.split("=", 1)
                    else:
                        value = "true"
                    params[param.strip().casefold()] = value.casefold()
            COOKIE_JAR[self.host] = (cookie, params)

        # 本実装では非対応のエンコーディングが含まれていないことを確認
        assert "transfer-encoding" not in response_headers
        assert "content-encoding" not in response_headers

        # レスポンスボディを読み込んでソケットを閉じる
        content = response.read()
        s.close()

        return response_headers, content

    def resolve(self, url):
        """相対URLを絶対URLに変換して返す。
        絶対URL（://を含む）はそのままURLオブジェクトに変換する。
        "../" を含む相対パスはディレクトリを遡って解決する。"""
        if "://" in url: return URL(url)
        if not url.startswith("/"):
            dir, _ = self.path.rsplit("/", 1)
            while url.startswith("../"):
                _, url = url.split("/", 1)
                if "/" in dir:
                    dir, _ = dir.rsplit("/", 1)
            url = dir + "/" + url
        if url.startswith("//"):
            # プロトコル相対URL（//example.com/...）にスキームを補完
            return URL(self.scheme + ":" + url)
        else:
            # パス絶対URL（/path/...）にホスト・スキームを補完
            return URL(self.scheme + "://" + self.host + \
                       ":" + str(self.port) + url)

    def origin(self):
        return self.scheme + "://" + self.host + ":" + str(self.port)

    def __str__(self):
        port_part = ":" + str(self.port)
        if self.scheme == "https" and self.port == 443:
            port_part = ""
        if self.scheme == "http" and self.port == 80:
            port_part = ""
        return self.scheme + "://" + self.host + port_part + self.path


# ============================================================
# フォントシステム（Skia版）
# ============================================================
# Typefaceオブジェクトをキャッシュするグローバル辞書
# キーは(weight, style)で、Typefaceは重いオブジェクトなので再生成を避ける
FONTS = {}

def get_font(size, weight, style):
    """Skia版フォント取得。Typefaceをキャッシュし、Fontはサイズ付きで毎回生成（軽量）"""
    key = (weight, style)
    if key not in FONTS:
        if weight == "bold":
            skia_weight = skia.FontStyle.kBold_Weight
        else:
            skia_weight = skia.FontStyle.kNormal_Weight
        if style == "italic":
            skia_slant = skia.FontStyle.kItalic_Slant
        else:
            skia_slant = skia.FontStyle.kUpright_Slant
        skia_width = skia.FontStyle.kNormal_Width
        style_info = skia.FontStyle(skia_weight, skia_width, skia_slant)
        FONTS[key] = skia.Typeface('Arial', style_info)
    return skia.Font(FONTS[key], size)

def linespace(font):
    """Skiaのフォントメトリクスから行間（行の高さ）を求める。
    Skiaではascentが負の値（上方向が負）なので、descent - ascent で正の行高になる。"""
    metrics = font.getMetrics()
    return metrics.fDescent - metrics.fAscent

def font_measureText(font, text):
    """Skiaのフォントでテキスト幅を測定する。Tkinterのfont.measure()の代替。"""
    return font.measureText(text)


# ============================================================
# 色のパースと名前付き色
# ============================================================
NAMED_COLORS = {
    "black": "#000000",
    "gray":  "#808080",
    "white": "#ffffff",
    "red":   "#ff0000",
    "green": "#00ff00",
    "blue":  "#0000ff",
    "lightblue": "#add8e6",
    "lightgreen": "#90ee90",
    "orange": "#ffa500",
    "orangered": "#ff4500",
}

def parse_color(color):
    """CSS色文字列をskia.Colorに変換する。
    #rrggbb (7文字), #rrggbbaa (9文字), 名前付き色に対応。"""
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return skia.Color(r, g, b)
    elif color.startswith("#") and len(color) == 9:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        a = int(color[7:9], 16)
        return skia.Color(r, g, b, a)
    elif color in NAMED_COLORS:
        return parse_color(NAMED_COLORS[color])
    else:
        return skia.ColorBLACK

def parse_blend_mode(blend_mode_str):
    """CSSのmix-blend-mode文字列をSkiaのBlendMode定数に変換する"""
    if blend_mode_str == "multiply":
        return skia.BlendMode.kMultiply
    elif blend_mode_str == "difference":
        return skia.BlendMode.kDifference
    elif blend_mode_str == "destination-in":
        return skia.BlendMode.kDstIn
    elif blend_mode_str == "source-over":
        return skia.BlendMode.kSrcOver
    else:
        return skia.BlendMode.kSrcOver


# ============================================================
# 定数
# ============================================================
WIDTH, HEIGHT = 800, 600    # ウィンドウの幅と高さ（ピクセル）
HSTEP, VSTEP = 13, 18       # 水平・垂直方向の初期オフセット
SCROLL_STEP = 100            # 1回のスクロール量（ピクセル）


# ============================================================
# レイアウトクラス群
# ============================================================
class BlockLayout:
    """HTMLノード1つに対応するレイアウトオブジェクト。
    ブロックモード（子を縦に積む）とインラインモード（テキストを横に並べる）
    の2種類のレイアウトを担当する。"""

    BLOCK_ELEMENTS = [
        "html", "body", "article", "section", "nav", "aside",
        "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
        "footer", "address", "p", "hr", "pre", "blockquote",
        "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
        "figcaption", "main", "div", "table", "form", "fieldset",
        "legend", "details", "summary"
    ]

    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = None

    def word(self, node, word):
        """1単語を行バッファに追加する。行幅を超える場合は新しい行へ折り返す"""
        weight = node.style["font-weight"]
        style = node.style["font-style"]
        if style == "normal": style = "roman"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)
        w = font_measureText(font, word)

        if self.cursor_x + w > self.width:
            self.new_line()

        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        text = TextLayout(node, word, line, previous_word)
        line.children.append(text)
        self.cursor_x += w + font_measureText(font, " ")

    def recurse(self, node):
        """HTMLツリーを深さ優先で再帰走査し、テキストを単語単位でレイアウトする"""
        if isinstance(node, Text):
            for word in node.text.split():
                self.word(node, word)
        else:
            if node.tag == "br":
                self.new_line()
            elif node.tag == "input" or node.tag == "button":
                self.input(node)
            else:
                for child in node.children:
                    self.recurse(child)

    def layout_mode(self):
        if isinstance(self.node, Text):
            return "inline"
        elif any(isinstance(child, Element) and \
                child.tag in self.BLOCK_ELEMENTS
                for child in self.node.children):
            return "block"
        elif self.node.children or self.node.tag == "input":
            return "inline"
        else:
            return "block"

    def layout(self):
        self.x = self.parent.x
        self.width = self.parent.width

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        mode = self.layout_mode()

        if mode == "block":
            previous = None
            for child in self.node.children:
                next_child = BlockLayout(child, self, previous)
                self.children.append(next_child)
                previous = next_child
        else:
            self.cursor_x = 0
            self.new_line()
            self.recurse(self.node)

        for child in self.children:
            child.layout()

        self.height = sum([child.height for child in self.children])

    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x, self.y,
            self.x + self.width, self.y + self.height)

    def paint(self):
        cmds = []
        if isinstance(self.node, Element):
            bgcolor = self.node.style.get("background-color", "transparent")
            if bgcolor != "transparent":
                radius = float(self.node.style.get("border-radius", "0px")[:-2])
                cmds.append(DrawRRect(self.self_rect(), radius, bgcolor))
        return cmds

    def paint_effects(self, cmds):
        """ビジュアルエフェクトを適用する（opacity, blend-mode, overflow clip）"""
        cmds = paint_visual_effects(self.node, cmds, self.self_rect())
        return cmds

    def input(self, node):
        weight = node.style["font-weight"]
        style = node.style["font-style"]
        if style == "normal": style = "roman"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)
        w = INPUT_WIDTH_PX

        if self.cursor_x + w > self.width:
            self.new_line()

        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        input_layout = InputLayout(node, line, previous_word)
        line.children.append(input_layout)
        self.cursor_x += w + font_measureText(font, " ")

    def new_line(self):
        self.cursor_x = 0
        last_line = self.children[-1] if self.children else None
        new_line = LineLayout(self.node, self, last_line)
        self.children.append(new_line)

    def should_paint(self):
        return isinstance(self.node, Text) or \
        (self.node.tag != "input" and self.node.tag != "button")


class DocumentLayout:
    """ページ全体のルートレイアウト"""

    def __init__(self, node):
        self.node = node
        self.parent = None
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = None

    def layout(self):
        self.width = WIDTH - 2*HSTEP
        self.x = HSTEP
        self.y = VSTEP
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout()
        self.height = child.height

    def paint(self):
        return []

    def paint_effects(self, cmds):
        return cmds

    def should_paint(self):
        return True


class Text:
    """HTMLテキストノード"""

    def __init__(self, text, parent):
        self.text = text
        self.children = []
        self.parent = parent
        self.is_focused = False

    def __repr__(self):
        return repr(self.text)


class Element:
    """HTMLタグノード"""

    def __init__(self, tag, attributes, parent):
        self.tag = tag
        self.attributes = attributes
        self.children = []
        self.parent = parent
        self.is_focused = False

    def __repr__(self):
        return "<" + self.tag + ">"


class HTMLParser:
    """HTMLソーステキストをパースしてDOMツリーを構築する"""

    SELF_CLOSING_TAGS = [
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ]
    HEAD_TAGS = [
        "base", "basefont", "bgsound", "noscript",
        "link", "meta", "title", "style", "script",
    ]

    def __init__(self, body):
        self.body = body
        self.unfinished = []

    def parse(self):
        text = ""
        in_tag = False
        for c in self.body:
            if c == "<":
                in_tag = True
                if text: self.add_text(text)
                text = ""
            elif c == ">":
                in_tag = False
                self.add_tag(text)
                text = ""
            else:
                text += c
        if not in_tag and text:
            self.add_text(text)
        return self.finish()

    def add_text(self, text):
        if text.isspace(): return
        self.implicit_tags(None)
        parent = self.unfinished[-1]
        node = Text(text, parent)
        parent.children.append(node)

    def add_tag(self, tag):
        tag, attributes = self.get_attributes(tag)
        if tag.startswith("!"): return
        self.implicit_tags(tag)

        if tag.startswith("/"):
            if len(self.unfinished) == 1: return
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        elif tag in self.SELF_CLOSING_TAGS:
            parent = self.unfinished[-1]
            node = Element(tag, attributes, parent)
            parent.children.append(node)
        else:
            parent = self.unfinished[-1] if self.unfinished else None
            node = Element(tag, attributes, parent)
            self.unfinished.append(node)

    def finish(self):
        if not self.unfinished:
            self.implicit_tags(None)
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        return self.unfinished.pop()

    def get_attributes(self, text):
        parts = text.split()
        tag = parts[0].casefold()
        attributes = {}
        for attrpair in parts[1:]:
            if "=" in attrpair:
                key, value = attrpair.split("=", 1)
                if len(value) > 2 and value[0] in ["'", "\""]:
                    value = value[1:-1]
                attributes[key.casefold()] = value
            else:
                attributes[attrpair.casefold()] = ""
        return tag, attributes

    def implicit_tags(self, tag):
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if open_tags == [] and tag != "html":
                self.add_tag("html")
            elif open_tags == ["html"] \
                    and tag not in ["head", "body", "/html"]:
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")
            elif open_tags == ["html", "head"] and \
                    tag not in ["/head"] + self.HEAD_TAGS:
                self.add_tag("/head")
            else:
                break


# ============================================================
# 描画コマンド群（Skia Canvas API版）
# ============================================================
class DrawText:
    """テキスト描画コマンド（Skia版）"""

    def __init__(self, x1, y1, text, font, color):
        self.left = x1
        self.top = y1
        self.text = text
        self.font = font
        self.color = color
        self.bottom = y1 + linespace(font)
        self.rect = skia.Rect.MakeLTRB(x1, y1, x1 + font_measureText(font, text), self.bottom)

    def execute(self, canvas):
        paint = skia.Paint(AntiAlias=True, Color=parse_color(self.color))
        baseline = self.top - self.font.getMetrics().fAscent
        canvas.drawString(self.text, float(self.left), baseline, self.font, paint)


class DrawRect:
    """矩形描画コマンド（Skia版）"""

    def __init__(self, x1, y1, x2, y2, color):
        self.top = y1
        self.left = x1
        self.bottom = y2
        self.right = x2
        self.color = color
        self.rect = skia.Rect.MakeLTRB(x1, y1, x2, y2)

    def execute(self, canvas):
        paint = skia.Paint(Color=parse_color(self.color))
        canvas.drawRect(self.rect, paint)


class DrawRRect:
    """角丸矩形描画コマンド。CSSのborder-radiusに対応する。"""

    def __init__(self, rect, radius, color):
        self.rect = rect
        self.rrect = skia.RRect.MakeRectXY(rect, radius, radius)
        self.color = color
        self.top = rect.top()
        self.bottom = rect.bottom()

    def execute(self, canvas):
        paint = skia.Paint(Color=parse_color(self.color))
        canvas.drawRRect(self.rrect, paint)


class DrawLine:
    """直線描画コマンド（Skia版）"""

    def __init__(self, x1, y1, x2, y2, color, thickness):
        self.left = x1
        self.top = y1
        self.right = x2
        self.bottom = y2
        self.color = color
        self.thickness = thickness
        self.rect = skia.Rect.MakeLTRB(x1, y1, x2, y2)

    def execute(self, canvas):
        path = skia.Path()
        path.moveTo(self.left, self.top)
        path.lineTo(self.right, self.bottom)
        paint = skia.Paint(
            Color=parse_color(self.color),
            StrokeWidth=self.thickness,
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawPath(path, paint)


class DrawOutline:
    """矩形の枠線描画コマンド（Skia版）"""

    def __init__(self, rect, color, thickness):
        self.rect = rect
        self.color = color
        self.thickness = thickness
        self.top = rect.top()
        self.bottom = rect.bottom()

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
            StrokeWidth=self.thickness,
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawRect(self.rect, paint)


# ============================================================
# ビジュアルエフェクト: Blendコマンド
# ============================================================
class Blend:
    """OpacityとBlendModeを統合したビジュアルエフェクトコマンド。
    saveLayerで新しいサーフェスを作り、子コマンドを描画し、restoreで親へブレンドする。
    最適化: opacity=1.0かつblend_mode無しの場合はsaveLayerをスキップする。"""

    def __init__(self, opacity, blend_mode, children):
        self.opacity = opacity
        self.blend_mode = blend_mode
        self.children = children
        self.should_save = self.blend_mode or self.opacity < 1
        self.rect = skia.Rect.MakeEmpty()
        for cmd in self.children:
            self.rect.join(cmd.rect)
        self.top = self.rect.top()
        self.bottom = self.rect.bottom()

    def execute(self, canvas):
        paint = skia.Paint(
            Alphaf=self.opacity,
            BlendMode=parse_blend_mode(self.blend_mode),
        )
        if self.should_save:
            canvas.saveLayer(None, paint)
        for cmd in self.children:
            cmd.execute(canvas)
        if self.should_save:
            canvas.restore()


def paint_visual_effects(node, cmds, rect):
    """ノードのCSSプロパティに基づいてビジュアルエフェクトを適用する。
    opacity, mix-blend-mode, overflow:clip をまとめて処理する。"""
    opacity = float(node.style.get("opacity", "1.0"))
    blend_mode = node.style.get("mix-blend-mode")

    if node.style.get("overflow", "visible") == "clip":
        border_radius = float(node.style.get("border-radius", "0px")[:-2])
        if not blend_mode:
            blend_mode = "source-over"
        cmds.append(Blend(1.0, "destination-in", [
            DrawRRect(rect, border_radius, "white")
        ]))

    return [Blend(opacity, blend_mode, cmds)]


# ============================================================
# CSS関連
# ============================================================
INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "color": "black",
}


class CSSParser:
    def __init__(self, s):
        self.s = s
        self.i = 0

    def whitespace(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def word(self):
        start = self.i
        while self.i < len(self.s):
            if self.s[self.i].isalnum() or self.s[self.i] in "#-.%":
                self.i += 1
            else:
                break
        if not (self.i > start):
            raise Exception("Parsing error")
        return self.s[start:self.i]

    def literal(self, literal):
        if not (self.i < len(self.s) and self.s[self.i] == literal):
            raise Exception("Parsing error")
        self.i += 1

    def pair(self):
        prop = self.word()
        self.whitespace()
        self.literal(":")
        self.whitespace()
        val = self.word()
        return prop.casefold(), val

    def body(self):
        pairs = {}
        while self.i < len(self.s) and self.s[self.i] != "}":
            try:
                prop, val = self.pair()
                pairs[prop] = val
                self.whitespace()
                self.literal(";")
                self.whitespace()
            except Exception:
                why = self.ignore_until([";", "}"])
                if why == ";":
                    self.literal(";")
                    self.whitespace()
                else:
                    break
        return pairs

    def ignore_until(self, chars):
        while self.i < len(self.s):
            if self.s[self.i] in chars:
                return self.s[self.i]
            else:
                self.i += 1
        return None

    def selector(self):
        out = TagSelector(self.word().casefold())
        self.whitespace()
        while self.i < len(self.s) and self.s[self.i] != "{":
            tag = self.word()
            descendant = TagSelector(tag.casefold())
            out = DescendantSelector(out, descendant)
            self.whitespace()
        return out

    def parse(self):
        rules = []
        while self.i < len(self.s):
            try:
                self.whitespace()
                selector = self.selector()
                self.literal("{")
                self.whitespace()
                body = self.body()
                self.literal("}")
                rules.append((selector, body))
            except Exception:
                why = self.ignore_until(["}"])
                if why == "}":
                    self.literal("}")
                    self.whitespace()
                else:
                    break
        return rules


class TagSelector:
    def __init__(self, tag):
        self.tag = tag
        self.priority = 1

    def matches(self, node):
        return isinstance(node, Element) and self.tag == node.tag


class DescendantSelector:
    def __init__(self, ancestor, descendant):
        self.ancestor = ancestor
        self.descendant = descendant
        self.priority = ancestor.priority + descendant.priority

    def matches(self, node):
        if not self.descendant.matches(node): return False
        while node.parent:
            if self.ancestor.matches(node.parent): return True
            node = node.parent
        return False


# ============================================================
# インラインレイアウトクラス群
# ============================================================
class LineLayout:
    """インラインモードの1行分"""

    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []

    def layout(self):
        self.width = self.parent.width
        self.x = self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        for word in self.children:
            word.layout()

        if not self.children:
            self.height = 0
            return

        # ベースライン揃え（Skia版: linespace/ascentを使用）
        max_ascent = max([-word.font.getMetrics().fAscent
                          for word in self.children])
        baseline = self.y + 1.25 * max_ascent
        for word in self.children:
            word.y = baseline + word.font.getMetrics().fAscent
        max_descent = max([word.font.getMetrics().fDescent
                           for word in self.children])
        self.height = 1.25 * (max_ascent + max_descent)

    def paint(self):
        return []

    def paint_effects(self, cmds):
        return cmds

    def should_paint(self):
        return True


class TextLayout:
    """1単語分のレイアウトオブジェクト"""

    def __init__(self, node, word, parent, previous):
        self.node = node
        self.word = word
        self.children = []
        self.parent = parent
        self.previous = previous

    def layout(self):
        weight = self.node.style["font-weight"]
        style = self.node.style["font-style"]
        if style == "normal": style = "roman"
        size = int(float(self.node.style["font-size"][:-2]) * .75)
        self.font = get_font(size, weight, style)

        self.width = font_measureText(self.font, self.word)

        if self.previous:
            space = font_measureText(self.previous.font, " ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

    def paint(self):
        color = self.node.style["color"]
        return [DrawText(self.x, self.y, self.word, self.font, color)]

    def paint_effects(self, cmds):
        return cmds

    def should_paint(self):
        return True


INPUT_WIDTH_PX = 200


class InputLayout:
    """<input>/<button> 要素のインラインレイアウト"""

    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []

    def layout(self):
        weight = self.node.style["font-weight"]
        style = self.node.style["font-style"]
        if style == "normal": style = "roman"
        size = int(float(self.node.style["font-size"][:-2]) * .75)
        self.font = get_font(size, weight, style)

        self.width = INPUT_WIDTH_PX

        if self.previous:
            space = font_measureText(self.previous.font, " ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

    def should_paint(self):
        return True

    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x, self.y,
            self.x + self.width, self.y + self.height)

    def paint(self):
        cmds = []
        bgcolor = self.node.style.get("background-color", "transparent")
        if bgcolor != "transparent":
            rect = DrawRect(self.x, self.y,
                            self.x + self.width, self.y + self.height, bgcolor)
            cmds.append(rect)

        if self.node.tag == "input":
            text = self.node.attributes.get("value", "")
        elif self.node.tag == "button":
            if len(self.node.children) == 1 and \
               isinstance(self.node.children[0], Text):
                text = self.node.children[0].text
            else:
                print("Ignoring HTML contents inside button")
                text = ""

        color = self.node.style["color"]
        cmds.append(DrawText(self.x, self.y, text, self.font, color))

        if self.node.is_focused:
            cx = self.x + font_measureText(self.font, text)
            cmds.append(DrawLine(cx, self.y, cx, self.y + self.height, "black", 1))

        return cmds

    def paint_effects(self, cmds):
        cmds = paint_visual_effects(self.node, cmds, self.self_rect())
        return cmds


# ============================================================
# スタイル適用
# ============================================================
def style(node, rules):
    node.style = {}

    for property, default_value in INHERITED_PROPERTIES.items():
        if node.parent:
            node.style[property] = node.parent.style[property]
        else:
            node.style[property] = default_value

    if isinstance(node, Element):
        for selector, body in rules:
            if not selector.matches(node): continue
            for property, value in body.items():
                node.style[property] = value

        if "style" in node.attributes:
            pairs = CSSParser(node.attributes["style"]).body()
            for property, value in pairs.items():
                node.style[property] = value

    if node.style["font-size"].endswith("%"):
        if node.parent:
            parent_font_size = node.parent.style["font-size"]
        else:
            parent_font_size = INHERITED_PROPERTIES["font-size"]
        node_pct = float(node.style["font-size"][:-1]) / 100
        parent_px = float(parent_font_size[:-2])
        node.style["font-size"] = str(node_pct * parent_px) + "px"

    for child in node.children:
        style(child, rules)


def print_tree(node, indent=0):
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


def paint_tree(layout_object, display_list):
    """レイアウトツリーを走査し、描画コマンドをツリー構造でdisplay_listに収集する。
    paint_effects()でビジュアルエフェクト（opacity, blend等）を各ノードに適用する。"""
    if layout_object.should_paint():
        cmds = layout_object.paint()
    else:
        cmds = []

    for child in layout_object.children:
        paint_tree(child, cmds)

    if layout_object.should_paint():
        cmds = layout_object.paint_effects(cmds)

    display_list.extend(cmds)


def tree_to_list(tree, result):
    result.append(tree)
    for child in tree.children:
        tree_to_list(child, result)
    return result


def cascade_priority(rule):
    selector, body = rule
    return selector.priority


# ============================================================
# デフォルトスタイルシートとJSランタイム
# ============================================================
DEFAULT_STYLE_SHEET = CSSParser(open("browser.css", encoding="utf-8").read()).parse()

RUNTIME_JS = open("runtime.js", encoding="utf-8").read()

EVENT_DISPATCH_JS = \
    "new Node(dukpy.handle).dispatchEvent(new Event(dukpy.type))"

class JSContext:
    def __init__(self, tab):
        self.tab = tab
        self.interp = dukpy.JSInterpreter()

        self.interp.export_function("log", print)
        self.interp.export_function("querySelectorAll", self.querySelectorAll)
        self.interp.export_function("getAttribute", self.getAttribute)
        self.interp.export_function("innerHTML_set", self.innerHTML_set)

        self.interp.evaljs(RUNTIME_JS)

        self.node_to_handle = {}
        self.handle_to_node = {}

    def run(self, script, code):
        try:
            return self.interp.evaljs(code)
        except dukpy.JSRuntimeError as e:
            print("Script", script, "crashed", e)

    def querySelectorAll(self, selector_text):
        selector = CSSParser(selector_text).selector()
        nodes = [node for node in tree_to_list(self.tab.nodes, [])
                 if selector.matches(node)]
        return [self.get_handle(node) for node in nodes]

    def get_handle(self, elt):
        if elt not in self.node_to_handle:
            handle = len(self.node_to_handle)
            self.node_to_handle[elt] = handle
            self.handle_to_node[handle] = elt
        else:
            handle = self.node_to_handle[elt]
        return handle

    def getAttribute(self, handle, attr):
        elt = self.handle_to_node[handle]
        attr = elt.attributes.get(attr, None)
        return attr if attr else ""

    def dispatch_event(self, type, elt):
        handle = self.node_to_handle.get(elt, -1)
        do_default = self.interp.evaljs(
            EVENT_DISPATCH_JS, type=type, handle=handle
        )
        return not do_default

    def innerHTML_set(self, handle, s):
        doc = HTMLParser("<html><body>" + s + "</body></html>").parse()
        new_nodes = doc.children[0].children
        elt = self.handle_to_node[handle]
        elt.children = new_nodes
        for child in elt.children:
            child.parent = elt
        self.tab.render()

    def XMLHttpRequest_send(self, method, url, body):
        full_url = self.tab.url.resolve(url)
        if not self.tab.allowed_request(full_url):
            raise Exception("Cross-origin XHR blocked by CSP")
        if full_url.origin() != self.tab.url.origin():
            raise Exception("Cross-origin XHR request not allowed")
        headers, out = full_url.request(self.tab.url, body)
        return out


# ============================================================
# Rect ユーティリティ（Chrome UI のヒットテスト用）
# ============================================================
class Rect:
    """矩形領域を表すユーティリティクラス。Chrome UIのヒットテスト等に使用。
    skia.Rectとは別に、整数座標ベースのUIレイアウト用に残す。"""

    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def contains_point(self, x, y):
        return x >= self.left and x < self.right \
            and y >= self.top and y < self.bottom

    def to_skia(self):
        """skia.Rectに変換する"""
        return skia.Rect.MakeLTRB(self.left, self.top, self.right, self.bottom)


# ============================================================
# Tab クラス
# ============================================================
class Tab:
    def __init__(self, tab_height):
        self.url = None
        self.scroll = 0
        self.tab_height = tab_height
        self.history = []
        self.nodes = []
        self.rules = []
        self.focus = None

    def load(self, url, payload=None):
        self.history.append(url)
        self.url = url
        headers, body = url.request(None, payload)
        self.nodes = HTMLParser(body).parse()

        self.allowed_origins = None
        if "content-security-policy" in headers:
            csp = headers["content-security-policy"].split()
            if len(csp) > 0 and csp[0] == "default-src":
                self.allowed_origins = []
                for origin in csp[1:]:
                    self.allowed_origins.append(URL(origin).origin())

        scripts = [node.attributes["src"] for node in tree_to_list(self.nodes, [])
                   if isinstance(node, Element)
                   and node.tag == "script"
                   and "src" in node.attributes]

        self.js = JSContext(self)
        for script in scripts:
            script_url = url.resolve(script)
            if not self.allowed_request(script_url):
                print("Blocked script", script, "due to CSP")
                continue
            try:
                _, body = script_url.request(url)
            except:
                continue
            self.js.run(script, body)

        self.rules = DEFAULT_STYLE_SHEET.copy()

        links = [node.attributes["href"]
                 for node in tree_to_list(self.nodes, [])
                 if isinstance(node, Element)
                 and node.tag == "link"
                 and node.attributes.get("rel") == "stylesheet"
                 and "href" in node.attributes]
        for link in links:
            style_url = url.resolve(link)
            if not self.allowed_request(style_url):
                print("Blocked stylesheet", link, "due to CSP")
                continue
            try:
                _, css_body = style_url.request(url)
            except Exception:
                continue
            self.rules.extend(CSSParser(css_body).parse())

        self.render()

    def render(self):
        style(self.nodes, sorted(self.rules, key=cascade_priority))
        self.document = DocumentLayout(self.nodes)
        self.document.layout()
        self.display_list = []
        paint_tree(self.document, self.display_list)

    def scrolldown(self):
        max_y = max(
            self.document.height + 2*VSTEP - self.tab_height, 0)
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)

    def click(self, x, y):
        self.focus = None
        y += self.scroll

        objs = [obj for obj in tree_to_list(self.document, [])
                if obj.x <= x < obj.x + obj.width
                and obj.y <= y < obj.y + obj.height]

        if not objs: return
        elt = objs[-1].node

        if self.focus:
            self.focus.is_focused = False

        while elt:
            if isinstance(elt, Text):
                pass
            elif elt.tag == "a" and "href" in elt.attributes:
                if self.js.dispatch_event("click", elt): return
                url = self.url.resolve(elt.attributes["href"])
                return self.load(url)
            elif elt.tag == "input":
                if self.js.dispatch_event("click", elt): return
                elt.attributes["value"] = ""
                self.focus = elt
                elt.is_focused = True
                return self.render()
            elif elt.tag == "button":
                if self.js.dispatch_event("click", elt): return
                while elt:
                    if elt.tag == "form" and "action" in elt.attributes:
                        return self.submit_form(elt)
                    elt = elt.parent
                return
            elt = elt.parent

    def go_back(self):
        if len(self.history) > 1:
            self.history.pop()
            back = self.history.pop()
            self.load(back)

    def keypress(self, char):
        if self.focus:
            if self.js.dispatch_event("keydown", self.focus): return
            self.focus.attributes["value"] += char
            self.render()

    def submit_form(self, elt):
        if self.js.dispatch_event("submit", elt): return
        inputs = [node for node in tree_to_list(elt, [])
                  if isinstance(node, Element)
                  and node.tag == "input"
                  and "name" in node.attributes]

        body = ""
        for input in inputs:
            name = input.attributes["name"]
            value = input.attributes.get("value", "")
            name = urllib.parse.quote(name)
            value = urllib.parse.quote(value)
            body += "&" + name + "=" + value
        body = body[1:]

        url = self.url.resolve(elt.attributes["action"])
        self.load(url, body)

    def allowed_request(self, url):
        return self.allowed_origins == None or \
            url.origin() in self.allowed_origins


# ============================================================
# Chrome クラス（ブラウザUI）
# ============================================================
class Chrome:
    def __init__(self, browser):
        self.browser = browser
        self.font = get_font(20, "normal", "roman")
        self.font_height = linespace(self.font)
        self.padding = 5

        self.tabbar_top = 0
        self.tabbar_bottom = self.font_height + 2*self.padding

        plus_width = font_measureText(self.font, "+") + 2*self.padding
        self.newtab_rect = Rect(
            self.padding, self.padding,
            self.padding + plus_width,
            self.padding + self.font_height
        )

        self.bottom = self.tabbar_bottom
        self.urlbar_top = self.tabbar_bottom
        self.urlbar_bottom = self.urlbar_top + \
            self.font_height + 2*self.padding
        self.bottom = self.urlbar_bottom

        back_width = font_measureText(self.font, "<") + 2*self.padding
        self.back_rect = Rect(
            self.padding,
            self.urlbar_top + self.padding,
            self.padding + back_width,
            self.urlbar_bottom - self.padding)

        self.address_rect = Rect(
            self.back_rect.right + self.padding,
            self.urlbar_top + self.padding,
            WIDTH - self.padding,
            self.urlbar_bottom - self.padding)

        self.focus = None
        self.address_bar = ""

    def tab_rect(self, i):
        tabs_start = self.newtab_rect.right + self.padding
        tab_width = font_measureText(self.font, "Tab X") + 2*self.padding
        return Rect(
            tabs_start + tab_width * i, self.tabbar_top,
            tabs_start + tab_width * (i + 1), self.tabbar_bottom
        )

    def paint(self):
        cmds = []

        cmds.append(DrawRect(0, 0, WIDTH, self.bottom, "white"))

        cmds.append(DrawOutline(self.newtab_rect.to_skia(), "black", 1))
        cmds.append(DrawText(
            self.newtab_rect.left + self.padding,
            self.newtab_rect.top,
            "+", self.font, "black"
        ))

        for i, tab in enumerate(self.browser.tabs):
            bounds = self.tab_rect(i)
            cmds.append(DrawLine(
                bounds.left, 0, bounds.left, bounds.bottom,
                "black", 1))
            cmds.append(DrawLine(
                bounds.right, 0, bounds.right, bounds.bottom,
                "black", 1))
            cmds.append(DrawText(
                bounds.left + self.padding, bounds.top + self.padding,
                "Tab {}".format(i), self.font, "black"))

            if tab == self.browser.active_tab:
                cmds.append(DrawLine(
                    0, bounds.bottom, bounds.left, bounds.bottom,
                    "black", 1))
                cmds.append(DrawLine(
                    bounds.right, bounds.bottom, WIDTH, bounds.bottom,
                    "black", 1))

        cmds.append(DrawLine(
            0, self.bottom, WIDTH,
            self.bottom, "black", 1))

        cmds.append(DrawOutline(self.back_rect.to_skia(), "black", 1))
        cmds.append(DrawText(
            self.back_rect.left + self.padding,
            self.back_rect.top,
            "<", self.font, "black"))

        cmds.append(DrawOutline(self.address_rect.to_skia(), "black", 1))

        if self.focus == "address bar":
            cmds.append(DrawText(self.address_rect.left + self.padding,
                                 self.address_rect.top,
                                 self.address_bar, self.font, "black"))
            w = font_measureText(self.font, self.address_bar)
            cmds.append(DrawLine(
                self.address_rect.left + self.padding + w,
                self.address_rect.top,
                self.address_rect.left + self.padding + w,
                self.address_rect.bottom,
                "red", 1))
        else:
            url = str(self.browser.active_tab.url)
            cmds.append(DrawText(
                self.address_rect.left + self.padding,
                self.address_rect.top,
                url, self.font, "black"))
        return cmds

    def click(self, x, y):
        self.focus = None
        if self.newtab_rect.contains_point(x, y):
            self.browser.new_tab(URL("https://browser.engineering/"))
        elif self.back_rect.contains_point(x, y):
            self.browser.active_tab.go_back()
        elif self.address_rect.contains_point(x, y):
            self.focus = "address bar"
            self.address_bar = ""
        else:
            for i, tab in enumerate(self.browser.tabs):
                if self.tab_rect(i).contains_point(x, y):
                    self.browser.active_tab = tab
                    break

    def blur(self):
        self.focus = None

    def keypress(self, char):
        if self.focus == "address bar":
            self.address_bar += char
            return True
        return False

    def enter(self):
        if self.focus == "address bar":
            self.browser.active_tab.load(URL(self.address_bar))
            self.focus = None


# ============================================================
# Browser クラス（トップレベル・SDL/Skia版）
# ============================================================

# SDL_CreateRGBSurfaceFromに渡すマスク値（RGBA各チャンネルの位置を指定）
RED_MASK = 0x000000ff
GREEN_MASK = 0x0000ff00
BLUE_MASK = 0x00ff0000
ALPHA_MASK = 0xff000000


class Browser:
    """ブラウザのトップレベルクラス。SDLウィンドウの管理、Skiaサーフェスの管理、
    タブ管理、イベントハンドリングを統括する。"""

    def __init__(self):
        self.tabs = []
        self.active_tab = None

        # SDLの初期化とウィンドウ作成
        sdl2.SDL_Init(sdl2.SDL_INIT_EVENTS)
        self.sdl_window = sdl2.SDL_CreateWindow(
            b"Browser",
            sdl2.SDL_WINDOWPOS_CENTERED,
            sdl2.SDL_WINDOWPOS_CENTERED,
            WIDTH, HEIGHT,
            sdl2.SDL_WINDOW_SHOWN)

        # Skiaのルートサーフェス（最終合成結果をここに描画しSDLへコピー）
        self.root_surface = skia.Surface.MakeRaster(
            skia.ImageInfo.Make(
                WIDTH, HEIGHT,
                ct=skia.kRGBA_8888_ColorType,
                at=skia.kUnpremul_AlphaType))

        # ChromeとTabのサーフェスを初期化
        self.chrome = Chrome(self)
        self.chrome_surface = skia.Surface(WIDTH, math.ceil(self.chrome.bottom))
        self.tab_surface = None

    def raster_tab(self):
        """タブのコンテンツをtab_surfaceにラスタライズ（描画）する"""
        tab = self.active_tab
        tab_height = math.ceil(tab.document.height + 2*VSTEP)
        if self.tab_surface is None or \
           self.tab_surface.width() != WIDTH or \
           self.tab_surface.height() != tab_height:
            self.tab_surface = skia.Surface(WIDTH, tab_height)

        canvas = self.tab_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)
        for cmd in tab.display_list:
            cmd.execute(canvas)

    def raster_chrome(self):
        """Chrome UIをchrome_surfaceにラスタライズする"""
        canvas = self.chrome_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)
        for cmd in self.chrome.paint():
            cmd.execute(canvas)

    def draw(self):
        """tab_surfaceとchrome_surfaceをroot_surfaceに合成し、SDLウィンドウへコピーする"""
        canvas = self.root_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        # タブコンテンツをクリップして適切な位置に描画
        tab_rect = skia.Rect.MakeLTRB(0, self.chrome.bottom, WIDTH, HEIGHT)
        tab_offset = self.chrome.bottom - self.active_tab.scroll
        canvas.save()
        canvas.clipRect(tab_rect)
        if self.tab_surface:
            self.tab_surface.draw(canvas, 0, tab_offset)
        canvas.restore()

        # Chrome UIを上に重ねて描画
        chrome_rect = skia.Rect.MakeLTRB(0, 0, WIDTH, self.chrome.bottom)
        canvas.save()
        canvas.clipRect(chrome_rect)
        self.chrome_surface.draw(canvas, 0, 0)
        canvas.restore()

        # SkiaサーフェスのピクセルをSDLウィンドウにコピー
        skia_image = self.root_surface.makeImageSnapshot()
        skia_bytes = skia_image.tobytes()

        depth = 32
        pitch = 4 * WIDTH
        sdl_surface = sdl2.SDL_CreateRGBSurfaceFrom(
            skia_bytes, WIDTH, HEIGHT, depth, pitch,
            RED_MASK, GREEN_MASK, BLUE_MASK, ALPHA_MASK)

        rect = sdl2.SDL_Rect(0, 0, WIDTH, HEIGHT)
        window_surface = sdl2.SDL_GetWindowSurface(self.sdl_window)
        sdl2.SDL_BlitSurface(sdl_surface, rect, window_surface, rect)
        sdl2.SDL_UpdateWindowSurface(self.sdl_window)

    def new_tab(self, url):
        new_tab = Tab(HEIGHT - self.chrome.bottom)
        new_tab.load(url)
        self.active_tab = new_tab
        self.tabs.append(new_tab)
        self.raster_tab()
        self.raster_chrome()
        self.draw()

    def handle_quit(self):
        pass

    def handle_down(self):
        self.active_tab.scrolldown()
        self.draw()

    def handle_click(self, e):
        if e.y < self.chrome.bottom:
            self.chrome.focus = None
            self.chrome.click(e.x, e.y)
            self.raster_chrome()
        else:
            self.chrome.blur()
            tab_y = e.y - self.chrome.bottom + self.active_tab.scroll
            self.active_tab.click(e.x - HSTEP, tab_y)
            self.raster_tab()
        self.draw()

    def handle_key(self, char):
        if self.chrome.keypress(char):
            self.raster_chrome()
            self.draw()
        else:
            self.active_tab.keypress(char)
            self.raster_tab()
            self.draw()

    def handle_enter(self):
        self.chrome.enter()
        self.raster_tab()
        self.raster_chrome()
        self.draw()


def mainloop(browser):
    """SDLのイベントループ。Tkinterのmainloop()に相当する。
    各イベントを適切なハンドラに振り分ける。"""
    event = sdl2.SDL_Event()
    while True:
        while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl2.SDL_QUIT:
                browser.handle_quit()
                sdl2.SDL_Quit()
                sys.exit()
            elif event.type == sdl2.SDL_MOUSEBUTTONUP:
                browser.handle_click(event.button)
            elif event.type == sdl2.SDL_KEYDOWN:
                if event.key.keysym.sym == sdl2.SDLK_RETURN:
                    browser.handle_enter()
                elif event.key.keysym.sym == sdl2.SDLK_DOWN:
                    browser.handle_down()
            elif event.type == sdl2.SDL_TEXTINPUT:
                browser.handle_key(event.text.text.decode('utf8'))


if __name__ == "__main__":
    browser = Browser()
    browser.new_tab(URL(sys.argv[1]))
    mainloop(browser)
