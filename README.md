# A3Dshell Web

A web-based tool for preparing [Alpine3D](https://alpine3d.slf.ch) simulation environments.

## What is Alpine3D?

[Alpine3D](https://alpine3d.slf.ch) is a spatially distributed model developed by [SLF](https://www.slf.ch) that simulates snow-dominated surface processes in mountainous terrain. It combines the SNOWPACK model with modules for radiation transfer, snow transport, and runoff modeling. Alpine3D is used for:

- Water resource assessment in mountain watersheds
- Climate change impact studies
- Avalanche forecasting and ski slope management

## What does A3Dshell do?

A3Dshell automates the preparation of Alpine3D simulation inputs:

- Downloads and processes DEM data from Swisstopo
- Generates land use grids from Swiss topographic data
- Selects appropriate IMIS meteorological stations
- Packages everything into a ready-to-use simulation folder

## Demo

<p align="center">
  <img src="docs/images/demo.gif" alt="A3Dshell Demo" width="900"/>
</p>

## Quick Start

1. **Select your region** on the interactive map (Switzerland)
2. **Configure parameters** - simulation period, grid resolution, land cover
3. **Set a point of interest** for detailed output
4. **Run the setup** to generate your simulation package
5. **Download the ZIP** containing all Alpine3D input files

## Workflow Tabs

| Tab | Description |
|-----|-------------|
| 1. General | Simulation name, date range, coordinate system |
| 2. ROI/DEM | Define region of interest, DEM resolution |
| 3. POI | Set point of interest coordinates |
| 4. Landcover | Choose land cover source and settings |
| 5. Meteo | IMIS station selection (automatic) |
| 6. Run Config | Execute setup and download results |

## Output Structure

After running the setup, you'll get a ZIP file containing:

```
your_simulation/
├── input/
│   ├── surface-grids/    # DEM, land use grids
│   ├── meteo/            # SMET meteorological files
│   ├── snowfiles/        # Initial snow profiles
│   └── ...
├── output/               # Ready for A3D execution
├── io.ini                # Alpine3D configuration
└── ...
```

## Run Locally with Docker

```bash
# Clone and build
git clone https://github.com/frischwood/A3Dshell.git
cd A3Dshell
docker-compose up --build

# Open browser to http://localhost:8501
```

The Docker image includes MeteoIO and Snowpack binaries. Output files are saved to `./output/`.

**Local-only features** (enabled via docker-compose environment variables):
- **IMIS/Snowpack preprocessing** - Requires SLF VPN access to the IMIS database
- **Run A3D tab** - Execute Alpine3D simulations directly

These features are disabled in the hosted web version. Running `streamlit run gui_app.py` directly won't enable them - use `docker-compose up` instead.

## License

MIT License - see [LICENSE](LICENSE) file.
