pip install python3-dtls

def setup_ssl(self):
    if self.args.udp:
        try:
            from dtls import do_patch
            do_patch()  # Patches Python's ssl module to support DTLS over UDP
            logging.info("[*] DTLS enabled for UDP security.")
        except ImportError:
            logging.error("[!] python3-dtls package not found. Run: pip install python3-dtls")
            sys.exit(1)

    # The rest of your certificate and SSLContext setup logic remains identical!

