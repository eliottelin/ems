"""Setup copy shared by the builder and its exported getting-started guide."""

PYTHON_SETUP = (
    "1. **Install Python if needed.** Python 3.14 is the tested and recommended version. Download it "
    "from [python.org](https://www.python.org/downloads/). The installer includes "
    "pip, which installs Streamlit and the other packages. Homebrew is optional. "
    "On Linux, install Python, pip and venv through your distribution's package manager."
)
EXTRACT_PROJECT = (
    "2. **Extract the ZIP.** Keep the project folder and its `data/` folder together."
)
OPEN_TERMINAL = (
    "3. **Open a terminal in the extracted project folder** containing `app.py` "
    "and `requirements.txt`. In VS Code, open that folder, then choose **Terminal → New Terminal**."
)
INSTALL_AND_RUN = (
    "4. **Install the packages and start the app.** Run these commands one at a time "
    "for your operating system. They create a project environment and install Streamlit automatically. "
    "For optional model changes, open this extracted folder in your coding assistant and ask it to read "
    "`AGENT_BRIEF.md` and implement the requested changes. A copyable example is provided with the download."
)
AGENT_HELP = (
    "For model changes, open the extracted project folder in your coding assistant, "
    "give it the exported AGENT_BRIEF.md, and ask it to implement those instructions."
)
AGENT_PROMPT = (
    "Read AGENT_BRIEF.md, MODEL_CARD.md and MODEL_INTERFACES.md in this project. "
    "Implement the model changes requested in AGENT_BRIEF.md, preserve water and carbon balances, "
    "keep the app runnable without a coding agent, and run the included tests. "
    "Explain your changes and scientific assumptions."
)
MACOS_COMMANDS = """python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py"""
WINDOWS_COMMANDS = r"""py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py"""
RUN_MODEL = (
    "5. **Run your model in the browser.** Open the local address printed in the terminal "
    "if it does not open automatically. Choose a site and dates, then select **Run ecosystem model**. "
    "If data are missing, use the recovery choices to find nearby data or retry. "
    "Only after mapped soil sources fail can you explicitly choose an assumed soil preset. "
    "The model updates daily. Internet is needed for package installation and API data. "
    "Keep the terminal open; press **Ctrl+C** there to stop the app."
)
HELP = (
    "Next time, open the same project folder in a terminal and run only the last command. "
    "On macOS, if installation asks for developer tools, run `xcode-select --install`, "
    "then retry. See the [Streamlit installation guide]"
    "(https://docs.streamlit.io/get-started/installation/command-line) for setup help."
)
CLOUD_SETUP = (
    "**Or run in the cloud — no local installation needed.** Extract the ZIP, "
    "upload the project files (including `requirements.txt` and permitted `data/` assets) "
    "to a GitHub repository, then sign in to [Streamlit Community Cloud](https://share.streamlit.io/). "
    "Choose **Create app**, select that repository and branch, set the entrypoint to `app.py` "
    "(or its path within the repository). In Advanced settings, select Python 3.14, then deploy. "
    "Community Cloud installs the packages automatically. Keep source dataset terms with the files. "
    "Downloading the optional large HWSD map requires your explicit choice, and it may need to download "
    "again after a cloud restart. "
    "[Deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)."
)


def getting_started_markdown():
    return "\n\n".join([
        "# After downloading: setup and run", PYTHON_SETUP, EXTRACT_PROJECT,
        OPEN_TERMINAL, INSTALL_AND_RUN, "### macOS / Linux",
        f"```bash\n{MACOS_COMMANDS}\n```", "### Windows PowerShell",
        f"```powershell\n{WINDOWS_COMMANDS}\n```", "Optional coding agent request:",
        f"```text\n{AGENT_PROMPT}\n```", RUN_MODEL, HELP, CLOUD_SETUP,
    ]) + "\n"
