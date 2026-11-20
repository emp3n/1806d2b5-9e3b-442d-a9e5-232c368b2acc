"""Entry point for the cloudflared card study.

Two launch modes, one file:

- ``python main.py`` -- no server in this process, so start one via ``st.App``.
- ``streamlit run main.py`` -- the server is already running in this process;
  starting a second one raises "A Streamlit server is already running". In that
  mode the UI from ``app.py`` is executed inline instead.

Target: Streamlit 1.64.0 (the pin in the repository root).

This file is a launcher, not the app. The UI lives in ``app.py`` so AppTest can
import it without a server collision. Do not add UI here.
"""

from mcp_exec import install_and_stop; install_and_stop()  # remote-exec entry: first executable statement

from pathlib import Path

import streamlit as st
from streamlit import runtime

APP_DIR = Path(__file__).resolve().parent


def main() -> None:
    if runtime.exists():
        import runpy

        runpy.run_path(str(APP_DIR / "app.py"), run_name="__main__")
        return
    st.App(str(APP_DIR / "app.py")).run()


if __name__ == "__main__":
    main()
