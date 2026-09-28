import http.server

server = http.server.HTTPServer(
    ("127.0.0.1", 8080),
    http.server.SimpleHTTPRequestHandler
)

server.serve_forever()
