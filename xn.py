#!/usr/bin/env python3
import argparse
import socket
import sys
import logging
import os
import hashlib
import time
import tqdm
import random

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


class PyNcatUDP:
    def __init__(self):
        self.args = self.parse_args()
        if self.args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        
        self.udp_crypto = None
        self.xor_engine = XORObfuscator(self.args.xor) if self.args.xor else None
        
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
            description="PyNcat - Dedicated Secure UDP Netcat (AES-GCM / XOR Obfuscation)", 
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-l', '--listen', action='store_true', help='Listen mode (Server)')
        group.add_argument('-c', '--connect', type=str, help='Connect to target destination IP (Client)')
        
        parser.add_argument('-p', '--port', type=int, required=True, help='UDP Port number')
        
        # Operation profiles
        parser.add_argument('-f', '--file', type=str, help='File to upload/transmit')
        parser.add_argument('-o', '--output', type=str, help='Save received data stream to file')
        
        # Security Layers
        parser.add_argument('--ssl', action='store_true', help='Enable cryptographic AES-GCM payload encryption')
        parser.add_argument('--key', type=str, help='UDP Pre-shared Key/Password passphrase for AES-GCM mode')
        parser.add_argument('--xor', type=str, help='Enable lightweight rolling multi-byte XOR obfuscation with specified key')
        
        # Add these to your argument block inside parse_args()
        parser.add_argument('--jitter', type=float, default=0.0, help='Jitter percentage as a decimal (e.g., 0.30 for 30%% variance)')
        parser.add_argument('--burst-delay', type=float, default=0.0, help='Inter-packet sleep delay in seconds during file bursts')

        # Add these configuration boundaries inside parse_args()
        parser.add_argument('--min-chunk', type=int, default=512, help='Minimum chunk size in bytes for file streaming profiles')
        parser.add_argument('--max-chunk', type=int, default=1400, help='Maximum chunk size in bytes to prevent MTU fragmentation splits')

        # Reconnect Settings (Client Target Tracking)
        parser.add_argument('--retry', type=int, default=0, help='Max connection retry attempts (-1 for infinite)')
        parser.add_argument('--delay', type=int, default=5, help='Delay in seconds between client transmission retry loops')
        
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose debug logging output')
        
        return parser.parse_args()

    def listen(self):
        """Dedicated UDP Listener Context."""
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("0.0.0.0", self.args.port))
        
        # Build context description strings for console awareness
        modes = []
        if self.args.ssl: modes.append("AES-GCM Encrypted")
        if self.args.xor: modes.append("XOR Obfuscated")
        mode_desc = f"[{' + '.join(modes)}]" if modes else "[Raw Data]"
        
        logging.info(f"[*] Listening for UDP datagrams on 0.0.0.0:{self.args.port} {mode_desc}")
        
        try:
            if self.args.output:
                with open(self.args.output, "wb") as f:
                    while True:
                        data, addr = server.recvfrom(65507)
                        if self.args.ssl and self.udp_crypto:
                            try:
                                data = self.udp_crypto.decrypt(data)
                            except Exception:
                                continue
                        if self.xor_engine:
                            data = self.xor_engine.process(data)
                        
                        if b"__EOF__" in data: 
                            break
                        f.write(data)
                logging.info(f"[+] UDP file stream received and closed: {self.args.output}")
            else:
                while True:
                    data, addr = server.recvfrom(65507)
                    if self.args.ssl and self.udp_crypto:
                        try:
                            data = self.udp_crypto.decrypt(data)
                        except Exception as err:
                            logging.warning(f"[-] Dropped unauthenticated datagram from {addr}: {err}")
                            continue
                    if self.xor_engine:
                        data = self.xor_engine.process(data)
                        
                    logging.info(f"[{addr[0]}:{addr[1]}]: {data.decode('utf-8', errors='replace').strip()}")
        except KeyboardInterrupt:
            logging.info("\n[!] Shutting down UDP listener instance.")
        finally:
            server.close()

    def connect(self):
        """Dedicated UDP Target Transmitter Context."""
        retry_count = 0
        max_retries = self.args.retry
        
        modes = []
        if self.args.ssl: modes.append("AES-GCM Secure")
        if self.args.xor: modes.append("XOR Masked")
        mode_desc = f"[{' + '.join(modes)}]" if modes else "[Raw Data]"

        while True:
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                logging.info(f"[*] Ready to transmit UDP targeted at {self.args.connect}:{self.args.port} {mode_desc}")
                
                def send_packet(payload: bytes):
                    if self.xor_engine:
                        payload = self.xor_engine.process(payload)
                    if self.args.ssl and self.udp_crypto:
                        payload = self.udp_crypto.encrypt(payload)
                    client.sendto(payload, (self.args.connect, self.args.port))

                # Handle File Streaming Strategy
                if self.args.file and os.path.exists(self.args.file):
                    filesize = os.path.getsize(self.args.file)
                    # Safe ceiling adjustments for UDP MTU/Fragmentation limits
                    chunk_size = 8000 if self.args.ssl else 8192
                    
                    with open(self.args.file, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="UDP Burst") as pbar:
                        while chunk := f.read(chunk_size):
                            send_packet(chunk)
                            pbar.update(len(chunk))
                    send_packet(b"__EOF__")
                    return

                # Inside the connect() method, under the File Streaming loop:
                if self.args.file and os.path.exists(self.args.file):
                    filesize = os.path.getsize(self.args.file)
                    chunk_size = 8000 if self.args.ssl else 8192
                    
                    with open(self.args.file, 'rb') as f, tqdm.tqdm(total=filesize, unit='B', unit_scale=True, desc="UDP Burst") as pbar:
                        while chunk := f.read(chunk_size):
                            send_packet(chunk)
                            pbar.update(len(chunk))
                            
                            # Break up packet-burst signatures using jittered delays
                            if self.args.burst_delay > 0:
                                base = self.args.burst_delay
                                variance = base * (self.args.jitter if self.args.jitter > 0 else 0.20) # 20% default jitter if unset
                                actual_burst_sleep = random.uniform(base - variance, base + variance)
                                time.sleep(max(0.001, actual_burst_sleep))
                                
                    send_packet(b"__EOF__")
                    return


                # Handle Interactive STDIN Strategy 
                logging.info("[*] Entering stream loop. Press Ctrl+C or send empty line to drop client execution.")
                while True:
                    user_input = sys.stdin.readline()
                    if not user_input or user_input.strip() == "":
                        break
                    send_packet(user_input.encode())
                break

                # Inside the connect() method, under the STDIN Interactive loop:
                logging.info("[*] Entering stream loop. Press Ctrl+C to drop client execution.")
                while True:
                    user_input = sys.stdin.readline()
                    if not user_input:
                        break
                    send_packet(user_input.encode())
                    
                    # Apply Jitter to standard transmissions if requested
                    if self.args.delay > 0 and self.args.jitter > 0:
                        base = self.args.delay
                        variance = base * self.args.jitter
                        actual_sleep = random.uniform(base - variance, base + variance)
                        actual_sleep = max(0.01, actual_sleep) # Ensure time is positive
                        time.sleep(actual_sleep)


            except socket.error as connection_fault:
                logging.warning(f"[-] Socket network fault experienced: {connection_fault}")
                try: client.close()
                except NameError: pass
                
                if max_retries != -1 and retry_count >= max_retries:
                    logging.error("[!] Maximum retry threshold achieved. Terminating framework execution.")
                    break
                    
                retry_count += 1
                logging.info(f"[*] Sleeping for {self.args.delay} seconds before reprocessing client connection...")
                try:
                    time.sleep(self.args.delay)
                except KeyboardInterrupt:
                    logging.info("[!] Reconnect tracking loop aborted by operator command.")
                    break
            finally:
                try: client.close()
                except NameError: pass

    def run(self):
        if self.args.listen:
            self.listen()
        elif self.args.connect:
            self.connect()


if __name__ == '__main__':
    PyNcatUDP().run()
