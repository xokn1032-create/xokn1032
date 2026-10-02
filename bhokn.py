import socket

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(playstation.com,5555)
client.send(b"GET / HTTP/1.1\r\nHost: playstation.com\r\n\r\n")
# receive some data
response = client.recv(4096)
print(response.decode())
client.close()
