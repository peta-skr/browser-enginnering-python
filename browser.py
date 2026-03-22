import socket
import ssl
import tkinter
import tkinter.font


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

    def request(self):
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
        request = "GET {} HTTP/1.0\r\n".format(self.path)
        request += "Host: {}\r\n".format(self.host)
        request += "\r\n"
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
        """1単語を行バッファに追加する。行幅を超える場合はフラッシュして次の行へ折り返す"""
        weight = node.style["font-weight"]
        style = node.style["font-style"]
        if style == "normal": style = "roman"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)
        w = font.measure(word)
        color = node.style["color"]

        if self.cursor_x + w > self.width:
            self.flush()

        self.line.append((self.cursor_x, word, font, color))
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
                self.flush()
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
        elif self.node.children:
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
            self.cursor_y = 0
            self.line = []
            self.recurse(self.node)
            self.flush()

        # 子レイアウトを再帰的に確定
        for child in self.children:
            child.layout()

        # 高さの集計
        if mode == "block":
            self.height = sum([child.height for child in self.children])
        else:
            self.height = self.cursor_y

    def paint(self):
        """描画コマンドリストを返す。
        背景色（background-color）が設定されていればDrawRectを追加し、
        インラインモードの場合はdisplay_listの各単語をDrawTextとして追加する。"""
        cmds = []

        if isinstance(self.node, Element):
            bgcolor = self.node.style.get("background-color", "transparent")
            if bgcolor != "transparent":
                x2, y2 = self.x + self.width, self.y + self.height
                rect = DrawRect(self.x, self.y, x2, y2, bgcolor)
                cmds.append(rect)

        if self.layout_mode() == "inline":
            for x, y, word, font, color in self.display_list:
                cmds.append(DrawText(x, y, word, font, color))
        return cmds


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


class Text:
    """HTMLテキストノード。タグ間のテキスト内容を保持する。"""

    def __init__(self, text, parent):
        self.text = text        # テキスト内容
        self.children = []      # テキストノードは子を持たない（常に空リスト）
        self.parent = parent    # 親Elementノード

    def __repr__(self):
        return repr(self.text)


class Element:
    """HTMLタグノード。タグ名・属性・子ノードを保持する。"""

    def __init__(self, tag, attributes, parent):
        self.tag = tag              # タグ名（例: "div", "p"）
        self.attributes = attributes  # 属性辞書（例: {"class": "foo"}）
        self.children = []          # 子ノードのリスト
        self.parent = parent        # 親ノード

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


DEFAULT_STYLE_SHEET = CSSParser(open("browser.css").read()).parse()


class Browser:
    """ブラウザのメインクラス。ウィンドウ・キャンバス・スクロール状態を管理し、
    URLの読み込みからレンダリングまでのパイプラインを統括する。"""

    def __init__(self):
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
        self.window.bind("<Down>", self.scrolldown)  # 下矢印キーにスクロールを割り当て

    def load(self, url):
        """URLからHTMLを取得し、パース・スタイル適用・レイアウト・描画を行う"""
        body = url.request()
        self.nodes = HTMLParser(body).parse()

        # ① デフォルトスタイルシートを起点にルールを集める
        rules = DEFAULT_STYLE_SHEET.copy()

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
            rules.extend(CSSParser(css_body).parse())

        # ③ ルールが全部揃ってから1回だけ style() を適用
        style(self.nodes, sorted(rules, key=cascade_priority))

        # ④ style() 完了後にレイアウトを計算（word() 内で node.style を参照するため）
        self.document = DocumentLayout(self.nodes)
        self.document.layout()

        self.display_list = []
        paint_tree(self.document, self.display_list)
        self.draw()

    def draw(self):
        """display_listの描画コマンドをキャンバスに描画する。
        現在のビューポート（scroll〜scroll+HEIGHT）外のコマンドはスキップして高速化する。"""
        self.canvas.delete("all")
        for cmd in self.display_list:
            if cmd.top > self.scroll + HEIGHT: continue  # 画面下より下は描画しない
            if cmd.bottom < self.scroll: continue        # 画面上より上は描画しない
            cmd.execute(self.scroll, self.canvas)

    def scrolldown(self, e):
        """下矢印キー押下時にスクロール位置を更新して再描画する。
        ページ末尾を超えないようにmax_yでクランプする。"""
        max_y = max(self.document.height + 2*VSTEP - HEIGHT, 0)
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)
        self.draw()


if __name__ == "__main__":
    import sys
    Browser().load(URL(sys.argv[1]))
    tkinter.mainloop()