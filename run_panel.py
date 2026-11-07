"""Start the ADC demo panel:  python run_panel.py   (then open http://127.0.0.1:8765 if it does not open by itself)"""
import sys
import threading
import webbrowser

from panel.server import serve

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"ADC demo panel on http://127.0.0.1:{port}   (Ctrl+C to stop)")
    serve(port)
