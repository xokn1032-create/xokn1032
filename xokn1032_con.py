#!/usr/bin/env python3
import argparse
import socket

class PyNcat:
    def __init__(self):
        self.args = self.parse_args()
        if self.args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)

    def parse_args(self):
        parser = argparse.ArgumentParser(description="PyNcat", 
                                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-c', '--connect', type=str, help='Connect to target (reverse shell)')
        parser.add_argument('-v', '--verbose', action='store_true')
        
        return parser.parse_args()

    # === NETWORK RUNTIME ===
    def start(self):
        retries = 0
        while True:
            try:
                if  self.args.connect:
                    self.run_connector()
                    if not self.args.persistent:
                        break
            except KeyboardInterrupt:
                logging.info("\n[*] Exiting by user request.")
                break
            except Exception as e:
                logging.error(f"Runtime error encountered: {e}")

if __name__ == "__main__":
    netcat = PyNcat()
    netcat.start()
