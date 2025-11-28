# A3Dshell Web-Hosted Frontend

A Streamlit-based web service for A3Dshell Alpine3D simulations. This version is designed for server deployment with pre-installed binaries.

**Live:** https://a3dshell-721977282288.europe-west6.run.app

## Features

- **Switzerland Mode**: Automatic DEM download from Swisstopo APIs
- **Server-side Processing**: Data downloaded and processed on the server
- **Pre-installed Binaries**: Uses binaries configured by server administrator
- **Embedded Templates**: Configuration templates embedded in code with file override capability
- **INI Editors**: Edit Snowpack and Alpine3D configuration files in-browser

## Architecture

```
Browser (User Interface)
        │
        ▼ HTTP
Streamlit Server
├── gui_app.py (Web Interface)
├── src/ (Python Backend)
│   ├── DEM/LUS download & processing
│   ├── Cache management
│   └── Binary execution (subprocess)
        │
        ▼ subprocess calls
Pre-installed Binaries
├── snowpack
├── meteoio_timeseries
└── alpine3d
```

## Deployment

### Google Cloud Run (Recommended)

The easiest deployment option with **scale-to-zero** (no cost when idle).

**Prerequisites:**
1. Google Cloud account with billing enabled
2. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed

**Deploy:**

```bash
# One-time setup
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Deploy (first deployment takes ~10-15 mins for binary compilation)
gcloud run deploy a3dshell \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 10 \
  --memory 4Gi \
  --cpu 2 \
  --timeout 900 \
  --port 8501
```

**Configuration:**
| Setting | Value | Notes |
|---------|-------|-------|
| Region | europe-west1 | Belgium (supports custom domains) |
| Min instances | 0 | Scale to zero when idle |
| Max instances | 10 | Handle burst usage |
| Memory | 4 GB | Required for simulations |
| CPU | 2 vCPU | Good balance for performance |
| Timeout | 900s (15 min) | Max simulation duration |

**Estimated Cost:** $0-5/month (scale-to-zero means $0 when idle)

**Custom Domain (Optional):**

To use a custom domain like `a3dshell.ch`:

```bash
# Map your domain to the Cloud Run service
gcloud run domain-mappings create \
  --service a3dshell \
  --domain a3dshell.ch \
  --region europe-west6

# Follow the DNS instructions provided by gcloud
# Add the CNAME or A records to your domain registrar
```

**Auto-Deploy on Push (CI/CD):**

Set up automatic deployment when pushing to the `main` branch:

1. Go to [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers?project=a3dshell)
2. Click **Create Trigger** → **Connect Repository** → Select GitHub
3. Configure:
   - Name: `deploy-on-push`
   - Event: Push to branch `^main$`
   - Configuration: Cloud Build configuration file `/cloudbuild.yaml`
4. Click **Create**

Subsequent deployments take ~2-5 minutes (cached builds).

### Using Docker Compose (Self-Hosted)

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f
```

### Manual Installation

Server administrator must install:
1. MeteoIO, Snowpack, and Alpine3D binaries
2. Python 3.11+ with required packages
3. GDAL libraries

### Environment Variables

Configure binary paths via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SNOWPACK_BIN` | `snowpack` | Path to Snowpack binary |
| `METEOIO_BIN` | `meteoio_timeseries` | Path to MeteoIO binary |
| `ALPINE3D_BIN` | `alpine3d` | Path to Alpine3D binary |
| `A3D_CACHE_DIR` | `./cache` | Cache directory for downloads |
| `A3D_OUTPUT_DIR` | `./output` | Output directory |
| `A3D_TEMPLATE_DIR` | `./input/templates` | Template directory (for overrides) |

### Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set binary paths (if not in PATH)
export SNOWPACK_BIN=/usr/local/bin/snowpack
export ALPINE3D_BIN=/usr/local/bin/alpine3d

# Run Streamlit
streamlit run gui_app.py
```

## Configuration Templates

Templates are embedded in `src/templates/embedded.py`. To override:

1. Place custom template files in `input/templates/`
2. The system will use file overrides if present, otherwise embedded templates

Supported templates:
- `spConfig.ini` - Snowpack configuration
- `a3dConfig.ini` - Alpine3D configuration
- `a3dConfigComplex.ini` - Alpine3D with complex terrain radiation
- `template.sno` - Default snow profile
- `lus_*.sno` - Land use specific snow profiles
- `poi.smet` - Points of interest
- `template.pv` - PV panel configuration

## Differences from Main Repository

This web-hosted version differs from the main A3Dshell repository:

| Feature | Main (Docker) | Web-Hosted |
|---------|---------------|------------|
| Binary compilation | In Dockerfile | Pre-installed |
| Binary paths | User configurable | Server admin configured |
| Templates | External files | Embedded with override |
| Mode | Switzerland + Other Locations | Switzerland only |
| Data download | User or server | Server-side |

## License

See [LICENSE](LICENSE) file.
