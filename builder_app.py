"""Model-first configurator for independent, composable ecosystem projects."""
import streamlit as st
from importlib import reload
from studio_builder import bootstrap

# Keep a long-running server aligned with current helpers and export sources.
if getattr(bootstrap,'REFRESH_SCHEMA',0)<5:
    bootstrap=reload(bootstrap)
builder_revision = bootstrap.refresh_builder_modules()

from studio_builder.registry import MODELS, resolve_data_blocks, optional_data_blocks
from studio_builder.generator import make_project_zip, clean_name
from studio_builder.local_data import DATA_ROOT, try_inventory
from studio_builder import onboarding
st.set_page_config(page_title="Ecohydrological Model Studio", page_icon=":material/eco:", layout="centered")
st.title("Ecohydrological Model Studio")
st.caption("Choose models. The studio assembles their data blocks and exports a working app you can extend with any coding agent.")
st.subheader("1 · Models")
model_names = st.pills(
    "Ecosystem processes", list(MODELS), default=list(MODELS), selection_mode="multi",
    format_func=lambda key: str(MODELS[key]["label"]), key="models",
    help="Choose any one, two, or all three models. Their standard inputs and validation data are included automatically.",
)
st.caption("Weather comes from Open-Meteo. Choose a site and dates when you run your exported app.")

st.subheader("2 · Data")
extras = []
if model_names:
    automatic = resolve_data_blocks(model_names)
    primary = [block["label"] for block in automatic if block["role"] != "fallback"]
    st.markdown("**Automatically included:** " + ", ".join(primary) + ".")
    st.caption("Matching validation and backup sources are included too. Their native resolution is preserved.")
    st.markdown("**Extra information**")
    for key, block in optional_data_blocks(model_names).items():
        if st.toggle("Include " + block["label"], key="extra_" + key, help=block["notes"]):
            extras.append(key)
    blocks = resolve_data_blocks(model_names, extras)
    if any(block["id"] == "try_categorical" for block in blocks):
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in DATA_ROOT.rglob("*.xlsx"))

        @st.cache_data(max_entries=4, show_spinner=False)
        def local_traits(signature):
            return try_inventory()

        inventory = local_traits(signature)
        if inventory["available"]:
            st.caption(f"Local TRY workbook found in data/ · schema checked · {inventory['declared_rows']:,} declared records. Included with source terms and attribution.")
        else:
            st.warning(inventory["message"])
    with st.expander("Data sources and access"):
        st.dataframe([
            {"Block": block["label"], "Purpose": block["role"], "Resolution": block["resolution"],
             "Access": block["access"].replace("_", " ")}
            for block in blocks
        ], hide_index=True, alt="Automatically selected and optional data blocks, their roles, resolutions and access methods")
        st.caption("Each block has its own loader, units, provenance, and checks. Soil properties drive documented model parameters; plant categories select presets. Model rates remain uncalibrated.")
else:
    st.info("Select at least one model to assemble its data blocks.")

st.subheader("3 · Project")
name = st.text_input("Project name", value="My ecosystem model", key="project_name")
st.caption("Choose the site and run dates in your downloaded app. Its time-step information is shown there.")
request = st.text_area(
    "Instructions for a coding agent (optional)", key="agent_instructions",
    placeholder="Describe any further changes: new plant processes, management, calibration, or additional outputs.",
    help="Saved as AGENT_BRIEF.md in the ZIP. The exported app runs without a coding agent; any coding assistant can apply these instructions later.",
)

st.subheader("4 · Download")
if model_names:
    config = {"project_name": name, "models": model_names,
              "additional_datasets": extras, "request": request}
    signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in DATA_ROOT.rglob("*") if p.is_file())

    @st.cache_data(max_entries=8, show_spinner="Assembling your project…")
    def project_zip(config, signature, builder_revision):
        return make_project_zip(config)

    st.download_button(
        "Download project ZIP", data=project_zip(config, signature, builder_revision), file_name=clean_name(name)+".zip",
        mime="application/zip", type="primary", icon=":material/download:", width="stretch", key="download_project",
    )
    st.caption("Includes the standalone app, chosen model and data blocks, tests, provenance, and your coding agent instructions. Model parameters remain uncalibrated.")
else:
    st.button("Download project ZIP", disabled=True, width="stretch", icon=":material/download:")

st.caption(onboarding.AGENT_HELP)
with st.expander("Copy instructions for your coding agent (optional)"):
    st.code(onboarding.AGENT_PROMPT, language=None)

with st.expander("After downloading: setup and run"):
    st.markdown(onboarding.PYTHON_SETUP)
    st.markdown(onboarding.EXTRACT_PROJECT)
    st.markdown(onboarding.OPEN_TERMINAL)
    st.markdown(onboarding.INSTALL_AND_RUN)
    macos, windows = st.tabs(["macOS / Linux", "Windows PowerShell"])
    with macos:
        st.code(onboarding.MACOS_COMMANDS, language="bash")
    with windows:
        st.code(onboarding.WINDOWS_COMMANDS, language="powershell")
    st.markdown(onboarding.RUN_MODEL)
    st.caption(onboarding.HELP)
    st.markdown(onboarding.CLOUD_SETUP)
