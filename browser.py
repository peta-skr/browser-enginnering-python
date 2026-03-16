import socket
import ssl

def request(url):
    # URLパース
    scheme, url = url.split("://", 1)
    assert scheme in ["http", "https"], f"Unknown scheme {scheme}"
    
    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host, path = url, "/"
    
    # ポート処理
    port = 80 if scheme == "http" else 443
    if ":" in host:
        host, port = host.rsplit(":", 1)
        port = int(port)
    
    # ソケット接続
    s = socket.socket(
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    s.connect((host, port))
    
    if scheme == "https":
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(s, server_hostname=host)
    
    # HTTPリクエスト送信
    s.send(("GET {} HTTP/1.0\r\n".format(path) +
            "Host: {}\r\n\r\n".format(host)).encode("utf8"))
    
    # レスポンス受信
    response = s.makefile("r", encoding="utf8", newline="\r\n")
    
    statusline = response.readline()
    version, status, explanation = statusline.split(" ", 2)
    
    # ヘッダ読み込み
    response_headers = {}
    while True:
        line = response.readline()
        if line == "\r\n":
            break
        header, value = line.split(":", 1)
        response_headers[header.casefold()] = value.strip()
    
    assert "transfer-encoding" not in response_headers
    assert "content-encoding" not in response_headers
    
    # ボディ読み込み
    body = response.read()
    s.close()
    
    return response_headers, body


def show(body):
    in_tag = False
    for c in body:
        if c == "<":
            in_tag = True
        elif c == ">":
            in_tag = False
        elif not in_tag:
            print(c, end="")


def load(url):
    headers, body = request(url)
    show(body)


if __name__ == "__main__":
    import sys
    load(sys.argv[1])