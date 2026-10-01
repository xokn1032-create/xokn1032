#!/usr/bin/env python3
import redis
import sys
import json
import time

def start_central_aggregator(redis_host='127.0.0.1', redis_port=6379):
    """
    Subscribes to a shared cache broker to unify and reassemble 
    scattered payload fragments sent from multi-homed receivers.
    """
    print(f"[*] Initializing Centralized Collector Pipeline on Redis [{redis_host}:{redis_port}]")
    r = redis.Redis(host=redis_host, port=redis_port, db=0)
    pubsub = r.pubsub()
    pubsub.subscribe('pyncat_c2_mesh')

    print("[+] Aggregator Active. Awaiting decentralized traffic components...\n")
    
    # Track the last active source to format the terminal output cleanly
    last_seen_source = None

    try:
        for message in pubsub.listen():
            if message['type'] == 'message':
                # Decode the serialized JSON fragment container
                data_packet = json.loads(message['data'].decode('utf-8'))
                
                source_node = data_packet['source']
                payload_text = data_packet['payload']
                timestamp = data_packet['timestamp']

                # Print header if the packet comes from a new or different source machine
                if source_node != last_seen_source:
                    print(f"\n\n[{timestamp}] --- Node Update: {source_node} ---")
                    last_seen_source = source_node

                # Output the reassembled fragment block directly to the operator console
                sys.stdout.write(payload_text)
                sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n[!] Shutting down log aggregation pipeline.")

if __name__ == '__main__':
    # Run this on your primary tracking display machine
    start_central_aggregator()
