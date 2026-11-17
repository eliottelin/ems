# Deploy the EMS builder on Streamlit Community Cloud

The requested deployment is the **builder**, in a private GitHub repository named
`ems`. Its entrypoint is **`builder_app.py`**. The generated ecosystem model is a
separate app whose entrypoint is `app.py` after its ZIP is extracted; the template
directory alone is not a runnable model because the builder creates its
`model_config.json` during export.

## Repository contents

Keep these source files and directories together at the repository root:

- `builder_app.py`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`.
- `studio_builder/`, `template_generated_app/`, `tests/` and project documentation.
- `.streamlit/config.toml` and `.gitignore`.
- `data/hwsd/HWSD2_LAYERS.csv.gz` and `data/hwsd/source.json`.
- The supplied TRY categorical package under
  `data/Try2026119174542TRY_Categorical_Traits_Lookup_Table_2012_03_17_TestRelease/`,
  including its original workbook, documentation, terms PDF and derived index.

The builder uses the compact HWSD table directly when the original Access
database is absent. Do not upload `data/private/HWSD2.mdb`, local virtual
environments, exported ZIPs, downloaded BIL/HDR maps, runtime caches, backups,
secrets or developer credentials. The geographic HWSD raster is approximately
1.9 GB uncompressed and is not needed to run the builder or create project ZIPs.

Preserve the supplied TRY terms and citations. Its supplied file-archive terms
describe unrestricted downloads subject to individual dataset restrictions and
require citation of the publication and data package. The supplied categorical
documentation does not add a separate restriction. This private deployment
preserves the package bytes and attribution; review the source terms again
before changing the repository or application to a public distribution.

The code does not require API credentials for the included public soil/weather
services. If credentials are added later, put them in Community Cloud's Secrets
settings, never in tracked source files.

## Community Cloud settings

1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) using the
   GitHub account that owns or administers the private `ems` repository.
2. Choose **Create app**, select the repository and `main` branch, and enter
   **`builder_app.py`** as the main file path.
3. Open **Advanced settings** and select **Python 3.14**, matching the locally
   verified Python 3.14 environment. Community Cloud's documented default is
   currently 3.12, so choose the version explicitly.
4. Deploy and inspect the build logs. The root `requirements.txt` installs the
   app dependencies. No `packages.txt` or system-level Access/ODBC tools are
   required by the current source loaders.
5. On the first page, confirm the three model choices and project download work.
   Extract one downloaded project and run its included tests before distributing
   a customized model.

Private repositories require the appropriate GitHub authorization for Streamlit
and repository admin access to deploy. The Cloud connection is separate from
this assistant's GitHub connection. See the official
[deployment instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy),
[dependency guidance](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
and [private-repository requirements](https://docs.streamlit.io/deploy/streamlit-community-cloud/status).

## Local verification

From the repository root, install development dependencies and run:

```bash
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q
python -m streamlit run builder_app.py
```

The deployment-readiness regression creates an isolated copy containing source
and permitted assets, omits the original MDB and local Downloads folders, runs
the builder and verifies its exported project. Export tests verify that
`.gitignore` and the allowed Streamlit configuration are included while secrets,
runtime caches and large private/source assets are excluded. Scientific tests
check units, input validation and mass balance; passing them does not establish
ecological calibration or predictive accuracy.

## Generated models in the cloud

If a generated model is deployed separately, use that extracted project's
`app.py`, requirements and data assets. Model sources retain their selected
spatial support and recorded provenance. Mapped source failures never become
invented soil observations. A climate scenario must reuse a valid loaded soil
profile or an explicitly accepted assumed profile; loading climate forcing alone
does not authorize a soil preset.

SoilGrids REST is checked automatically when enabled in the generated project;
failed service calls use a cooldown and retry after it expires. A service that
returns data is still required to pass complete site, units, depth and physical
checks. WMS/WCS remain available when REST is unavailable. A missing HWSD raster
requires an explicit download action and may take several minutes.

Local cache files and downloaded rasters are optimizations, not durable cloud
storage. Community Cloud does not guarantee persistence of runtime-created files;
a restart or rebuild may require a fresh lookup or download. Keep the HWSD
download opt-in and account for its disk/network cost. The raster is streamed to
disk and sampled locally rather than loaded entirely into RAM. Check the app's
actual resource limits in its Cloud workspace; they can vary over time. See
[storage persistence guidance](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)
and [resource guidance](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app).

## Completion criteria

Before calling the deployment complete, verify the remote `main` commit contains
the intended source and assets, the repository is private, Cloud selects
`builder_app.py` with Python 3.14, the Cloud build succeeds, and a generated ZIP
opens and tests independently. A prepared repository and a successful local run
are distinct from a verified Cloud deployment.
