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
import os
import hashlib
import time
import select

# Try loading structural AES-GCM engine components for UDP security profiles
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

class SecureUDPWrapper:
    """Handles AES-GCM authenticated symmetric encryption for raw UDP datagrams."""
    def __init__(self, psk: str):
        # Derive a robust 256-bit key using PBKDF2 with a fixed salt
        salt = b'PyNcat_Static_Salt_For_UDP_AES'
        self.key = hashlib.pbkdf2_hmac('sha256', psk.encode(), salt, 100000, 32)
        self.aesgcm = AESGCM(self.key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(12)  # Unique 12-byte nonce per transmitted datagram packet
        ciphertext = self.aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    def decrypt(self, encrypted_packet: bytes) -> bytes:
        if len(encrypted_packet) < 28:  # 12 bytes nonce + 16 bytes minimum auth tag
            raise ValueError("Packet structurally too short.")
        nonce = encrypted_packet[:12]
        ciphertext = encrypted_packet[12:]
        return self.aesgcm.decrypt(nonce, ciphertext, None)


class PyNcat:
    def __init__(self):
        self.args = self.parse_args()
        if self.args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        self.ssl_context = None
        self.udp_crypto = None
        
        # Security initialization routing
        if self.args.ssl:
            if self.args.udp:
                if not HAS_CRYPTOGRAPHY:
                    logging.error("[!] Secure UDP requested, but 'cryptography' library is missing.")
                    logging.error("Install it using: pip install cryptography")
                    sys.exit(1)
                if not self.args.key:
                    logging.error("[!] UDP encryption requires a passphrase via --key <passphrase>")
                    sys.exit(1)
                logging.info("[*] Initializing AES-GCM Engine for Encrypted UDP mode.")
                self.udp_crypto = SecureUDPWrapper(self.args.key)
            else:
                self.setup_ssl()

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description="PyNcat - Secure Netcat (TLS for TCP / AES-GCM for UDP)", 
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode')
        group.add_argument('-c', '--connect', type=str, help='Connect to target IP')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='Port number')
        parser.add_argument('-u', '--udp', action='store_true', help='Use UDP mode')
        
        # Operation profiles
        parser.add_argument('-e', '--execute', type=str, help='Execute command on connection')
        parser.add_argument('-f', '--file', type=str, help='File to upload/transmit')
        parser.add_argument('-o', '--output', type=str, help='Save received data to file')
        
        # Encryption Options
        parser.add_argument('--ssl', action='store_true', help='Enable Encryption (TLS for TCP, AES for UDP)')
        parser.add_argument('--cert', type=str, help='Path to TLS certificate (.pem) [TCP Listener Only]')
        parser.add_argument('--key', type=str, help='TLS Private Key path (.pem) OR UDP Pre-shared Password')
        
        # Reconnect Settings
        parser.add_argument('--retry', type=int, default=0, help='Max connection retry attempts (-1 for infinite)')
        parser.add_argument('--delay', type=int, default=5, help='Delay in seconds between retry attempts')
        
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
        
        return parser.parse_args()

    def setup_ssl(self):
        """Sets up the operational TLS streaming context for TCP."""
        cert_path = self.args.cert
        key_path = self.args.key

        if self.args.listen and (not cert_path or not key_path):
            logging.info("[*] Missing TLS credentials. Fabricating a self-signed identity...")
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
            logging.error(f"TLS Context layout failed: {e}")
            sys.exit(1)

    def generate_self_signed_cert(self):
        """Generates fallback self-signed TLS certificates for local testing using pyOpenSSL."""
        try:
            from OpenSSL import crypto
        except ImportError:
            logging.error("[!] pyOpenSSL not installed. Install with: pip install pyOpenSSL")
            sys.exit(1)

        cert_path = "/tmp/pyncat_cert.pem" if os.name != 'nt' else "pyncat_cert.pem"
        key_path = "/tmp/pyncat_key.pem" if os.name != 'nt' else "pyncat_key.pem"

        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 2048)
        cert = crypto.X509()
        cert.get_subject().CN = "pyncat"
        cert.set_serial_number(1001)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(365*24*60*60)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha256')

        with open(cert_path, "wb") as f: f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        with open(key_path, "wb") as f: f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k))
        return cert_path, key_path

    def execute_command(self, cmd: str) -> str:
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=15)
            return output.decode('utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            return "[!] Command execution timed out.\n"
        except Exception as e:
            return f"[!] Subprocess Error: {e}\n"

    def handle_tcp_client(self, client_socket, addr):
        logging.info(f"[+] TCP session active with client: {addr}")
        try:
            if self.args.execute:
                output = self.execute_command(self.args.execute)
                client_socket.sendall(output.encode())
                return

            if self.args.file and self.args.listen:
                with open(self.args.file, 'wb') as f:
                    while chunk := client_socket.recv(8192):
                        f.write(chunk)
                logging.info(f"[+] Streaming destination output completed: {self.args.file}")
                return

            while True:
                client_socket.sendall(b"pyncat> ")
                request = client_socket.recv(8192).decode('utf-8').strip()
                if not request or request.lower() in ['exit', 'quit']:
                    break
                output = self.execute_command(request)
                client_socket.sendall(output.encode())
        except Exception as e:
            logging.debug(f"TCP Worker exception: {e}")
        finally:
            client_socket.close()

    def listen(self):
        # PROFILE: UDP LISTENER
        if self.args.udp:
            server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server.bind(("0.0.0.0", self.args.port))
            logging.info(f"[*] Listening for UDP packets on 0.0.0.0:{self.args.port} {'[AES-GCM Encrypted]' if self.args.ssl else ''}")
            
            try:
                if self.args.output:
                    with open(self.args.output, "wb") as f:
                        while True:
                            data, addr = server.recvfrom(65507)
                            if self.args.ssl:
                                try: data = self.udp_crypto.decrypt(data)
                                except Exception: continue
                            if b"__EOF__" in data: break
                            f.write(data)
                    logging.info(f"[+] UDP file received and closed: {self.args.output}")
                else:
                    while True:
                        data, addr = server.recvfrom(65507)
                        if self.args.ssl:
                            try:
                                data = self.udp_crypto.decrypt(data)
                            except Exception as err:
                                logging.warning(f"[-] Dropped unauthenticated datagram from {addr}: {err}")
                                continue
                        logging.info(f"[{addr}]: {data.decode('utf-8', errors='replace').strip()}")
            except KeyboardInterrupt:
                pass
            finally:
                server.close()

        # PROFILE: TCP LISTENER
        else:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.args.port))
            server.listen(5)
            logging.info(f"[*] Listening for TCP connections on 0.0.0.0:{self.args.port} {'[TLS Secure]' if self.args.ssl else ''}")

            try:
                while True:
                    client, addr = server.accept()
                    if self.args.ssl:
                                            try:
                        client = self.ssl_context.wrap_socket(client, server_side=True)
                    except Exception as tls_err:
                        logging.error(f"[-] TLS Handshake failure with {addr}: {tls_err}")
                        client.close()
                        continue
                    
                    thread = threading.Thread(target=self.handle_tcp_client, args=(client, addr), daemon=True)
                    thread.start()
            except KeyboardInterrupt:
                logging.info("\n[!] Shutting down listener infrastructure.")
            finally:
                server.close()

    def connect(self):
        retry_count = 0
        max_retries = self.args.retry
        
        while True:
            try:
                # PROFILE: UDP CLIENT
                if self.args.udp:
                    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    logging.info(f"[*] Ready to transmit UDP targeted at {self.args.connect}:{self.args.port} {'[AES-GCM Secure]' if self.args.ssl else ''}")
                    
                    def send_packet(payload: bytes):
                        if self.args.ssl:
                            payload = self.udp_crypto.encrypt(payload)
                        client.sendto(payload, (self.args.connect, self.args.port))

                    if self.args.file and os.path.exists(self.args.file):
                        filesize = os.path.getsize(self.args.file)
                        chunk_size = 8100 if self.args.ssl else 8192
                        with open(self.args.file, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="UDP Burst") as pbar:
                            while chunk := f.read(chunk_size):
                                send_packet(chunk)
                                pbar.update(len(chunk))
                        send_packet(b"__EOF__")
                        return

                    while True:
                        user_input = sys.stdin.readline()
                        if not user_input:
                            break
                        send_packet(user_input.encode())
                    break

                # PROFILE: TCP CLIENT
                else:
                    raw_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    raw_client.settimeout(10)
                    logging.info(f"[*] Opening TCP route to {self.args.connect}:{self.args.port} (Attempt {retry_count + 1})")
                    
                    raw_client.connect((self.args.connect, self.args.port))
                    raw_client.settimeout(None)
                    
                    client = self.ssl_context.wrap_socket(raw_client, server_hostname=self.args.connect) if self.args.ssl else raw_client
                    logging.info("[+] Connection successfully brokered.")
                    retry_count = 0

                    if self.args.file and os.path.exists(self.args.file):
                        filesize = os.path.getsize(self.args.file)
                        with open(self.args.file, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="TLS Upload") as pbar:
                            while chunk := f.read(8192):
                                client.sendall(chunk)
                                pbar.update(len(chunk))
                        return

                    connection_alive = threading.Event()
                    connection_alive.set()

                    def receive_loop():
                        while True:
                            try:
                                data = client.recv(8192)
                                if not data:
                                    break
                                sys.stdout.write(data.decode('utf-8', errors='replace'))
                                sys.stdout.flush()
                            except Exception:
                                break
                        print("\n[!] Connection closed by remote host.")
                        connection_alive.clear()

                    threading.Thread(target=receive_loop, daemon=True).start()

                    while connection_alive.is_set():
                        # select loop prevents terminal read locks from freezing socket-drop awareness
                        ready, _, _ = select.select([sys.stdin], [], [], 1.0)
                        if ready:
                            user_input = sys.stdin.readline()
                            if not user_input:
                                break
                            client.sendall(user_input.encode())
                            if user_input.strip().lower() in ['exit', 'quit']:
                                return
                                
                    raise socket.error("Remote endpoint disconnected.")

            except (socket.error, ssl.SSLError) as connection_fault:
                logging.warning(f"[-] Connection failed or dropped: {connection_fault}")
                try:
                    client.close()
                except NameError:
                    pass
                
                if max_retries != -1 and retry_count >= max_retries:
                    logging.error("[!] Maximum retry threshold achieved. Terminating framework execution.")
                    break
                    
                retry_count += 1
                logging.info(f"[*] Sleeping for {self.args.delay} seconds before trying again...")
                try:
                    time.sleep(self.args.delay)
                except KeyboardInterrupt:
                    logging.info("[!] Reconnect runtime loop aborted by operator command.")
                    break
            finally:
                try:
                    client.close()
                except NameError:
                    pass

    def run(self):
        if self.args.listen:
            self.listen()
        elif self.args.connect:
            self.connect()


if __name__ == "__main__":
    PyNcat().run()
