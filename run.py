"""Inicia o Controle de Férias.

Uso:
    python run.py            servidor de produção (waitress), só neste computador
    python run.py --rede     libera o acesso para outros computadores da rede
    python run.py --debug    servidor de desenvolvimento com recarga automática
"""

import os
import socket
import sys

from app import create_app

app = create_app()


def _ip_local() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    na_rede = "--rede" in sys.argv
    host = "0.0.0.0" if na_rede else os.environ.get("HOST", "127.0.0.1")

    if "--debug" in sys.argv:
        app.run(host=host, port=porta, debug=True)
    else:
        from waitress import serve

        print(f"Controle de Férias rodando em http://127.0.0.1:{porta}", flush=True)
        if host == "0.0.0.0":
            print(f"Acesso pela rede: http://{_ip_local()}:{porta}", flush=True)
        print("Pressione Ctrl+C para encerrar.", flush=True)
        serve(app, host=host, port=porta)
