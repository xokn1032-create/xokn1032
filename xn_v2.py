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
import tqdm

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


class XORObfuscator:
    """Implements dependency-free rolling multi-byte XOR masking for UDP payloads."""
    def __init__(self, key: str):
        self.key = key.encode('utf-8') if isinstance(key, str) else key
        self.key_len = len(self.key)

    def process(self, data: bytes) -> bytes:
        """XORs bytes against rotating pattern of the key (mask/unmask are identical)."""
        if not self.key_len:
            return data
        return bytes(b ^ self.key[i % self.key_len] for i, b in enumerate(data))


class PyNcatUDPEvader:
    def __init__(self):
        self.args = self.parse_args()
        if self.args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        self.udp_crypto = None
        self.xor_engine = XORObfuscator(self.args.xor) if self.args.xor else None
        
        # Validation rules for advanced evasion envelopes
        if self.args.pad_target > 0 and self.args.pad_target < 8:
            logging.error("[!] --pad-target must be at least 8 bytes to structurally support the size header.")
            sys.exit(1)

        # Security initialization routing
        if self.args.ssl:
            if not HAS_CRYPTOGRAPHY:
                logging.error("[!] Secure UDP requested, but 'cryptography' library is missing.")
                logging.error("Install it using: pip install cryptography")
                sys.exit(1)
            if not self.args.key:
                logging.error("[!] UDP encryption requires a passphrase via --key <passphrase>")
                sys.exit(1)
            logging.info("[*] Initializing AES-GCM Engine for Encrypted UDP mode.")
            self.udp_crypto = SecureUDPWrapper(self.args.key)

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description="PyNcat - Dedicated Secure UDP Netcat (Evasion Profile Edition)", 
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode (Server)')
        group.add_argument('-c', '--connect', type=str, help='Connect to target destination IP or Domain (Client)')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='UDP Port number')
        
        # Operation profiles
        parser.add_argument('-f', '--file', type=str, help='File to upload/transmit')
        parser.add_argument('-o', '--output', type=str, help='Save received data stream to file')
        
        # Security Layers
        parser.add_argument('--ssl', action='store_true', help='Enable cryptographic AES-GCM payload encryption')
        parser.add_argument('--key', type=str, help='UDP Pre-shared Key/Password passphrase for AES-GCM mode')
        parser.add_argument('--xor', type=str, help='Enable lightweight rolling multi-byte XOR obfuscation with specified key')
        
        # Timing Evasion (Jitter)
        parser.add_argument('--delay', type=float, default=0.0, help='Base delay in seconds between interactive transmissions')
        parser.add_argument('--burst-delay', type=float, default=0.0, help='Inter-packet sleep delay in seconds during file streaming bursts')
        parser.add_argument('--jitter', type=float, default=0.0, help='Jitter percentage as a decimal variance envelope (e.g., 0.30 for 30%%)')
        
        # Volumetric Evasion (Sizing & Padding)
        parser.add_argument('--min-chunk', type=int, default=512, help='Minimum chunk size in bytes for dynamic file streaming limits')
        parser.add_argument('--max-chunk', type=int, default=1400, help='Maximum chunk size in bytes to prevent MTU fragmentation splits')
        parser.add_argument('--pad-target', type=int, default=0, help='Pad all transmitted packets to a fixed size structure (0 to disable)')
        
        # Reconnect Settings 
        parser.add_argument('--retry', type=int, default=0, help='Max network socket recreation retry attempts (-1 for infinite)')
        
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose debug logging output')
        
        return parser.parse_args()

    def _apply_sleep(self, base_delay: float):
        """Calculates and enforces timing signatures using randomized bounds."""
        if base_delay <= 0:
            return
        if self.args.jitter > 0:
            variance = base_delay * self.args.jitter
            actual_sleep = random.uniform(base_delay - variance, base_delay + variance)
            time.sleep(max(0.001, actual_sleep))
        else:
            time.sleep(base_delay)

    def listen(self):
        """Dedicated UDP Listener Context with Structural Unpacking Engine."""
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("0.0.0.0", self.args.port))
        
        modes = []
        if self.args.ssl: modes.append("AES-GCM Encrypted")
        if self.args.xor: modes.append("XOR Obfuscated")
        if self.args.pad_target > 0: modes.append(f"Fixed Padding [{self.args.pad_target}B]")
        mode_desc = f"[{' + '.join(modes)}]" if modes else "[Raw Data]"
        
        logging.info(f"[*] Listening for UDP datagrams on 0.0.0.0:{self.args.port} {mode_desc}")
        
        output_file_handle = None
        if self.args.output:
            output_file_handle = open(self.args.output, "wb")

        try:
            while True:
                data, addr = server.recvfrom(65507)
                
                # 1. Reverse Cryptographic Envelopes
                if self.args.ssl and self.udp_crypto:
                    try:
                        data = self.udp_crypto.decrypt(data)
                    except Exception as err:
                        logging.debug(f"[-] Decryption dropped corrupted packet from {addr}: {err}")
                        continue
                
                # 2. Drop Obfuscation Masks
                if self.xor_engine:
                    data = self.xor_engine.process(data)
                
                # 3. Strip Length-Prefixed Padding Layers
                if self.args.pad_target > 0:
                    if len(data) >= 4:
                        actual_len = struct.unpack('!I', data[:4])[0]
                        data = data[4:4 + actual_len]
                    else:
                        logging.warning(f"[-] Dropped malformed packet from {addr} (too short for padding envelope header)")
                        continue

                # 4. Stream Evaluation
                if b"__EOF__" in data:
                    if output_file_handle:
                        logging.info("[+] Terminal end-of-file validation signature reached.")
                        break
                    continue
                
                # 5. Output Management 
                if output_file_handle:
                    output_file_handle.write(data)
                else:
                    logging.info(f"[{addr[0]}:{addr[1]}]: {data.decode('utf-8', errors='replace').strip()}")
                    
        except KeyboardInterrupt:
            logging.info("\n[!] Shutting down UDP listener processing.")
        finally:
            if output_file_handle:
                output_file_handle.close()
                logging.info(f"[+] UDP file stream received and written successfully to: {self.args.output}")
            server.close()

    def connect(self):
        """Dedicated UDP Transmitter with Injection Evasion Handlers."""
        retry_count = 0
        max_retries = self.args.retry
        
        modes = []
        if self.args.ssl: modes.append("AES-GCM Secure")
        if self.args.xor: modes.append("XOR Masked")
        if self.args.pad_target > 0: modes.append(f"Fixed Size Target [{self.args.pad_target}B]")
        mode_desc = f"[{' + '.join(modes)}]" if modes else "[Raw Data]"

        while True:
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                logging.info(f"[*] Ready to transmit UDP targeted at {self.args.connect}:{self.args.port} {mode_desc}")
                
                def send_packet(payload: bytes):
                    # 1. Inject Uniform Padding if config is active
                    if self.args.pad_target > 0:
                        payload_len = len(payload)
                        if payload_len + 4 > self.args.pad_target:
                                                    logging.warning(f"[!] Payload block ({payload_len}B) exceeds targeted pad limit ({self.args.pad_target}B). Stripping structure bounds.")
                    else:
                        header = struct.pack('!I', payload_len)
                        padding_needed = self.args.pad_target - (payload_len + 4)
                        payload = header + payload + os.urandom(padding_needed)
                
                # 2. Inject Obfuscation Layer
                if self.xor_engine:
                    payload = self.xor_engine.process(payload)
                    
                # 3. Inject Cryptographic Armor
                if self.args.ssl and self.udp_crypto:
                    payload = self.udp_crypto.encrypt(payload)
                    
                client.sendto(payload, (self.args.connect, self.args.port))

            # STRATEGY A: EVASIVE FILE TRANSFERS
            if self.args.file and os.path.exists(self.args.file):
                filesize = os.path.getsize(self.args.file)
                min_c = max(16, self.args.min_chunk)
                max_c = min(1400, self.args.max_chunk) if self.args.ssl else min(1450, self.args.max_chunk)
                if min_c > max_c:
                    min_c = max_c
                    
                # Ensure structural padding boundaries do not collide with selected ceilings
                if self.args.pad_target > 0 and max_c + 4 > self.args.pad_target:
                    max_c = self.args.pad_target - 4
                    if min_c > max_c: 
                        min_c = max_c
                        
                with open(self.args.file, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="Evasive Upload") as pbar:
                    while True:
                        current_chunk_size = random.randint(min_c, max_c)
                        chunk = f.read(current_chunk_size)
                        if not chunk:
                            break
                        send_packet(chunk)
                        pbar.update(len(chunk))
                        self._apply_sleep(self.args.burst_delay)
                send_packet(b"__EOF__")
                return

            # STRATEGY B: INTERACTIVE STDIN TRANSMISSIONS
            logging.info("[*] Entering interactive loop. Press Enter/Ctrl+C to break client routing.")
            while True:
                user_input = sys.stdin.readline()
                if not user_input or user_input.strip() == "":
                    break
                send_packet(user_input.encode())
                self._apply_sleep(self.args.delay)
            break

        except socket.error as connection_fault:
            logging.warning(f"[-] Socket network fault experienced: {connection_fault}")
            try: 
                client.close()
            except NameError: 
                pass
                
            if max_retries != -1 and retry_count >= max_retries:
                logging.error("[!] Maximum retry threshold achieved. Terminating framework execution.")
                break
                
            retry_count += 1
            logging.info(f"[*] Sleeping for {self.args.delay if self.args.delay > 0 else 5} seconds before reprocessing network interfaces...")
            try:
                time.sleep(self.args.delay if self.args.delay > 0 else 5)
            except KeyboardInterrupt:
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


if __name__ == '__main__':
    engine = PyNcatUDPEvader()
    engine.run()
