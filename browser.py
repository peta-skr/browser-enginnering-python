import socket
import ssl
import tkinter
import tkinter.font
import urllib.parse
import dukpy


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

    def request(self, payload=None):
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

        # 本実装では非対応のエンコーディングが含まれていないことを確認
        assert "transfer-encoding" not in response_headers
        assert "content-encoding" not in response_headers

        # レスポンスボディを読み込んでソケットを閉じる
        content = response.read()
        s.close()

        return content

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
        
    def __str__(self):
        port_part = ":" + str(self.port)
        if self.scheme == "https" and self.port == 443:
            port_part = ""
        if self.scheme == "http" and self.port == 80:
            port_part = ""
        return self.scheme + "://" + self.host + port_part + self.path


# フォントオブジェクトのキャッシュ（サイズ・ウェイト・スタイルをキーとする）
FONTS = {}

def get_font(size, weight, style):
    """フォントオブジェクトをキャッシュから取得、なければ作成して登録する"""
    key = (size, weight, style)
    if key not in FONTS:
        font = tkinter.font.Font(size=size, weight=weight, slant=style)
        # Labelと紐付けることでmetricsのパフォーマンスが向上する（Tk推奨）
        label = tkinter.Label(font=font)
        FONTS[key] = (font, label)
    return FONTS[key][0]


WIDTH, HEIGHT = 800, 600    # ウィンドウの幅と高さ（ピクセル）
HSTEP, VSTEP = 13, 18       # 水平・垂直方向の初期オフセット
SCROLL_STEP = 100            # 1回のスクロール量（ピクセル）


class BlockLayout:
    """HTMLノード1つに対応するレイアウトオブジェクト。
    ブロックモード（子を縦に積む）とインラインモード（テキストを横に並べる）
    の2種類のレイアウトを担当する。"""

    # ブロック要素として扱うタグ名の一覧（これに該当する子を持つ場合はブロックモードになる）
    BLOCK_ELEMENTS = [
        "html", "body", "article", "section", "nav", "aside",
        "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
        "footer", "address", "p", "hr", "pre", "blockquote",
        "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
        "figcaption", "main", "div", "table", "form", "fieldset",
        "legend", "details", "summary"
    ]

    def __init__(self, node, parent, previous):
        self.node = node          # 対応するHTMLノード（ElementまたはText）
        self.parent = parent      # 親レイアウトオブジェクト
        self.previous = previous  # 直前の兄弟レイアウトオブジェクト（y座標計算に使用）
        self.children = []        # 子レイアウトオブジェクトのリスト
        self.x = None             # ページ上の絶対X座標（layout()で確定）
        self.y = None             # ページ上の絶対Y座標（layout()で確定）
        self.width = None         # 幅（親の幅を継承）
        self.height = None        # 高さ（layout()で確定）
        self.display_list = []    # インラインモード時の描画情報（x, y, word, font, color）のリスト

    def word(self, node, word):
        """1単語を行バッファに追加する。行幅を超える場合は新しい行へ折り返してから追加する"""
        weight = node.style["font-weight"]
        style = node.style["font-style"]
        if style == "normal": style = "roman"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)
        w = font.measure(word)

        if self.cursor_x + w > self.width:
            self.new_line()

        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        text = TextLayout(node, word, line, previous_word)
        line.children.append(text)
        self.cursor_x += w + font.measure(" ")

    def flush(self):
        """行バッファの内容をdisplay_listに書き出す。"""
        if not self.line: return

        metrics = [font.metrics() for x, word, font, color in self.line]

        max_ascent = max([metric["ascent"] for metric in metrics])
        baseline = self.cursor_y + 1.25 * max_ascent

        for rel_x, word, font, color in self.line:
            x = self.x + rel_x
            y = self.y + baseline - font.metrics("ascent")
            self.display_list.append((x, y, word, font, color))

        max_descent = max([metric["descent"] for metric in metrics])
        self.cursor_y = baseline + 1.25 * max_descent

        self.cursor_x = 0
        self.line = []

    def recurse(self, node):
        """HTMLツリーを深さ優先で再帰走査し、テキストを単語単位でレイアウトする（インラインモード専用）"""
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
        """ノードのレイアウトモードを返す。
        Textノードは常にinline。
        Elementノードがブロック要素の子を1つでも持つ場合はblock、
        それ以外の子がある場合はinline、子がなければblockとする。"""
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
        """座標・サイズを確定させる。
        blockモード: 子ノードをBlockLayoutとして生成し縦に積む。
        inlineモード: テキストを単語単位で配置し行バッファに蓄積する。
        最後に全子のlayout()を再帰呼び出しし、高さを集計する。"""
        # 親と同じx座標・幅を引き継ぐ
        self.x = self.parent.x
        self.width = self.parent.width

        # y座標は直前の兄弟の末尾、なければ親の先頭
        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        mode = self.layout_mode()

        if mode == "block":
            # ブロックモード: 子ノードごとにBlockLayoutを生成
            previous = None
            for child in self.node.children:
                next_child = BlockLayout(child, self, previous)
                self.children.append(next_child)
                previous = next_child
        else:
            # インラインモード: テキストを単語単位で行バッファに積む
            self.cursor_x = 0
            # self.cursor_y = 0
            # self.line = []
            self.new_line()
            self.recurse(self.node)
            # self.flush()

        # 子レイアウトを再帰的に確定
        for child in self.children:
            child.layout()

        # 高さの集計
        self.height = sum([child.height for child in self.children])

    def self_rect(self):
        return Rect(self.x, self.y,
                    self.x + self.width, self.y + self.height)

    def paint(self):
        """描画コマンドリストを返す。
        背景色（background-color）が設定されていればDrawRectを追加する。"""
        cmds = []

        if isinstance(self.node, Element):
            bgcolor = self.node.style.get("background-color", "transparent")
            if bgcolor != "transparent":
                rect = DrawRect(self.x, self.y, self.x + self.width, self.y + self.height, bgcolor)
                cmds.append(rect)

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
        self.cursor_x += w + font.measure(" ")

    def new_line(self):
        self.cursor_x = 0
        last_line = self.children[-1] if self.children else None
        new_line = LineLayout(self.node, self, last_line)
        self.children.append(new_line)

    def should_paint(self):
        return isinstance(self.node, Text) or \
        (self.node.tag != "input" and self.node.tag != "button")


class DocumentLayout:
    """ページ全体のルートレイアウト。ウィンドウ幅・余白を設定してBlockLayoutのルートを生成する。"""

    def __init__(self, node):
        self.node = node    # HTMLツリーのルートノード
        self.parent = None  # ルートなので親はなし
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = None

    def layout(self):
        """ウィンドウ幅から余白を引いた領域にルートBlockLayoutを配置する"""
        self.width = WIDTH - 2*HSTEP
        self.x = HSTEP
        self.y = VSTEP
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout()
        self.height = child.height

    def paint(self):
        # ドキュメントルート自体は描画コマンドを持たない
        return []

    def should_paint(self):
        return True


class Text:
    """HTMLテキストノード。タグ間のテキスト内容を保持する。"""

    def __init__(self, text, parent):
        self.text = text        # テキスト内容
        self.children = []      # テキストノードは子を持たない（常に空リスト）
        self.parent = parent    # 親Elementノード
        self.is_focused = False

    def __repr__(self):
        return repr(self.text)


class Element:
    """HTMLタグノード。タグ名・属性・子ノードを保持する。"""

    def __init__(self, tag, attributes, parent):
        self.tag = tag              # タグ名（例: "div", "p"）
        self.attributes = attributes  # 属性辞書（例: {"class": "foo"}）
        self.children = []          # 子ノードのリスト
        self.parent = parent        # 親ノード
        self.is_focused = False

    def __repr__(self):
        return "<" + self.tag + ">"


class HTMLParser:
    """HTMLソーステキストをパースしてDOMツリー（TextとElementの木構造）を構築する。"""

    SELF_CLOSING_TAGS = [
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ]
    HEAD_TAGS = [
        "base", "basefont", "bgsound", "noscript",
        "link", "meta", "title", "style", "script",
    ]

    def __init__(self, body):
        self.body = body        # パース対象のHTMLソース文字列
        self.unfinished = []    # 開いているが閉じていないElementノードのスタック

    def parse(self):
        """HTMLソースを1文字ずつ走査し、テキストとタグに分けて処理する。
        '<' でタグ開始、'>' でタグ終了と判断し、最後にfinish()でツリーを確定する。"""
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
        """テキストノードを現在の親要素に追加する。空白のみのテキストは無視する。"""
        if text.isspace(): return
        self.implicit_tags(None)
        parent = self.unfinished[-1]
        node = Text(text, parent)
        parent.children.append(node)

    def add_tag(self, tag):
        """タグ文字列を解析してDOMノードをスタックに積む（または閉じる）。
        '!' 始まりのコメント・DOCTYPE宣言は無視する。
        '/' 始まりは閉じタグ、SELF_CLOSING_TAGSは即座に親に追加、
        それ以外は開きタグとしてunfinishedスタックに積む。"""
        tag, attributes = self.get_attributes(tag)
        if tag.startswith("!"): return  # コメント・DOCTYPEを無視
        self.implicit_tags(tag)

        if tag.startswith("/"):
            # 閉じタグ: スタックからポップして親の子に追加
            if len(self.unfinished) == 1: return  # ルート要素は閉じない
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        elif tag in self.SELF_CLOSING_TAGS:
            # 自己終了タグ: 即座に親の子として追加
            parent = self.unfinished[-1]
            node = Element(tag, attributes, parent)
            parent.children.append(node)
        else:
            # 開きタグ: スタックに積んで閉じタグを待つ
            parent = self.unfinished[-1] if self.unfinished else None
            node = Element(tag, attributes, parent)
            self.unfinished.append(node)

    def finish(self):
        """パース終了処理。未閉じのノードをすべて閉じてルートノードを返す。"""
        if not self.unfinished:
            self.implicit_tags(None)
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        return self.unfinished.pop()

    def get_attributes(self, text):
        """タグ文字列をタグ名と属性辞書に分解して返す。
        属性値はクォートで囲まれている場合は除去する。
        値のない属性は空文字列を値として登録する。"""
        parts = text.split()
        tag = parts[0].casefold()
        attributes = {}
        for attrpair in parts[1:]:
            if "=" in attrpair:
                key, value = attrpair.split("=", 1)
                if len(value) > 2 and value[0] in ["'", "\""]:
                    value = value[1:-1]  # クォートを除去
                attributes[key.casefold()] = value
            else:
                attributes[attrpair.casefold()] = ""  # 値なし属性
        return tag, attributes

    def implicit_tags(self, tag):
        """暗黙的なタグを補完する。
        HTMLでは<html>/<head>/<body>が省略可能なため、
        現在のスタック状態と次のタグに応じて必要なタグを自動挿入する。"""
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if open_tags == [] and tag != "html":
                # ルートに<html>がなければ補完
                self.add_tag("html")
            elif open_tags == ["html"] \
                    and tag not in ["head", "body", "/html"]:
                # <html>直下でhead/bodyタグでない場合、適切な方を補完
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")
            elif open_tags == ["html", "head"] and \
                    tag not in ["/head"] + self.HEAD_TAGS:
                # <head>内でない要素が来たら</head>を補完して閉じる
                self.add_tag("/head")
            else:
                break


class DrawText:
    """テキスト描画コマンド。Canvas.create_text()のパラメータを保持する。"""

    def __init__(self, x1, y1, text, font, color):
        self.top = y1
        self.left = x1
        self.text = text
        self.font = font
        self.bottom = y1 + font.metrics("linespace")  # 描画範囲の下端（スクロール判定に使用）
        self.color = color

    def execute(self, scroll, canvas):
        """スクロール量を考慮してキャンバスにテキストを描画する"""
        canvas.create_text(
            self.left, self.top - scroll,
            text=self.text,
            font=self.font,
            anchor='nw',
            fill=self.color
        )


class DrawRect:
    """矩形描画コマンド。背景色ブロックの描画に使用する。"""

    def __init__(self, x1, y1, x2, y2, color):
        self.top = y1
        self.left = x1
        self.bottom = y2
        self.right = x2
        self.color = color

    def execute(self, scroll, canvas):
        """スクロール量を考慮してキャンバスに塗りつぶし矩形を描画する（枠線なし）"""
        canvas.create_rectangle(
            self.left, self.top - scroll,
            self.right, self.bottom - scroll,
            width=0,    # 枠線の幅を0にして塗りつぶしのみにする
            fill=self.color
        )


# 親ノードから子ノードへ継承されるCSSプロパティとそのデフォルト値
INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "color": "black",
}


class CSSParser:
    """CSSソーステキストをパースして（セレクタ, スタイル辞書）のリストを生成する。
    self.i がカーソル位置を示し、各メソッドが少しずつ消費していく再帰下降パーサ。"""

    def __init__(self, s):
        self.s = s  # パース対象のCSS文字列
        self.i = 0  # 現在のパース位置

    def whitespace(self):
        """空白文字をスキップしてカーソルを進める"""
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def word(self):
        """英数字・#・-・.・% から構成されるトークンを読み取って返す"""
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
        """期待する1文字を消費する。一致しない場合は例外を送出する"""
        if not (self.i < len(self.s) and self.s[self.i] == literal):
            raise Exception("Parsing error")
        self.i += 1

    def pair(self):
        """'property: value' の形式を読み取り、(プロパティ名, 値) のタプルを返す"""
        prop = self.word()
        self.whitespace()
        self.literal(":")
        self.whitespace()
        val = self.word()
        return prop.casefold(), val

    def body(self):
        """CSSブロック内（{} の中身）をパースしてプロパティ辞書を返す。
        パースエラーが発生したプロパティは ';' または '}' まで読み飛ばして継続する。"""
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
        """指定文字のいずれかが現れるまでカーソルを進め、その文字を返す。末尾に達した場合はNoneを返す。"""
        while self.i < len(self.s):
            if self.s[self.i] in chars:
                return self.s[self.i]
            else:
                self.i += 1
        return None

    def selector(self):
        """セレクタを解析して TagSelector または DescendantSelector を返す。
        スペース区切りのタグ名は子孫セレクタとしてネストする。"""
        out = TagSelector(self.word().casefold())
        self.whitespace()
        while self.i < len(self.s) and self.s[self.i] != "{":
            tag = self.word()
            descendant = TagSelector(tag.casefold())
            out = DescendantSelector(out, descendant)
            self.whitespace()
        return out

    def parse(self):
        """CSSソース全体をパースし、(セレクタ, スタイル辞書) のリストを返す。
        パースエラーが発生したルールは '}'まで読み飛ばして継続する。"""
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
    """タグ名によるCSSセレクタ（例: `p`, `div`）。優先度は1。"""

    def __init__(self, tag):
        self.tag = tag
        self.priority = 1  # タグセレクタの詳細度は1

    def matches(self, node):
        """ノードがElementかつタグ名が一致する場合にTrueを返す"""
        return isinstance(node, Element) and self.tag == node.tag


class DescendantSelector:
    """子孫セレクタ（例: `div p`）。ancestor の子孫に descendant が存在するか判定する。
    優先度は構成要素の優先度の合計。"""

    def __init__(self, ancestor, descendant):
        self.ancestor = ancestor
        self.descendant = descendant
        self.priority = ancestor.priority + descendant.priority  # 詳細度を加算

    def matches(self, node):
        """descendantセレクタにマッチし、かつ祖先にancestorセレクタにマッチするノードがある場合Trueを返す"""
        if not self.descendant.matches(node): return False
        while node.parent:
            if self.ancestor.matches(node.parent): return True
            node = node.parent
        return False

class LineLayout:
    """インラインモードの1行分を表すレイアウトオブジェクト。
    子要素としてTextLayoutを持ち、ベースライン揃えで単語を配置する。"""

    def __init__(self, node, parent, previous):
        self.node = node          # 対応するHTMLノード
        self.parent = parent      # 親BlockLayout
        self.previous = previous  # 直前の行（y座標計算に使用）
        self.children = []        # この行に含まれるTextLayoutのリスト

    def layout(self):
        """行の座標・高さを確定する。各単語のy座標をベースライン基準で揃える。"""
        self.width = self.parent.width
        self.x = self.parent.x

        # y座標: 前の行の直後、または親の先頭
        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        # 各単語（TextLayout）のレイアウトを確定
        for word in self.children:
            word.layout()

        # 空行の場合は高さ0で終了
        if not self.children:
            self.height = 0
            return

        # ベースライン揃え: 全単語のascentの最大値を基準にy座標を調整
        max_ascent = max([word.font.metrics("ascent") for word in self.children])
        baseline = self.y + 1.25 * max_ascent
        for word in self.children:
            word.y = baseline - word.font.metrics("ascent")
        max_descent = max([word.font.metrics("descent") for word in self.children])
        self.height = 1.25 * (max_ascent + max_descent)

    def paint(self):
        # 行自体は描画コマンドを持たない（子のTextLayoutが描画を担当）
        return []

    def should_paint(self):
        return True


class TextLayout:
    """1単語分のレイアウトオブジェクト。フォント・座標・サイズを計算し、
    DrawTextコマンドを生成する最小描画単位。"""

    def __init__(self, node, word, parent, previous):
        self.node = node          # 対応するHTMLテキストノード
        self.word = word          # この単語の文字列
        self.children = []        # 葉ノードのため常に空
        self.parent = parent      # 親LineLayout
        self.previous = previous  # 同じ行内の直前の単語（x座標計算に使用）

    def layout(self):
        """フォントを決定し、x座標・幅・高さを計算する。
        y座標はLineLayout.layout()でベースライン揃え後に設定される。"""
        weight = self.node.style["font-weight"]
        style = self.node.style["font-style"]
        if style == "normal": style = "roman"  # tkinterでは"roman"が通常体
        size = int(float(self.node.style["font-size"][:-2]) * .75)  # px→pt変換（概算）
        self.font = get_font(size, weight, style)

        self.width = self.font.measure(self.word)

        # x座標: 前の単語の右端＋スペース幅、なければ行の先頭
        if self.previous:
            space = self.previous.font.measure(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = self.font.metrics("linespace")

    def paint(self):
        """この単語のDrawTextコマンドを返す"""
        color = self.node.style["color"]
        return [DrawText(self.x, self.y, self.word, self.font, color)]

    def should_paint(self):
        return True


INPUT_WIDTH_PX = 200


class InputLayout:
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
            space = self.previous.font.measure(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = self.font.metrics("linespace")

    def should_paint(self):
        return True

    def self_rect(self):
        return Rect(self.x, self.y,
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
            cx = self.x + self.font.measure(text)
            cmds.append(DrawLine(cx, self.y, cx, self.y + self.height, "black", 1))

        return cmds

def style(node, rules):
    """CSSルールと継承プロパティをノードに適用し、子ノードへ再帰する。
    適用優先順: 継承値 → CSSルール → style属性（インラインスタイル）"""
    node.style = {}

    # 継承: 親のスタイルを引き継ぐ（なければデフォルト値を使う）
    for property, default_value in INHERITED_PROPERTIES.items():
        if node.parent:
            node.style[property] = node.parent.style[property]
        else:
            node.style[property] = default_value

    # CSSルールを適用（Elementノードのみ。Textノードはセレクタにマッチしない）
    if isinstance(node, Element):
        for selector, body in rules:
            if not selector.matches(node): continue
            for property, value in body.items():
                node.style[property] = value

        # style属性を適用（CSSルールより優先）
        if "style" in node.attributes:
            pairs = CSSParser(node.attributes["style"]).body()
            for property, value in pairs.items():
                node.style[property] = value

    # font-size のパーセンテージを絶対ピクセル値に解決する
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
    """HTMLツリーをインデント付きで標準出力に表示する（デバッグ用）"""
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


def paint_tree(layout_object, display_list):
    """レイアウトツリーを深さ優先で走査し、全描画コマンドをdisplay_listに収集する"""
    if layout_object.should_paint():
        display_list.extend(layout_object.paint())
    for child in layout_object.children:
        paint_tree(child, display_list)


def tree_to_list(tree, result):
    """ツリーを深さ優先でフラットなリストに変換して返す（CSSリンク収集などに使用）"""
    result.append(tree)
    for child in tree.children:
        tree_to_list(child, result)
    return result


def cascade_priority(rule):
    """CSSルールのカスケード優先度（セレクタの詳細度）を返す。sorted()のkeyに使用する。"""
    selector, body = rule
    return selector.priority


# ブラウザのデフォルトスタイルシート（browser.cssから読み込み、起動時に1回だけパース）
DEFAULT_STYLE_SHEET = CSSParser(open("browser.css").read()).parse()

RUNTIME_JS = open("runtime.js").read()

# JSからPython側でイベントを発火する際に evaljs で実行するコード断片。
# dukpy.handle / dukpy.type は evaljs のキーワード引数で注入される。
EVENT_DISPATCH_JS = \
    "new Node(dukpy.handle).dispatchEvent(new Event(dukpy.type))"

class JSContext:
    """JavaScriptの実行環境を管理するクラス。
    DukPy の JSInterpreter をラップし、Python関数のエクスポートや
    DOM操作APIの橋渡しを担う。"""

    def __init__(self, tab):
        self.tab = tab
        self.interp = dukpy.JSInterpreter()

        # Python関数を JS の call_python() から呼び出せるようにエクスポート。
        # evaljs(RUNTIME_JS) より先に登録しておく必要がある。
        self.interp.export_function("log", print)
        self.interp.export_function("querySelectorAll", self.querySelectorAll)
        self.interp.export_function("getAttribute", self.getAttribute)
        self.interp.export_function("innerHTML_set", self.innerHTML_set)

        # ユーザースクリプトより先にランタイムを読み込む。
        # console, document, Node, Event などのグローバルオブジェクトを定義する。
        self.interp.evaljs(RUNTIME_JS)

        # PythonのElementオブジェクトをJSに直接渡せないため、
        # 整数のハンドル（ファイルディスクリプタと同じ発想）で間接参照する。
        self.node_to_handle = {}  # Element -> int
        self.handle_to_node = {}  # int -> Element

    def run(self, script, code):
        """JavaScriptコードを実行する。クラッシュしてもブラウザを落とさない。"""
        try:
            return self.interp.evaljs(code)
        except dukpy.JSRuntimeError as e:
            print("Script", script, "crashed", e)

    def querySelectorAll(self, selector_text):
        """CSSセレクタにマッチする全ノードのハンドルリストを返す。
        JSには Element オブジェクトを直接渡せないためハンドルに変換する。"""
        selector = CSSParser(selector_text).selector()
        nodes = [node for node in tree_to_list(self.tab.nodes, [])
                 if selector.matches(node)]
        return [self.get_handle(node) for node in nodes]

    def get_handle(self, elt):
        """ElementオブジェクトのハンドルIDを返す。未登録なら新規発行する。"""
        if elt not in self.node_to_handle:
            handle = len(self.node_to_handle)
            self.node_to_handle[elt] = handle
            self.handle_to_node[handle] = elt
        else:
            handle = self.node_to_handle[elt]
        return handle

    def getAttribute(self, handle, attr):
        """ハンドルで指定した要素の属性値を返す。属性がなければ空文字を返す。"""
        elt = self.handle_to_node[handle]
        attr = elt.attributes.get(attr, None)
        return attr if attr else ""

    def dispatch_event(self, type, elt):
        """指定要素にイベントを発火し、preventDefault が呼ばれたかを返す。
        まだハンドルがない要素（リスナーゼロ）には -1 を渡す。
        戻り値が True の場合、呼び出し元はデフォルト動作をスキップする。"""
        handle = self.node_to_handle.get(elt, -1)
        do_default = self.interp.evaljs(
            EVENT_DISPATCH_JS, type=type, handle=handle
        )
        return not do_default

    def innerHTML_set(self, handle, s):
        """ハンドルで指定した要素の子ノードをHTML文字列で置き換える。
        HTMLパーサーはドキュメント全体用のため <html><body> でラップしてパースする。
        DOM変更後は render() を呼んでレイアウト・描画を更新する。"""
        doc = HTMLParser("<html><body>" + s + "</body></html>").parse()
        new_nodes = doc.children[0].children
        elt = self.handle_to_node[handle]
        elt.children = new_nodes
        for child in elt.children:
            child.parent = elt
        self.tab.render()

class Tab:
    """ブラウザのメインクラス。ウィンドウ・キャンバス・スクロール状態を管理し、
    URLの読み込みからレンダリングまでのパイプラインを統括する。"""

    def __init__(self, tab_height):
        self.url = None
        self.scroll = 0
        self.tab_height = tab_height
        self.history = []
        self.nodes = []
        self.rules = []
        self.focus = None

    def load(self, url, payload=None):
        """URLからHTMLを取得し、パース・スタイル適用・レイアウト・描画を行う"""
        self.history.append(url)
        self.url = url
        body = url.request(payload)
        self.nodes = HTMLParser(body).parse()

        scripts = [node.attributes["src"] for node in tree_to_list(self.nodes, [])
                   if isinstance(node, Element)
                   and node.tag == "script"
                   and "src" in node.attributes]
        
        self.js = JSContext(self)
        for script in scripts:
            script_url = url.resolve(script)
            try:
                body = script_url.request()
            except:
                continue
            self.js.run(script, body)

        # ① デフォルトスタイルシートを起点にルールを集める
        self.rules = DEFAULT_STYLE_SHEET.copy()

        # ② <link rel=stylesheet> のCSSファイルをすべて収集して追加
        links = [node.attributes["href"]
                 for node in tree_to_list(self.nodes, [])
                 if isinstance(node, Element)
                 and node.tag == "link"
                 and node.attributes.get("rel") == "stylesheet"
                 and "href" in node.attributes]
        for link in links:
            style_url = url.resolve(link)
            try:
                css_body = style_url.request()
            except Exception:
                continue
            self.rules.extend(CSSParser(css_body).parse())

        self.render()

    
    def render(self):
        # ③ ルールが全部揃ってから1回だけ style() を適用
        style(self.nodes, sorted(self.rules, key=cascade_priority))

        # ④ style() 完了後にレイアウトを計算（word() 内で node.style を参照するため）
        self.document = DocumentLayout(self.nodes)
        self.document.layout()

        self.display_list = []
        paint_tree(self.document, self.display_list)


    def draw(self, canvas, offset):
        """display_listの描画コマンドをキャンバスに描画する。
        現在のビューポート（scroll〜scroll+HEIGHT）外のコマンドはスキップして高速化する。"""
        for cmd in self.display_list:
            if cmd.top > self.scroll + self.tab_height: continue  # 画面下より下は描画しない
            if cmd.bottom < self.scroll: continue        # 画面上より上は描画しない
            cmd.execute(self.scroll - offset, canvas)

    def scrolldown(self):
        """スクロール位置を1ステップ下へ更新する。ページ末尾を超えないようにクランプする。"""
        max_y = max(
            self.document.height + 2*VSTEP - self.tab_height, 0)
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)

    def click(self, x, y):
        """クリック座標にあるレイアウトオブジェクトを特定し、
        リンク（<a>タグ）であればそのhrefを読み込む。"""
        self.focus = None
        y += self.scroll  # スクロールを考慮した絶対座標に変換

        # クリック座標に重なるレイアウトオブジェクトを収集（最前面=末尾）
        objs = [obj for obj in tree_to_list(self.document, [])
                if obj.x <= x < obj.x + obj.width
                and obj.y <= y < obj.y + obj.height]

        if not objs: return
        elt = objs[-1].node  # 最も深い（最前面の）要素のDOMノード

        if self.focus:
            self.focus.is_focused = False

        # DOMツリーを親方向にたどり、対応する要素を探す
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
        """履歴を1つ戻る。現在のURLをpopし、その前のURLを再読み込みする。"""
        if len(self.history) > 1:
            self.history.pop()       # 現在のURLを破棄
            back = self.history.pop() # 戻り先URL（load()で再追加される）
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

class Rect:
    """矩形領域を表すユーティリティクラス。UI要素のヒットテストなどに使用する。"""

    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def contains_point(self, x, y):
        """座標(x, y)がこの矩形内に含まれるかを判定する"""
        return x >= self.left and x < self.right \
            and y >= self.top and y < self.bottom


class DrawOutline:
    """矩形の枠線描画コマンド。ボタンやアドレスバーの枠に使用する。"""

    def __init__(self, rect, color, thickness):
        self.rect = rect
        self.color = color
        self.thickness = thickness

    def execute(self, scroll, canvas):
        """スクロール量を考慮して枠線を描画する"""
        canvas.create_rectangle(
            self.rect.left, self.rect.top - scroll,
            self.rect.right, self.rect.bottom - scroll,
            width=self.thickness,
            outline=self.color
        )


class DrawLine:
    """直線描画コマンド。タブ区切り線やアドレスバーのカーソルなどに使用する。"""

    def __init__(self, x1, y1, x2, y2, color, thickness):
        self.rect = Rect(x1, y1, x2, y2)  # 始点(left,top)〜終点(right,bottom)
        self.color = color
        self.thickness = thickness
        self.top = y1
        self.bottom = y2

    def execute(self, scroll, canvas):
        """スクロール量を考慮して直線を描画する"""
        canvas.create_line(
            self.rect.left, self.rect.top - scroll,
            self.rect.right, self.rect.bottom - scroll,
            fill=self.color, width=self.thickness)

class Chrome:
    """ブラウザのUI部分（タブバー・アドレスバー・戻るボタン）を管理する。
    描画コマンドの生成とクリック・キー入力のハンドリングを担当する。"""

    def __init__(self, browser):
        self.browser = browser
        self.font = get_font(20, "normal", "roman")
        self.font_height = self.font.metrics("linespace")
        self.padding = 5

        # --- タブバー領域の計算 ---
        self.tabbar_top = 0
        self.tabbar_bottom = self.font_height + 2*self.padding

        # 新規タブボタン「+」の領域
        plus_width = self.font.measure("+") + 2*self.padding
        self.newtab_rect = Rect(
            self.padding, self.padding,
            self.padding + plus_width,
            self.padding + self.font_height
        )

        # --- URLバー領域の計算 ---
        self.bottom = self.tabbar_bottom
        self.urlbar_top = self.tabbar_bottom
        self.urlbar_bottom = self.urlbar_top + \
            self.font_height + 2*self.padding
        self.bottom = self.urlbar_bottom  # Chrome全体の下端

        # 戻るボタン「<」の領域
        back_width = self.font.measure("<") + 2*self.padding
        self.back_rect = Rect(
            self.padding,
            self.urlbar_top + self.padding,
            self.padding + back_width,
            self.urlbar_bottom - self.padding)

        # アドレスバーの領域（戻るボタンの右端〜ウィンドウ右端）
        self.address_rect = Rect(
            self.back_rect.right + self.padding,
            self.urlbar_top + self.padding,
            WIDTH - self.padding,
            self.urlbar_bottom - self.padding)

        self.focus = None        # 現在フォーカスがある要素（"address bar" or None）
        self.address_bar = ""    # アドレスバーに入力中のテキスト

    def tab_rect(self, i):
        """i番目のタブの矩形領域を計算して返す"""
        tabs_start = self.newtab_rect.right + self.padding
        tab_width = self.font.measure("Tab X") + 2*self.padding
        return Rect(
            tabs_start + tab_width * i, self.tabbar_top,
            tabs_start + tab_width * (i + 1), self.tabbar_bottom
        )

    def paint(self):
        """Chrome部分の描画コマンドリストを生成する"""
        cmds = []

        # Chrome背景（白で塗りつぶしてページコンテンツを隠す）
        cmds.append(DrawRect(0, 0, WIDTH, self.bottom, "white"))

        # 新規タブボタン「+」
        cmds.append(DrawOutline(self.newtab_rect, "black", 1))
        cmds.append(DrawText(
            self.newtab_rect.left + self.padding,
            self.newtab_rect.top,
            "+", self.font, "black"
        ))

        # 各タブの描画（左右の縦線・ラベル・アクティブタブの下線）
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

            # アクティブタブはタブ以外の領域に下線を引く（タブが前面に出ている表現）
            if tab == self.browser.active_tab:
                cmds.append(DrawLine(
                    0, bounds.bottom, bounds.left, bounds.bottom,
                    "black", 1))
                cmds.append(DrawLine(
                    bounds.right, bounds.bottom, WIDTH, bounds.bottom,
                    "black", 1))

        # タブバーとURLバーの境界線
        cmds.append(DrawLine(
            0, self.bottom, WIDTH,
            self.bottom, "black", 1))

        # 戻るボタン「<」
        cmds.append(DrawOutline(self.back_rect, "black", 1))
        cmds.append(DrawText(
            self.back_rect.left + self.padding,
            self.back_rect.top,
            "<", self.font, "black"))

        # アドレスバー
        cmds.append(DrawOutline(self.address_rect, "black", 1))

        if self.focus == "address bar":
            # 入力中: 入力テキストとカーソル（赤い縦線）を表示
            cmds.append(DrawText(self.address_rect.left + self.padding,
                                 self.address_rect.top,
                                 self.address_bar, self.font, "black"))
            w = self.font.measure(self.address_bar)
            cmds.append(DrawLine(
                self.address_rect.left + self.padding + w,
                self.address_rect.top,
                self.address_rect.left + self.padding + w,
                self.address_rect.bottom,
                "red", 1))
        else:
            # 非フォーカス時: 現在のURLを表示
            url = str(self.browser.active_tab.url)
            cmds.append(DrawText(
                self.address_rect.left + self.padding,
                self.address_rect.top,
                url, self.font, "black"))
        return cmds

    def click(self, x, y):
        """Chrome領域のクリックイベントを処理する。
        クリック位置に応じて新規タブ・戻る・アドレスバーフォーカス・タブ切替を行う。"""
        self.focus = None
        if self.newtab_rect.contains_point(x, y):
            self.browser.new_tab(URL("https://browser.engineering/"))
        elif self.back_rect.contains_point(x, y):
            self.browser.active_tab.go_back()
        elif self.address_rect.contains_point(x, y):
            self.focus = "address bar"
            self.address_bar = ""
        else:
            # タブバーのクリック: クリックされたタブをアクティブにする
            for i, tab in enumerate(self.browser.tabs):
                if self.tab_rect(i).contains_point(x, y):
                    self.browser.active_tab = tab
                    break

    def blur(self):
        """Chromeのフォーカスを解除する"""
        self.focus = None

    def keypress(self, char):
        """アドレスバーにフォーカスがある場合、入力文字を追加する"""
        if self.focus == "address bar":
            self.address_bar += char
            return True
        return False

    def enter(self):
        """アドレスバーにフォーカスがある場合、入力URLを読み込む"""
        if self.focus == "address bar":
            self.browser.active_tab.load(URL(self.address_bar))
            self.focus = None

class Browser:
    """ブラウザのトップレベルクラス。tkinterウィンドウの管理、タブの管理、
    イベントハンドリング（キー入力・マウスクリック）を統括する。"""

    def __init__(self):
        self.tabs = []          # 開いているタブのリスト
        self.active_tab = None  # 現在表示中のタブ

        # tkinterウィンドウとキャンバスを初期化
        self.window = tkinter.Tk()
        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT,
            bg="white"
        )
        self.canvas.pack()
        self.scroll = 0  # 現在のスクロール位置（ピクセル）

        # イベントバインド
        self.window.bind("<Down>", self.handle_down)      # ↓キー: スクロール
        self.window.bind("<Button-1>", self.handle_click)  # 左クリック
        self.chrome = Chrome(self)
        self.window.bind("<Key>", self.handle_key)         # キー入力: アドレスバーへ
        self.window.bind("<Return>", self.handle_enter)    # Enterキー: URL確定

    def handle_down(self, e):
        """↓キー押下時: アクティブタブをスクロールして再描画"""
        self.active_tab.scrolldown()
        self.draw()

    def handle_click(self, e):
        """クリック時: Chrome領域ならChromeに、それ以外はタブのコンテンツに委譲"""
        if e.y < self.chrome.bottom:
            self.focus = None
            self.chrome.click(e.x, e.y)
        else:
            self.focus = "content"
            self.chrome.blur()
            tab_y = e.y - self.chrome.bottom  # Chrome分のオフセットを引く
            self.active_tab.click(e.x, tab_y)
        self.draw()

    def draw(self):
        """キャンバスを全消去し、タブのコンテンツとChromeを重ねて描画する"""
        self.canvas.delete("all")
        self.active_tab.draw(self.canvas, self.chrome.bottom)

        # Chromeはスクロールなし（scroll=0）で最前面に描画
        for cmd in self.chrome.paint():
            cmd.execute(0, self.canvas)

    def new_tab(self, url):
        """新しいタブを作成してURLを読み込み、アクティブにする"""
        new_tab = Tab(HEIGHT - self.chrome.bottom)
        new_tab.load(url)
        self.active_tab = new_tab
        self.tabs.append(new_tab)
        self.draw()

    def handle_key(self, e):
        """印字可能文字の入力をChromeに転送する（アドレスバー入力用）"""
        if len(e.char) == 0: return
        if not (0x20 <= ord(e.char) < 0x7f): return  # 印字可能ASCII文字のみ
        if self.chrome.keypress(e.char):
            self.draw()
        elif self.focus == "content":
            self.active_tab.keypress(e.char)
            self.draw()

    def handle_enter(self, e):
        """Enterキー押下時: アドレスバーのURL確定をChromeに委譲"""
        self.chrome.enter()
        self.draw()
    

if __name__ == "__main__":
    import sys
    # コマンドライン引数のURLで新規タブを開き、tkinterイベントループを開始
    Browser().new_tab(URL(sys.argv[1]))
    tkinter.mainloop()