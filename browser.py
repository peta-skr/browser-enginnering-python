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

    def __init__(self, node, parent, previous):
        self.node = node          # 対応するHTMLノード（ElementまたはText）
        self.parent = parent      # 親レイアウトオブジェクト
        self.previous = previous  # 直前の兄弟レイアウトオブジェクト（y座標計算に使用）
        self.children = []        # 子レイアウトオブジェクトのリスト
        self.x = None             # ページ上の絶対X座標（layout()で確定）
        self.y = None             # ページ上の絶対Y座標（layout()で確定）
        self.width = None         # 幅（親の幅を継承）
        self.height = None        # 高さ（layout()で確定）
        self.display_list = []    # インラインモード時の描画情報（x, y, word, font）のリスト
        # ブロック要素として扱うタグ名の一覧（これに該当する子を持つ場合はブロックモードになる）
        self.BLOCK_ELEMENTS = [
            "html", "body", "article", "section", "nav", "aside",
            "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
            "footer", "address", "p", "hr", "pre", "blockquote",
            "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
            "figcaption", "main", "div", "table", "form", "fieldset",
            "legend", "details", "summary"
        ]

    def open_tag(self, tag):
        """開きタグに応じてフォントスタイル・サイズを変更する（インラインモード専用）"""
        if tag == "i":
            self.style = "italic"
        elif tag == "b":
            self.weight = "bold"
        elif tag == "small":
            self.size -= 2
        elif tag == "big":
            self.size += 4
        elif tag == "br":
            # <br> は自己終了タグだが、改行処理はopen_tag側で行う
            self.flush()

    def close_tag(self, tag):
        """閉じタグに応じてフォントスタイル・サイズを元に戻す（インラインモード専用）"""
        if tag == "i":
            self.style = "roman"
        elif tag == "b":
            self.weight = "normal"
        elif tag == "small":
            self.size += 2
        elif tag == "big":
            self.size -= 4
        elif tag == "p":
            # </p> で現在行をフラッシュし、段落間の余白を追加
            self.flush()
            self.cursor_y += VSTEP

    def word(self, word):
        """1単語を行バッファに追加する。行幅を超える場合はフラッシュして次の行へ折り返す"""
        font = get_font(self.size, self.weight, self.style)
        w = font.measure(word)

        # 単語を追加すると自身の幅を超える場合は折り返す
        if self.cursor_x + w > self.width:
            self.flush()

        # cursor_xは行内の相対X座標。絶対座標への変換はflush()で行う
        self.line.append((self.cursor_x, word, font))
        # 次の単語の開始位置へ（単語幅 + スペース1文字分）
        self.cursor_x += w + font.measure(" ")

    def flush(self):
        """行バッファの内容をdisplay_listに書き出す。
        行内の全単語をベースライン揃えで配置し、cursor_yを次の行の先頭へ進める。"""
        # バッファが空なら何もしない
        if not self.line: return

        # 行内の全フォントのメトリクスを取得
        metrics = [font.metrics() for x, word, font in self.line]

        # 行内最大アセントを基準にベースライン位置を計算（1.25倍で行間を追加）
        max_ascent = max([metric["ascent"] for metric in metrics])
        baseline = self.cursor_y + 1.25 * max_ascent

        # 各単語を絶対座標に変換してdisplay_listに追加
        for rel_x, word, font in self.line:
            # self.x + rel_x で絶対X座標、self.y + baseline - ascent で絶対Y座標
            x = self.x + rel_x
            y = self.y + baseline - font.metrics("ascent")
            self.display_list.append((x, y, word, font))

        # 行内最大ディセントを基準に次の行のY座標を更新（1.25倍で行間を追加）
        max_descent = max([metric["descent"] for metric in metrics])
        self.cursor_y = baseline + 1.25 * max_descent

        # 行バッファとX座標をリセット（cursor_xは絶対座標ではなく行内の相対位置）
        self.cursor_x = 0
        self.line = []

    def recurse(self, tree):
        """HTMLツリーを深さ優先で再帰走査し、テキストを単語単位でレイアウトする（インラインモード専用）"""
        if isinstance(tree, Text):
            # テキストノード: 空白区切りで単語に分割してword()に渡す
            for word in tree.text.split():
                self.word(word)
        else:
            # 要素ノード: 開きタグ処理 → 子を再帰処理 → 閉じタグ処理
            self.open_tag(tree.tag)
            for child in tree.children:
                self.recurse(child)
            self.close_tag(tree.tag)

    def layout_intermediate(self):
        """子ノードごとに BlockLayout を生成してchildren に追加する（現在は layout() 内で直接処理）"""
        previous = None
        for child in self.node.children:
            next = BlockLayout(child, self, previous)
            self.children.append(next)
            previous = next

    def layout_mode(self):
        """このノードをインラインとブロックのどちらでレイアウトするかを返す。
        子にブロック要素が1つでも含まれていればブロックモード、
        テキストやインライン要素だけならインラインモードとなる。"""
        if isinstance(self.node, Text):
            # テキストノード自体は常にインライン
            return "inline"
        elif any(isinstance(child, Element) and \
                child.tag in self.BLOCK_ELEMENTS
                for child in self.node.children):
            # 子にブロック要素が1つでもあればブロックモード
            return "block"
        elif self.node.children:
            # 子はいるがすべてインライン要素またはテキストノードの場合
            return "inline"
        else:
            # 子が1つもない場合（<hr> など）はブロックモード
            return "block"

    def layout(self):
        """サイズと位置を計算し、必要に応じて子BlockLayoutを生成して再帰的にレイアウトする。
        処理順序:
          1. 自身のx・width・yを親・兄弟から計算（子のlayout()より先に行う）
          2. モードに応じて子BlockLayoutを生成、またはインラインレイアウトを実行
          3. 子のlayout()を再帰呼び出し
          4. heightを確定（子のlayout()完了後に行う）
        """
        # x と width は親から継承（常に親の左端から親幅いっぱいに広がる）
        self.x = self.parent.x
        self.width = self.parent.width

        # y は直前の兄弟の直下、なければ親の上端から開始
        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        mode = self.layout_mode()

        if mode == "block":
            # ブロックモード: 子ノードごとにBlockLayoutを生成して縦に積む
            previous = None
            for child in self.node.children:
                next = BlockLayout(child, self, previous)
                self.children.append(next)
                previous = next
        else:
            # インラインモード: recurse()でテキストを単語単位でレイアウトする
            self.cursor_x = 0
            self.cursor_y = 0
            self.weight = "normal"
            self.style = "roman"
            self.size = 12
            self.line = []
            self.recurse(self.node)
            self.flush()

        # 子のlayout()を再帰呼び出し（ブロックモードのみ子が存在する）
        for child in self.children:
            child.layout()

        # height は子のlayout()完了後に確定する（子の高さに依存するため）
        if mode == "block":
            self.height = sum([child.height for child in self.children])
        else:
            # インラインモードではcursor_yがそのままコンテンツの高さになる
            self.height = self.cursor_y

    def paint(self):
        """このレイアウトオブジェクトが描画すべきコマンドのリストを返す。
        <pre>タグには背景矩形を、インラインモードには各単語のDrawTextを追加する。"""
        cmds = []
        # <pre> タグにはグレーの背景矩形を描画（テキストより先に追加して背景を下に置く）
        if isinstance(self.node, Element) and self.node.tag == "pre":
            x2, y2 = self.x + self.width, self.y + self.height
            rect = DrawRect(self.x, self.y, x2, y2, "gray")
            cmds.append(rect)
        # インラインモードの場合のみ、各単語をDrawTextコマンドとして追加する
        # ブロックモードのBlockLayoutは子へ描画を委譲するため、自身では何も描画しない
        if self.layout_mode() == "inline":
            for x, y, word, font in self.display_list:
                cmds.append(DrawText(x, y, word, font))
        return cmds


class DocumentLayout:
    """レイアウトツリーのルートノード。ページ全体のサイズと位置を管理し、
    直下に1つのBlockLayoutを持つ。"""

    def __init__(self, node):
        self.node = node      # HTMLパーサが返したDOMツリーのルートノード
        self.parent = None    # ルートなので親は存在しない
        self.children = []    # 子レイアウトオブジェクト（BlockLayoutが1つ入る）
        self.x = None
        self.y = None
        self.width = None
        self.height = None

    def layout(self):
        """ページ全体のx・y・widthを設定し、子BlockLayoutを生成してレイアウトする。
        x・y・widthは子のlayout()が参照するため、子の生成より先に設定する。"""
        # ウィンドウ幅から左右のパディング（HSTEP）を引いたものをページ幅とする
        self.width = WIDTH - 2*HSTEP
        self.x = HSTEP   # 左端にHSTEP分の余白
        self.y = VSTEP   # 上端にVSTEP分の余白
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout()
        self.height = child.height  # ページ全体の高さ = 唯一の子の高さ

    def paint(self):
        """DocumentLayout自体は何も描画しない"""
        return []


class Browser:
    """ブラウザのメインクラス。URLの読み込み・描画・スクロールを管理する"""

    def __init__(self):
        # Tkウィンドウとキャンバスを初期化
        self.window = tkinter.Tk()
        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT
        )
        self.canvas.pack()
        self.scroll = 0  # 現在のスクロール量（ピクセル）
        self.window.bind("<Down>", self.scrolldown)

    def load(self, url):
        """URLからHTMLを取得し、パース・レイアウト・描画を行う"""
        body = url.request()
        # HTMLをパースしてDOMツリーを構築
        self.nodes = HTMLParser(body).parse()
        # DocumentLayoutをルートとしてレイアウトツリーを構築・計算する
        self.document = DocumentLayout(self.nodes)
        self.document.layout()
        # paint_tree()でレイアウトツリーを再帰的に走査し、描画コマンドを収集する
        self.display_list = []
        paint_tree(self.document, self.display_list)
        self.draw()

    def draw(self):
        """display_listの描画コマンドを順に実行してキャンバスへ描画する。
        画面外のコマンドはスキップしてパフォーマンスを向上させる。"""
        self.canvas.delete("all")
        for cmd in self.display_list:
            if cmd.top > self.scroll + HEIGHT: continue  # 画面より下にある要素
            if cmd.bottom < self.scroll: continue        # 画面より上にある要素
            cmd.execute(self.scroll, self.canvas)

    def scrolldown(self, e):
        """↓キーでスクロールダウンする。ページ末尾を超えないようにmax_yで上限を設ける"""
        max_y = max(self.document.height + 2*VSTEP - HEIGHT, 0)
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)
        self.draw()


class Text:
    """DOMツリーにおけるテキストノード（タグの外側にある生テキスト）"""

    def __init__(self, text, parent):
        self.text = text
        self.children = []  # テキストノードは子を持たない（一貫性のため空リストを保持）
        self.parent = parent

    def __repr__(self):
        return repr(self.text)


class Element:
    """DOMツリーにおける要素ノード（<div>, <p> などのHTMLタグ）"""

    def __init__(self, tag, attributes, parent):
        self.tag = tag               # タグ名（例: "div", "p", "b"）
        self.attributes = attributes # 属性の辞書（例: {"href": "...", "class": "..."}）
        self.children = []           # 子ノードのリスト
        self.parent = parent         # 親ノード

    def __repr__(self):
        return "<" + self.tag + ">"


class HTMLParser:
    """HTML文字列をパースしてDOMツリー（ElementとTextのノード木）を構築するパーサー"""

    def __init__(self, body):
        self.body = body
        # 未完了（閉じタグ未着）のElementノードをスタックで管理
        self.unfinished = []
        # 自己終了タグ（閉じタグ不要）の一覧
        self.SELF_CLOSING_TAGS = [
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        ]
        # <head> 内に属するタグの一覧（implicit_tagsで自動挿入の判定に使用）
        self.HEAD_TAGS = [
            "base", "basefont", "bgsound", "noscript",
            "link", "meta", "title", "style", "script",
        ]

    def parse(self):
        """HTML文字列を1文字ずつ走査し、テキストとタグに分けてDOMツリーを構築する"""
        text = ""
        in_tag = False
        for c in self.body:
            if c == "<":
                in_tag = True
                if text: self.add_text(text)  # タグ開始前のテキストを処理
                text = ""
            elif c == ">":
                in_tag = False
                self.add_tag(text)            # タグ内容を処理
                text = ""
            else:
                text += c
        # ループ終了後にタグ外のテキストが残っていれば処理
        if not in_tag and text:
            self.add_text(text)
        return self.finish()

    def add_text(self, text):
        """テキストノードを作成し、現在の親ノードの子として追加する"""
        # 空白のみのテキストは無視
        if text.isspace(): return
        self.implicit_tags(None)
        parent = self.unfinished[-1]
        node = Text(text, parent)
        parent.children.append(node)

    def add_tag(self, tag):
        """タグを解析し、開きタグ・閉じタグ・自己終了タグに応じてDOMツリーを更新する"""
        tag, attributes = self.get_attributes(tag)
        # DOCTYPE宣言やコメントなど "!" で始まるタグは無視
        if tag.startswith("!"): return
        self.implicit_tags(tag)

        if tag.startswith("/"):
            # 閉じタグ: スタックからノードをポップして親の子として追加
            # スタックに1つしかない場合（ルートのhtml要素）はポップしない
            if len(self.unfinished) == 1: return
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        elif tag in self.SELF_CLOSING_TAGS:
            # 自己終了タグ: スタックに積まず直接親の子として追加
            parent = self.unfinished[-1]
            node = Element(tag, attributes, parent)
            parent.children.append(node)
        else:
            # 通常の開きタグ: スタックに積んで閉じタグを待つ
            parent = self.unfinished[-1] if self.unfinished else None
            node = Element(tag, attributes, parent)
            self.unfinished.append(node)

    def finish(self):
        """パース終了時にスタックに残ったノードをすべて閉じてルートノードを返す"""
        # スタックが空の場合は暗黙タグを補完してから処理
        if not self.unfinished:
            self.implicit_tags(None)
        # スタックに残ったノードを順番に親へ追加していく
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        # 最後に残ったルートノード（html要素）を返す
        return self.unfinished.pop()

    def get_attributes(self, text):
        """タグ文字列をタグ名と属性辞書に分解して返す"""
        parts = text.split()
        tag = parts[0].casefold()
        attributes = {}
        for attrpair in parts[1:]:
            if "=" in attrpair:
                key, value = attrpair.split("=", 1)
                # クォートで囲まれた値からクォートを除去
                if len(value) > 2 and value[0] in ["'", "\""]:
                    value = value[1:-1]
                attributes[key.casefold()] = value
            else:
                # 値のない属性（例: <input disabled>）は空文字列を設定
                attributes[attrpair.casefold()] = ""
        return tag, attributes

    def implicit_tags(self, tag):
        """HTMLの省略されたタグ（<html>, <head>, <body>）を暗黙的に補完する"""
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if open_tags == [] and tag != "html":
                # スタックが空かつ html タグ以外が来たら <html> を自動挿入
                self.add_tag("html")
            elif open_tags == ["html"] \
                    and tag not in ["head", "body", "/html"]:
                # html直下でhead/body以外が来たらタグ種別に応じて自動挿入
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")
            elif open_tags == ["html", "head"] and \
                    tag not in ["/head"] + self.HEAD_TAGS:
                # head内にbody系タグが来たら </head> を自動挿入
                self.add_tag("/head")
            else:
                break


class DrawText:
    """テキスト1単語を描画するコマンド"""

    def __init__(self, x1, y1, text, font):
        self.top = y1
        self.left = x1
        self.text = text
        self.font = font
        self.bottom = y1 + font.metrics("linespace")  # 画面外スキップ判定に使用

    def execute(self, scroll, canvas):
        """スクロール量を考慮してキャンバスにテキストを描画する"""
        canvas.create_text(
            self.left, self.top - scroll,
            text=self.text,
            font=self.font,
            anchor='nw'
        )


class DrawRect:
    """矩形を描画するコマンド（背景色などに使用）"""

    def __init__(self, x1, y1, x2, y2, color):
        self.top = y1
        self.left = x1
        self.bottom = y2
        self.right = x2
        self.color = color

    def execute(self, scroll, canvas):
        """スクロール量を考慮してキャンバスに矩形を描画する。
        width=0 でtkinterのデフォルトの枠線を非表示にする。"""
        canvas.create_rectangle(
            self.left, self.top - scroll,
            self.right, self.bottom - scroll,
            width=0,
            fill=self.color
        )


def print_tree(node, indent=0):
    """DOMツリーをインデント付きでコンソールに出力するデバッグ用関数"""
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


def paint_tree(layout_object, display_list):
    """レイアウトツリーを再帰的に走査し、各ノードの描画コマンドをdisplay_listに収集する。
    親のpaint()を先に呼ぶことで、背景などが子テキストより下のレイヤーに描画される。"""
    display_list.extend(layout_object.paint())

    for child in layout_object.children:
        paint_tree(child, display_list)


if __name__ == "__main__":
    import sys
    Browser().load(URL(sys.argv[1]))
    tkinter.mainloop()