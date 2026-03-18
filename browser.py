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


class Layout:
    """HTMLツリーを走査し、各単語の描画座標を計算するレイアウトエンジン"""

    def __init__(self, tree):
        self.display_list = []   # 描画命令リスト: (x, y, word, font) のタプル
        self.cursor_x = HSTEP   # 現在の描画X座標
        self.cursor_y = VSTEP   # 現在の描画Y座標
        self.weight = "normal"  # フォントウェイト（"normal" or "bold"）
        self.style = "roman"    # フォントスタイル（"roman" or "italic"）
        self.size = 12          # フォントサイズ（ポイント）
        self.line = []          # 現在処理中の行バッファ: (x, word, font) のタプル

        # ツリーを再帰的に走査してレイアウトを構築
        self.recurse(tree)
        # 最後の行をフラッシュ（ループ後にバッファに残った行を処理）
        self.flush()

    def open_tag(self, tag):
        """開きタグに応じてフォントスタイル・サイズを変更する"""
        if tag == "i":
            self.style = "italic"
        elif tag == "b":
            self.weight = "bold"
        elif tag == "small":
            self.size -= 2
        elif tag == "big":
            self.size += 4
        elif tag == "br":
            # <br> は自己終了タグだが、open_tag側で改行を処理する
            self.flush()

    def close_tag(self, tag):
        """閉じタグに応じてフォントスタイル・サイズを元に戻す"""
        if tag == "i":
            self.style = "roman"
        elif tag == "b":
            self.weight = "normal"
        elif tag == "small":
            self.size += 2
        elif tag == "big":
            self.size -= 4
        elif tag == "p":
            # </p> は改行 + 段落間の余白を追加
            self.flush()
            self.cursor_y += VSTEP

    def word(self, word):
        """1単語を行バッファに追加する。行をはみ出す場合はフラッシュして折り返す"""
        font = get_font(self.size, self.weight, self.style)
        w = font.measure(word)

        # 単語が現在行に収まらない場合は改行（flush）して折り返す
        if self.cursor_x + w > WIDTH - HSTEP:
            self.flush()

        # 行バッファに追加（Y座標はflush時に確定するためここでは持たない）
        self.line.append((self.cursor_x, word, font))
        # 次の単語の開始位置へ（単語幅 + スペース1文字分）
        self.cursor_x += w + font.measure(" ")

    def flush(self):
        """行バッファをdisplay_listに書き出し、ベースライン揃えとY座標更新を行う"""
        # バッファが空なら何もしない
        if not self.line: return

        # 行内の全フォントのメトリクスを取得
        metrics = [font.metrics() for x, word, font in self.line]

        # 行内最大アセントを基準にベースライン位置を計算（1.25倍でレディングを追加）
        max_ascent = max([metric["ascent"] for metric in metrics])
        baseline = self.cursor_y + 1.25 * max_ascent

        # 各単語をベースラインに揃えてdisplay_listに追加
        for x, word, font in self.line:
            # Y座標 = ベースライン - その単語のアセント（上端をベースラインに揃える）
            y = baseline - font.metrics("ascent")
            self.display_list.append((x, y, word, font))

        # 行内最大ディセントを基準に次の行のY座標を更新（1.25倍でレディングを追加）
        max_descent = max([metric["descent"] for metric in metrics])
        self.cursor_y = baseline + 1.25 * max_descent

        # 行バッファとX座標をリセット
        self.cursor_x = HSTEP
        self.line = []

    def recurse(self, tree):
        """HTMLツリーを深さ優先で再帰走査し、テキストノードを単語単位でレイアウトする"""
        if isinstance(tree, Text):
            # テキストノード: 空白区切りで単語に分割してレイアウトに追加
            for word in tree.text.split():
                self.word(word)
        else:
            # 要素ノード: 開きタグ処理 → 子を再帰処理 → 閉じタグ処理
            self.open_tag(tree.tag)
            for child in tree.children:
                self.recurse(child)
            self.close_tag(tree.tag)


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
        # ↓キーにスクロールダウンを割り当て
        self.window.bind("<Down>", self.scrolldown)

    def load(self, url):
        """URLからHTMLを取得し、パース・レイアウト・描画を行う"""
        body = url.request()
        # HTMLをパースしてDOMツリーを構築
        self.nodes = HTMLParser(body).parse()
        # DOMツリーをレイアウトして描画命令リストを生成
        self.display_list = Layout(self.nodes).display_list
        self.draw()

    def draw(self):
        """display_listを元にキャンバスへテキストを描画する"""
        self.canvas.delete("all")
        for x, y, c, f in self.display_list:
            # 画面外の要素はスキップしてパフォーマンスを向上
            if y > self.scroll + HEIGHT: continue  # 画面より下にある要素
            if y + VSTEP < self.scroll: continue   # 画面より上にある要素
            self.canvas.create_text(x, y - self.scroll, text=c, font=f, anchor="nw")

    def scrolldown(self, e):
        """↓キーでスクロールダウンし、再描画する"""
        self.scroll += SCROLL_STEP
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
            # ルートノード（htmlタグ）は誤った閉じタグでポップしない
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


def print_tree(node, indent=0):
    """DOMツリーをインデント付きでコンソールに出力するデバッグ用関数"""
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


if __name__ == "__main__":
    import sys
    Browser().load(URL(sys.argv[1]))
    tkinter.mainloop()