import argparse
import socket
import subprocess
import sys
import threading
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

class PyNcat:
    def __init__(self):
        self.args = self.parse_args()
        
    def parse_args(self):
        parser = argparse.ArgumentParser(description="Better Netcat - PyNcat")
        parser.add_argument('-l', '--listen', action='store_true', help='Listen mode')
        parser.add_argument('-c', '--connect', help='Target host to connect')
        parser.add_argument('-p', '--port', type=int, required=True, help='Port')
        parser.add_argument('-u', '--udp', action='store_true', help='UDP mode')
        parser.add_argument('-e', '--execute', help='Command to execute on connect')
        parser.add_argument('-f', '--file', help='File to upload')
        parser.add_argument('-o', '--output', help='Output file to write received data')
        parser.add_argument('-v', '--verbose', action='store_true')
        return parser.parse_args()

    def run(self):
        if self.args.listen:
            self.listen()
        else:
            self.connect()

    def execute(self, cmd):
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            return output.decode()
        except Exception as e:
            return f"Error: {str(e)}\n"

    def handle_client(self, client_socket):
        if self.args.execute:
            output = self.execute(self.args.execute)
            client_socket.send(output.encode())
        
        if self.args.file:
            self.upload_file(client_socket)
            
        # Interactive shell loop
        while True:
            try:
                client_socket.send(b"pyncat> ")
                cmd = client_socket.recv(4096).decode().strip()
                if not cmd:
                    continue
                if cmd.lower() in ['exit', 'quit']:
                    break
                    
                output = self.execute(cmd)
                client_socket.send(output.encode())
            except Exception:
                break

    def listen(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if self.args.udp else socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", self.args.port))
        if not self.args.udp:
            server.listen(5)
            logging.info(f"[*] Listening on 0.0.0.0:{self.args.port}")
            
            while True:
                client, addr = server.accept()
                logging.info(f"[+] Accepted connection from {addr}")
                client_handler = threading.Thread(target=self.handle_client, args=(client,))
                client_handler.start()
        else:
            # UDP handling...
            pass

    def connect(self):
        # Client code here
        pass

if __name__ == "__main__":
    pyncat = PyNcat()
    pyncat.run()
