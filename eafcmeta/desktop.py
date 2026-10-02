"""Finestra nativa (senza browser) con pywebview: avvia il server su una porta libera e lo mostra in una finestra."""
import socket
import threading
import time


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(open_window=None) -> bool:
    """Ritorna False se la finestra nativa non è disponibile (pywebview assente o errore all'avvio)."""
    if open_window is None:
        try:
            import webview
        except ImportError:
            return False

        def open_window(url):
            webview.create_window("EA FC Meta", url, width=1280, height=840, min_size=(440, 620),
                                  background_color="#080b14")
            webview.start()

    import uvicorn

    port = free_port()
    server = uvicorn.Server(uvicorn.Config("eafcmeta.api:app", host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        return False
    try:
        open_window(f"http://127.0.0.1:{port}/")
    except Exception as e:  # noqa: BLE001 - qualunque problema della GUI -> ripiego sul browser
        print(f"[app] impossibile aprire la finestra: {e}")
        server.should_exit = True
        thread.join(5)
        return False
    server.should_exit = True
    thread.join(5)
    return True
