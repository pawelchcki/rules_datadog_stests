"""Run the local Agent backend; its port file is written after binding."""
import argparse
from pathlib import Path

from harness.datadog_backend.backend import API_KEY, BackendServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    with BackendServer(("127.0.0.1", args.port), args.output_dir, API_KEY) as server:
        Path(args.ready_file).write_text(str(server.server_port))
        server.serve_forever()


if __name__ == "__main__":
    main()
