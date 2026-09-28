#!/usr/bin/env python3
import argparse
import socket
import subprocess
import sys
import threading
import logging
from pathlib import Path
import tqdm  # pip install tqdm

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

class PyNcat:
    def __init__(self):
        self.args = self.parse_args()
        if self.args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)

    def parse_args(self):
        parser = argparse.ArgumentParser(description="PyNcat - Better Netcat in Python", 
                                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        
        # Connection modes
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode (server)')
        group.add_argument('-c', '--connect', type=str, help='Connect to target IP')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='Port number')
        parser.add_argument('-u', '--udp', action='store_true', help='Use UDP instead of TCP')
        
        # Features
        parser.add_argument('-e', '--execute', type=str, help='Execute a command on connection')
        parser.add_argument('-f', '--file', type=str, help='File to upload (client) or save to (listener)')
        parser.add_argument('-o', '--output', type=str, help='Save received data to file')
        
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
        
        return parser.parse_args()

    def execute_command(self, cmd: str) -> str:
        """Execute shell command and return output"""
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, 
                                           timeout=15)
            return output.decode('utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            return "[!] Command timed out\n"
        except Exception as e:
            return f"[!] Error executing command: {e}\n"

    def upload_file(self, client_socket: socket.socket, filepath: str):
        """Receive file from client"""
        try:
            with open(filepath, 'wb') as f:
                logging.info(f"[*] Receiving file: {filepath}")
                while True:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    f.write(data)
            logging.info(f"[+] File saved successfully: {filepath}")
        except Exception as e:
            logging.error(f"File upload failed: {e}")

    def send_file(self, client_socket: socket.socket, filepath: str):
        """Send file to listener"""
        try:
            file_path = Path(filepath)
            if not file_path.exists():
                logging.error(f"File not found: {filepath}")
                return

            filesize = file_path.stat().st_size
            logging.info(f"[*] Sending {file_path.name} ({filesize:,} bytes)")

            with open(file_path, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', 
                                                      unit_scale=True, desc="Uploading") as pbar:
                while True:
                    data = f.read(4096)
                    if not data:
                        break
                    client_socket.send(data)
                    pbar.update(len(data))
            
            logging.info("[+] File sent successfully")
        except Exception as e:
            logging.error(f"File send failed: {e}")

    def handle_client(self, client_socket: socket.socket, addr):
        """Handle incoming client connection"""
        logging.info(f"[+] Connection from {addr}")

        try:
            if self.args.execute:
                output = self.execute_command(self.args.execute)
                client_socket.send(output.encode())

            if self.args.file and self.args.listen:
                self.upload_file(client_socket, self.args.file)

            # Interactive shell
            while True:
                client_socket.send(b"pyncat> ")
                try:
                    request = client_socket.recv(8192).decode('utf-8').strip()
                    if not request:
                        break
                    
                    if request.lower() in ['exit', 'quit', 'bye']:
                        client_socket.send(b"[*] Goodbye!\n")
                        break

                    output = self.execute_command(request)
                    client_socket.send(output.encode())
                except ConnectionResetError:
                    break
                except Exception as e:
                    logging.debug(f"Shell error: {e}")
                    break

        except Exception as e:
            logging.error(f"Client handler error: {e}")
        finally:
            client_socket.close()

    def listen(self):
        """Start listener"""
        try:
            sock_type = socket.SOCK_DGRAM if self.args.udp else socket.SOCK_STREAM
            server = socket.socket(socket.AF_INET, sock_type)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.args.port))

            if not self.args.udp:
                server.listen(5)
                logging.info(f"[*] Listening on 0.0.0.0:{self.args.port} (TCP)")
                
                while True:
                    client, addr = server.accept()
                    thread = threading.Thread(target=self.handle_client, 
                                           args=(client, addr))
                    thread.daemon = True
                    thread.start()
            else:
                logging.info(f"[*] Listening on 0.0.0.0:{self.args.port} (UDP)")
                while True:
                    data, addr = server.recvfrom(4096)
                    logging.info(f"[+] UDP packet from {addr}")
                    if self.args.execute:
                        output = self.execute_command(self.args.execute)
                        server.sendto(output.encode(), addr)

        except KeyboardInterrupt:
            logging.info("\n[!] Shutting down listener...")
        except Exception as e:
            logging.error(f"Listener error: {e}")
        finally:
            server.close()

    def connect(self):
        """Client connection"""
        try:
            sock_type = socket.SOCK_DGRAM if self.args.udp else socket.SOCK_STREAM
            client = socket.socket(socket.AF_INET, sock_type)
            
            logging.info(f"[*] Connecting to {self.args.connect}:{self.args.port}")
            client.connect((self.args.connect, self.args.port))
            logging.info("[+] Connected!")

            if self.args.file:
                self.send_file(client, self.args.file)
                return  # Exit after sending file

            # Interactive mode
            while True:
                try:
                    response = client.recv(8192).decode('utf-8', errors='replace')
                    if response:
                        print(response, end='')

                    cmd = input()
                    if cmd.lower() in ['exit', 'quit']:
                        break
                    
                    client.send(cmd.encode() + b'\n')
                except (ConnectionResetError, BrokenPipeError):
                    logging.info("[!] Connection closed by server")
                    break
                except KeyboardInterrupt:
                    break

        except Exception as e:
            logging.error(f"Connection error: {e}")
        finally:
            client.close()

    def run(self):
        if self.args.listen:
            self.listen()
        else:
            self.connect()


if __name__ == "__main__":
    try:
        pyncat = PyNcat()
        pyncat.run()
    except KeyboardInterrupt:
        print("\n[!] PyNcat terminated by user.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
