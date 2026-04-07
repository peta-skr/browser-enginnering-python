"""
シンプルなゲストブックサーバー（Chapter 10: セキュリティ）。
Cookie によるセッション管理、CSRF 対策のノンス、CSP ヘッダーを実装している。
"""
import html
import socket
import urllib.parse
import random

# token -> セッション辞書（ユーザー情報・ノンスを格納）
SESSIONS = {}
# ゲストブックのエントリ一覧。(コメント本文, 投稿者名) のタプルリスト
ENTRIES = [('Pavel was here', 'Pavel')]

# TCPソケットを作成してポート8000でリッスン
s = socket.socket(
    family=socket.AF_INET,
    type=socket.SOCK_STREAM,
    proto=socket.IPPROTO_TCP
)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # 再起動時のアドレス再利用を許可

s.bind(('', 8000))
s.listen()


def handle_connection(conx):
    """1つのHTTP接続を処理する。リクエストを解析し、セッションを取得してレスポンスを返す。"""
    req = conx.makefile("b")

    # リクエストライン（例: "GET / HTTP/1.0"）を解析
    reqline = req.readline().decode('utf8')
    method, url, version = reqline.split(" ", 2)
    assert method in ["GET", "POST"]

    # ヘッダーを辞書に格納（キーは小文字に正規化）
    headers = {}
    while True:
        line = req.readline().decode('utf8')
        if line == '\r\n': break
        header, value = line.split(":", 1)
        headers[header.casefold()] = value.strip()

    # POSTリクエストのボディを読み込む
    if 'content-length' in headers:
        length = int(headers["content-length"])
        body = req.read(length).decode('utf8')
    else:
        body = None

    # Cookieからセッショントークンを取得。なければ新規発行する
    if "cookie" in headers:
        token = headers["cookie"][len("token="):]
    else:
        token = str(random.random())[2:]

    # トークンに紐づくセッション辞書を取得（なければ空辞書を作成）
    session = SESSIONS.setdefault(token, {})
    status, body = do_request(method, url, headers, body, session)

    # レスポンスを組み立てる
    response = "HTTP/1.0 {}\r\n".format(status)
    response += "Content-Length: {}\r\n".format(len(body.encode("utf8")))
    # 新規クライアントにはSameSite=LaxのCookieを発行する
    if "cookie" not in headers:
        template = "Set-Cookie: token={}; SameSite=Lax\r\n"
        response += template.format(token)
    # CSPヘッダー: このサーバー以外からのスクリプト読み込みをブロックする
    csp =  "default-src http://localhost:8000"
    response += "Content-Security-Policy: {}\r\n".format(csp)
    response += "\r\n" + body
    conx.send(response.encode('utf8'))
    conx.close()

def show_comments(session):
    """ゲストブックのHTMLを生成して返す。
    ログイン済みセッションにはCSRF対策のノンスを埋め込む。
    XSS対策としてhtml.escape()でエントリをエスケープする。"""
    out = "<!doctype html>"
    if "user" in session:
        # CSRF対策: ランダムなノンスをセッションとhiddenフィールド両方に保存する
        nonce = str(random.random())[2:]
        session["nonce"] = nonce
        out +=  "<input name=nonce type=hidden value=" + nonce + ">"
    for entry, who in ENTRIES:
        # XSS対策: ユーザー入力をHTMLエスケープして出力する
        out += "<p>" + html.escape(entry) + "</p>"
        out += "<i>by " + html.escape(who) + "</i></p>"
    out += "<form action=add method=post>"
    out +=   "<p><input name=guest></p>"
    out +=   "<p><button>Sign the book!</button></p>"
    out += "</form>"
    out += "<strong></strong>"
    out += "<script src=/comment.js></script>"
    # CSPにより https://example.com/evil.js はブラウザ側でブロックされる（意図的なデモ）
    out += "<script src=https://example.com/evil.js></script>"
    return out

def do_request(method, url, headers, body, session):
    """URLとHTTPメソッドに応じてルーティングする"""
    if method == "GET" and url == "/":
        return "200 OK", show_comments(session)
    elif method == "GET" and url == "/comment.js":
        with open("comment.js", encoding="utf-8") as f:
            return "200 OK", f.read()
    elif method == "POST" and url == "/add":
        params = form_decode(body)
        return "200 OK", add_entry(session, params)
    else:
        return "404 Not Found", not_found(url, method)

def form_decode(body):
    """URLエンコードされたフォームボディ（a=1&b=2形式）をデコードして辞書に変換する"""
    params = {}
    for field in body.split("&"):
        name, value = field.split("=", 1)
        name = urllib.parse.unquote_plus(name)
        value = urllib.parse.unquote_plus(value)
        params[name] = value
    return params

def add_entry(session, params):
    """CSRF対策のノンスを検証してからゲストブックにエントリを追加する。
    ノンス不一致・未設定の場合は追加せずにコメント一覧を返す。"""
    if "nonce" not in session or "nonce" not in params: return show_comments(session)
    if session["nonce"] != params["nonce"]: return show_comments(session)
    if 'guest' in params and len(params['guest']) <= 100:
        ENTRIES.append((params['guest'], session.get("user", "anonymous")))
    return show_comments(session)

def not_found(url, method):
    """404レスポンス用のHTMLを生成して返す"""
    out = "<!doctype html>"
    out += "<h1>{} {} not found!</h1>".format(method, url)
    return out


# メインループ: 接続を順次受け付けて処理する（シングルスレッド）
while True:
    conx, addr = s.accept()
    handle_connection(conx)