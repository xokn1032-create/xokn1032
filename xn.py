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
        parser = argparse.ArgumentParser(description="PyNcat - Better Netcat with SSL", 
                                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode')
        group.add_argument('-c', '--connect', type=str, help='Connect to target IP')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='Port number')
        parser.add_argument('-u', '--udp', action='store_true', help='Use UDP (SSL not supported with UDP)')
        
        # Features
        parser.add_argument('-e', '--execute', type=str, help='Execute command on connection')
        parser.add_argument('-f', '--file', type=str, help='File to upload/save')
        parser.add_argument('-o', '--output', type=str, help='Save received data to file')
        
        # SSL Options
        parser.add_argument('--ssl', action='store_true', help='Enable SSL/TLS encryption')
        parser.add_argument('--cert', type=str, help='Path to SSL certificate (.pem)')
        parser.add_argument('--key', type=str, help='Path to SSL private key (.pem)')
        
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
        
        return parser.parse_args()

    def setup_ssl(self):
        """Setup SSL context and generate self-signed cert if needed"""
        if self.args.udp:
            logging.warning("[!] SSL is not supported with UDP. Disabling SSL.")
            self.args.ssl = False
            return

        cert_path = self.args.cert
        key_path = self.args.key

        if not cert_path or not key_path:
            logging.info("[*] No cert/key provided. Generating self-signed certificate...")
            cert_path, key_path = self.generate_self_signed_cert()

        try:
            self.ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            if self.args.listen:
                self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                self.ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            else:
                self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                self.ssl_context.check_hostname = False
                self.ssl_context.verify_mode = ssl.CERT_NONE  # For self-signed
        except Exception as e:
            logging.error(f"SSL setup failed: {e}")
            sys.exit(1)

    def generate_self_signed_cert(self):
        """Generate temporary self-signed certificate"""
        try:
            from OpenSSL import crypto  # pip install pyOpenSSL
        except ImportError:
            logging.error("[!] pyOpenSSL not installed. Install with: pip install pyOpenSSL")
            logging.error("Or provide --cert and --key manually.")
            sys.exit(1)

        cert_path = "/tmp/pyncat_cert.pem"
        key_path = "/tmp/pyncat_key.pem"

        # Generate key
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 2048)

        # Generate cert
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

        logging.info(f"[+] Self-signed cert generated: {cert_path}")
        return cert_path, key_path

    def execute_command(self, cmd: str) -> str:
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=15)
            return output.decode('utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            return "[!] Command timed out\n"
        except Exception as e:
            return f"[!] Error: {e}\n"

    def upload_file(self, client_socket, filepath):
        try:
            with open(filepath, 'wb') as f:
                logging.info(f"[*] Receiving file → {filepath}")
                while True:
                    data = client_socket.recv(8192)
                    if not data:
                        break
                    f.write(data)
            logging.info(f"[+] File saved: {filepath}")
        except Exception as e:
            logging.error(f"Upload failed: {e}")

    def send_file(self, client_socket, filepath):
        try:
            path = Path(filepath)
            if not path.exists():
                logging.error(f"File not found: {filepath}")
                return

            filesize = path.stat().st_size
            with open(path, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="Uploading") as pbar:
                while chunk := f.read(8192):
                    client_socket.send(chunk)
                    pbar.update(len(chunk))
            logging.info("[+] File sent successfully")
        except Exception as e:
            logging.error(f"Send failed: {e}")

    def handle_client(self, client_socket, addr):
        logging.info(f"[+] SSL Connection from {addr}")

        try:
            if self.args.execute:
                output = self.execute_command(self.args.execute)
                client_socket.send(output.encode())

            if self.args.file and self.args.listen:
                self.upload_file(client_socket, self.args.file)

            while True:
                client_socket.send(b"pyncat> ")
                request = client_socket.recv(8192).decode('utf-8').strip()
                if not request:
                    break
                if request.lower() in ['exit', 'quit']:
                    client_socket.send(b"[*] Goodbye!\n")
                    break
                output = self.execute_command(request)
                client_socket.send(output.encode())
        except Exception as e:
            logging.debug(f"Handler error: {e}")
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
            logging.info(f"[*] SSL Listener on 0.0.0.0:{self.args.port} {'(SSL)' if self.args.ssl else ''}")

            while True:
                client, addr = server.accept()
                thread = threading.Thread(target=self.handle_client, args=(client, addr))
                thread.daemon = True
                thread.start()

        except KeyboardInterrupt:
            logging.info("\n[!] Listener shutting down...")
        except Exception as e:
            logging.error(f"Listen error: {e}")
        finally:
            server.close()

    def connect(self):
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            logging.info(f"[*] Connecting to {self.args.connect}:{self.args.port} {'(SSL)' if self.args.ssl else ''}")

            if self.args.ssl:
                client = self.ssl_context.wrap_socket(client)

            client.connect((self.args.connect, self.args.port))
            logging.info("[+] SSL Connection established!")

            if self.args.file:
                self.send_file(client, self.args.file)
                return

            while True:
                response = client.recv(8192).decode('utf-8', errors='replace')
                if response:
                    print(response, end='')

                cmd = input()
                if cmd.lower() in ['exit', 'quit']:
                    break
                client.send(cmd.encode() + b'\n')

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
        print("\n[!] PyNcat terminated.")
    except Exception as e:
        logging.error(f"Critical error: {e}")
