# Prompt for your coding agent while building EMS (not embedded in the builder)

Copy the following into Codex in VS Code **or any other coding agent** with the EMS repository open. This is for developing the **builder and model library**, not for users of exported ZIPs.

> You are helping develop **Ecohydrological Model Studio (EMS)**, an agent-agnostic Streamlit builder that generates a downloadable ZIP containing a fully independent Streamlit ecosystem model project. Read `README.md`, `docs/ARCHITECTURE.md`, `docs/DATA_CATALOG.md`, `docs/DEVELOPMENT_ROADMAP.md`, `studio_builder/registry.py`, and the files in `template_generated_app/models/` before changing anything.
>
> The builder must **never** require a specific coding agent or LLM API key. Its main responsibilities are to let researchers select **predefined model and dataset blocks**, validate compatibility, export selected model files, and produce `model_config.json`, `MODEL_CARD.md`, `DATA_PROVENANCE.md`, tests, and `AGENT_BRIEF.md`. The generated app should run independently and the `AGENT_BRIEF.md` must be usable by any coding assistant.
>
> Start by improving the **scientific model library**: define input/output contracts and units for water balance, vegetation and soil-carbon modules, implement explicit daily state coupling, protect water and carbon mass balance, write tests, and distinguish research-validated equations from demonstration placeholders. Work incrementally, keep `pytest -q` passing, and do not claim an adapter works if it is merely cataloged.
>
> The dataset library includes Open-Meteo historical and climate APIs; SoilGrids REST API with numeric map-point and WCS recovery, then a local HWSD v2 backup; public/local SRDB; TerraClimate NCSS with MOD16A2GF v6.1 as backup; and local TRY categorical traits. Each now has an independent loader and fixture tests; live service availability is a separate check. Never redistribute TRY without checking its terms and preserving attribution. The supplied terms/docs were inspected for local ZIP bundling. Read docs/DATA_CATALOG.md for actual access requirements, units and known limits.

> Keep the builder model-first: automatically choose normal data, validation and backup blocks; expose only optional extras. Weather is Open-Meteo. Site and time frame belong in the exported app, whose step interval must respect model contracts and data resolution. The app must remain usable without a coding agent; optional modification instructions are saved to AGENT_BRIEF.md. The scientific direction is a Calabrese-based, configurable-depth water/soil/plant model rather than a literal two-layer port.
>
> For your first edit, propose a small plan, identify scientific simplifications, and implement **one** coherent improvement to model interfaces/validation with tests. Explain the files changed and how to run/verify them. Do not add agent SDKs, Cesium, drag-and-drop canvas, or automatic cloud deployment yet.
