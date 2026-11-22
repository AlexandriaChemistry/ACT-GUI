#
# This source file is part of the Alexandria Chemistry Toolkit.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
"""!
@file
@brief The test of the installed conda package act-gui: the page and its
files are there, and the server computes the charges of water with the
alexandria program of the environment and the default force field.
"""
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

import act
from act_gui import __version__
from act_gui.app import TOKEN_HEADER, create_app
from act_gui.settings import Settings


def check(client: TestClient) -> None:
    """!
    @brief Ask the server what a user's browser asks.
    @param client The client of the server.
    """
    headers = {TOKEN_HEADER: "t"}
    page = client.get("/")
    assert page.status_code == 200, page.status_code
    assert 'data-theme="light"' in page.text, "the page has no screen mode"
    for path in ("/favicon.ico", "/static/css/app.css", "/static/js/app.js",
                 "/static/js/settings.js", "/static/vendor/3Dmol-min.js",
                 "/static/img/act-logo.png"):
        answer = client.get(path)
        assert answer.status_code == 200, "%s: %d" % (path, answer.status_code)
    session = client.get("/api/session", headers=headers).json()
    assert session["version"] == __version__, session
    assert session["mode"] == "desktop", session
    # A calculation with the real program and the default force field
    answer = client.post("/api/jobs", headers=headers,
                         json={"task": "charges", "molecule": {"smiles": "O"}})
    assert answer.status_code == 200, answer.text
    job_id = answer.json()["id"]
    end = time.time() + 300
    while time.time() < end:
        job = client.get("/api/jobs/" + job_id, headers=headers).json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert job["status"] == "finished", job
    formula = job["result"]["molecules"][0]["molecule"]["formula"]
    assert formula == "H2O", formula


def main() -> int:
    """!
    @brief Start the server in a temporary directory and check it.
    @return The exit status.
    """
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp) / "data", token="t",
                            config_file=Path(tmp) / "settings.json")
        app = create_app(settings)
        try:
            check(TestClient(app))
        finally:
            app.state.jobs.shutdown()
    print("act-gui %s works with %s" % (__version__, " ".join(act.config.executable_words())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
