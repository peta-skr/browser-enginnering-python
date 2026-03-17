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


def lex(body):
    """HTMLボディ文字列をTextトークンとTagトークンのリストに変換する"""
    out = []
    buffer = ""
    in_tag = False
    for c in body:
        if c == "<":
            in_tag = True
            if buffer: out.append(Text(buffer))  # タグ開始前のテキストを保存
            buffer = ""
        elif c == ">":
            in_tag = False
            out.append(Tag(buffer))  # タグの内容を保存
            buffer = ""
        else:
            buffer += c
    # ループ終了後に残ったテキストを保存（タグの途中でない場合のみ）
    if not in_tag and buffer:
        out.append(Text(buffer))
    return out


class Layout:
    def __init__(self, tokens):
        self.display_list = []   # 描画命令リスト: (x, y, word, font) のタプル
        self.cursor_x = HSTEP   # 現在の描画X座標
        self.cursor_y = VSTEP   # 現在の描画Y座標
        self.weight = "normal"  # フォントウェイト（"normal" or "bold"）
        self.style = "roman"    # フォントスタイル（"roman" or "italic"）
        self.size = 12          # フォントサイズ（ポイント）
        self.line = []          # 現在処理中の行バッファ: (x, word, font) のタプル

        for tok in tokens:
            self.token(tok)
        # 最後の行をフラッシュ（ループ後にバッファに残った行を処理）
        self.flush()

    def token(self, tok):
        """トークンの種類に応じてスタイル変更または単語レイアウトを行う"""
        if isinstance(tok, Text):
            # テキストトークンは単語単位でレイアウト
            for word in tok.text.split():
                self.word(word)
        elif tok.tag == "i":
            self.style = "italic"
        elif tok.tag == "/i":
            self.style = "roman"
        elif tok.tag == "b":
            self.weight = "bold"
        elif tok.tag == "/b":
            self.weight = "normal"
        elif tok.tag == "small":
            self.size -= 2
        elif tok.tag == "/small":
            self.size += 2
        elif tok.tag == "big":
            self.size += 4
        elif tok.tag == "/big":
            self.size -= 4
        elif tok.tag == "br":
            # <br> は強制改行
            self.flush()
        elif tok.tag == "/p":
            # </p> は改行 + 段落間の余白を追加
            self.flush()
            self.cursor_y += VSTEP

    def word(self, word):
        """1単語を行バッファに追加する。行をはみ出す場合はフラッシュして折り返す"""
        font = get_font(self.size, self.weight, self.style)
        w = font.measure(word)

        # 単語が現在行に収まらない場合は改行（flush）
        if self.cursor_x + w > WIDTH - HSTEP:
            self.flush()

        # 行バッファに追加（Y座標はflush時に確定するためここでは持たない）
        self.line.append((self.cursor_x, word, font))
        # 次の単語の開始位置へ（単語幅 + スペース1文字分）
        self.cursor_x += w + font.measure(" ")

    def flush(self):
        """行バッファをdisplay_listに書き出し、ベースライン揃えとY座標更新を行う"""
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

        # 行バッファをリセット
        self.cursor_x = HSTEP
        self.line = []


class Browser:
    def __init__(self):
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
        """URLからHTMLを取得してレイアウトし描画する"""
        body = url.request()
        tokens = lex(body)
        self.display_list = Layout(tokens).display_list
        self.draw()

    def draw(self):
        """display_listを元にキャンバスへテキストを描画する"""
        self.canvas.delete("all")
        for x, y, c, f in self.display_list:
            # 画面外の要素はスキップしてパフォーマンスを向上
            if y > self.scroll + HEIGHT: continue  # 画面より下
            if y + VSTEP < self.scroll: continue   # 画面より上
            self.canvas.create_text(x, y - self.scroll, text=c, font=f, anchor="nw")

    def scrolldown(self, e):
        """↓キーでスクロールダウン"""
        self.scroll += SCROLL_STEP
        self.draw()


class Text:
    """タグの外側にある生テキストを表すトークン"""
    def __init__(self, text):
        self.text = text


class Tag:
    """HTMLタグ（<b>, </b> など）を表すトークン"""
    def __init__(self, tag):
        self.tag = tag


if __name__ == "__main__":
    import sys
    Browser().load(URL(sys.argv[1]))
    tkinter.mainloop()