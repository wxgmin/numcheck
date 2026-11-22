"""Launch the portable NumCheck desktop distribution."""
import argparse
import webbrowser

from werkzeug.serving import make_server

from numcheck.web.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    app = create_app()
    app.config["TRUSTED_HOSTS"] = ["127.0.0.1"]
    with make_server("127.0.0.1", args.port, app, threaded=True) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"NumCheck is ready at {url}", flush=True)
        print("Keep this window open. Press Ctrl+C to exit.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        server.serve_forever()


if __name__ == "__main__":
    main()
