"""
A3Dshell- Simple GUI
====================================

A Streamlit-based GUI for configuring and running A3Dshell simulations.

Run with: streamlit run gui_app.py
"""

import streamlit as st
import subprocess
import json
from pathlib import Path
from datetime import datetime, timedelta
import configparser
import os
import folium
from streamlit_folium import st_folium
from folium.plugins import Draw
import geopandas as gpd
from shapely.geometry import shape
from pyproj import Transformer

# Import environment variable configuration for binary paths
from src.config import (
    get_snowpack_bin, get_meteoio_bin, get_alpine3d_bin,
    get_cache_dir, get_output_dir, get_template_dir
)

# Import embedded templates for web-hosted version
from src.templates import get_template

# Page configuration
st.set_page_config(
    page_title="A3Dshell Simulation Setup",
    layout="wide"
)

# Helper functions
def get_build_info():
    """
    Read and parse BUILD_INFO.txt if it exists (Docker environment).

    Returns:
        dict: Build information or None if not found
    """
    build_info_path = Path("BUILD_INFO.txt")
    if build_info_path.exists():
        try:
            with open(build_info_path, 'r') as f:
                content = f.read()
            return content
        except Exception as e:
            return None
    return None

def find_shapefiles(base_dir):
    """
    Recursively find all .shp files in a directory.

    Args:
        base_dir: Base directory to search (str or Path)

    Returns:
        list: List of Path objects for found shapefiles, empty list if none found or dir invalid
    """
    try:
        base_path = Path(base_dir)
        if not base_path.exists() or not base_path.is_dir():
            return []

        # Find all .shp files recursively
        shapefiles = sorted(base_path.rglob("*.shp"))
        return shapefiles
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_swiss_boundary_polygon():
    """
    Fetch or create Swiss boundary polygon for validation.

    First attempts to fetch from Swisstopo REST API.
    Falls back to simplified boundary polygon if API unavailable.

    Returns:
        shapely.Polygon: Swiss boundary in EPSG:2056, or None if all methods fail
    """
    try:
        import requests
        from shapely.geometry import Polygon

        # Try Swisstopo REST API for height service (includes boundary query)
        # Alternative: Use a pre-simplified boundary polygon
        url = "https://api3.geo.admin.ch/rest/services/api/MapServer/identify?geometry=2660000,1185000&geometryType=esriGeometryPoint&layers=all:ch.swisstopo.swissboundaries3d-land-flaeche.fill&returnGeometry=true&sr=2056"

        response = requests.get(url, timeout=5)

        if response.status_code == 200:
            data = response.json()
            if 'results' in data and len(data['results']) > 0:
                # Extract geometry if available
                for result in data['results']:
                    if 'geometry' in result and 'rings' in result['geometry']:
                        rings = result['geometry']['rings']
                        if rings and len(rings) > 0:
                            # Create polygon from rings
                            from shapely.geometry import Polygon
                            polygon = Polygon(rings[0])
                            return polygon

        # Fallback: Use simplified Swiss boundary (approximate)
        # This is a generalized version for validation purposes
        # Coordinates in EPSG:2056 (Swiss LV95)
        simplified_coords = [
            (2485000, 1075000),  # SW corner
            (2485000, 1110000),
            (2490000, 1145000),  # West (Geneva area)
            (2495000, 1185000),
            (2510000, 1230000),
            (2525000, 1265000),  # NW
            (2570000, 1295000),  # North
            (2630000, 1296000),
            (2720000, 1295000),  # NE (Rhine valley)
            (2795000, 1280000),
            (2834000, 1255000),  # East (Grisons)
            (2830000, 1220000),
            (2815000, 1185000),
            (2785000, 1150000),  # SE
            (2750000, 1110000),
            (2715000, 1085000),  # Ticino
            (2680000, 1080000),
            (2630000, 1085000),
            (2580000, 1095000),
            (2530000, 1085000),
            (2490000, 1078000),  # South
            (2485000, 1075000),  # Close polygon
        ]

        polygon = Polygon(simplified_coords)
        return polygon

    except Exception:
        return None

def check_swiss_boundaries(x, y, roi_size=None):
    """
    Check if coordinates (and optional ROI) are within Swiss boundaries (EPSG:2056).

    Uses official Swiss boundary polygon from Swisstopo.

    Args:
        x: Easting coordinate (EPSG:2056)
        y: Northing coordinate (EPSG:2056)
        roi_size: Optional ROI size in meters (for bounding box check)

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    try:
        from shapely.geometry import Point, box

        # Get Swiss boundary polygon
        swiss_boundary = get_swiss_boundary_polygon()

        if swiss_boundary is None:
            # Fallback to bounding box check if API fails
            SWISS_MIN_E = 2485000
            SWISS_MAX_E = 2834000
            SWISS_MIN_N = 1075000
            SWISS_MAX_N = 1296000

            if not (SWISS_MIN_E <= x <= SWISS_MAX_E and SWISS_MIN_N <= y <= SWISS_MAX_N):
                return False, f"⚠️ Point ({x:.0f}, {y:.0f}) appears outside Swiss boundaries"

            if roi_size:
                half_size = roi_size / 2
                min_x, max_x = x - half_size, x + half_size
                min_y, max_y = y - half_size, y + half_size

                if not (SWISS_MIN_E <= min_x and max_x <= SWISS_MAX_E and
                       SWISS_MIN_N <= min_y and max_y <= SWISS_MAX_N):
                    return False, f"⚠️ ROI extends outside Swiss boundaries. Reduce ROI size or move center point."

            return True, "✅ Within Swiss boundaries"

        # Use actual Swiss boundary polygon
        point = Point(x, y)

        # Check if point is within Switzerland
        if not swiss_boundary.contains(point):
            return False, f"⚠️ Point ({x:.0f}, {y:.0f}) is outside Switzerland"

        # If ROI size provided, check if entire bounding box fits within Switzerland
        if roi_size:
            half_size = roi_size / 2
            roi_box = box(x - half_size, y - half_size, x + half_size, y + half_size)

            # Check if ROI box is fully within Switzerland
            if not swiss_boundary.contains(roi_box):
                return False, f"⚠️ ROI extends outside Switzerland. Reduce ROI size or move center point."

        return True, "✅ Within Switzerland"

    except Exception:
        # If anything fails, allow it (better than blocking)
        return True, "⚠️ Could not validate boundaries"

def check_polygon_in_swiss_boundaries(geojson_geometry):
    """
    Check if a drawn polygon is within Swiss boundaries.

    Uses official Swiss boundary polygon from Swisstopo.

    Args:
        geojson_geometry: GeoJSON geometry object (in WGS84)

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    try:
        from shapely.geometry import shape as shapely_shape
        import geopandas as gpd

        # Convert GeoJSON to shapely geometry (WGS84)
        geom_wgs84 = shapely_shape(geojson_geometry)

        # Transform to Swiss LV95
        gdf = gpd.GeoDataFrame([{'geometry': geom_wgs84}], crs='EPSG:4326')
        gdf_lv95 = gdf.to_crs('EPSG:2056')
        geom_lv95 = gdf_lv95.geometry.iloc[0]

        # Get Swiss boundary polygon
        swiss_boundary = get_swiss_boundary_polygon()

        if swiss_boundary is None:
            # Fallback to bounding box check if polygon unavailable
            SWISS_MIN_E = 2485000
            SWISS_MAX_E = 2834000
            SWISS_MIN_N = 1075000
            SWISS_MAX_N = 1296000

            minx, miny, maxx, maxy = geom_lv95.bounds

            if minx < SWISS_MIN_E or maxx > SWISS_MAX_E:
                return False, f"⚠️ Drawn ROI extends outside Swiss boundaries (East-West). Please redraw within Switzerland."

            if miny < SWISS_MIN_N or maxy > SWISS_MAX_N:
                return False, f"⚠️ Drawn ROI extends outside Swiss boundaries (North-South). Please redraw within Switzerland."

            return True, "✅ ROI within Swiss boundaries (bounding box check)"

        # Use actual Swiss boundary polygon
        # Check if drawn geometry is fully within Switzerland
        # Using .contains() checks if the ENTIRE geometry (including edges) is within the boundary
        if not swiss_boundary.contains(geom_lv95):
            # Additional check: does the drawn polygon intersect but not fully contained?
            if swiss_boundary.intersects(geom_lv95):
                return False, f"⚠️ Drawn ROI crosses Swiss border. Part of the ROI is outside Switzerland. Please redraw within Swiss borders."
            else:
                return False, f"⚠️ Drawn ROI is completely outside Switzerland. Please redraw within Swiss borders."

        return True, "✅ ROI within Switzerland (boundary polygon check)"

    except Exception as e:
        # Log the error for debugging
        import streamlit as st
        st.warning(f"⚠️ Boundary validation error: {str(e)}")
        # Fail safe: reject if validation fails
        return False, f"❌ Could not validate boundaries (error: {str(e)}). Please check your ROI."

def create_roi_map(center_lat=46.8, center_lon=8.2, zoom=8):
    """
    Create an interactive map with Swisstopo layers for drawing ROI polygons.

    Args:
        center_lat: Latitude for map center (WGS84)
        center_lon: Longitude for map center (WGS84)
        zoom: Initial zoom level

    Returns:
        folium.Map object with drawing tools
    """
    # Create base map centered on Switzerland
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles=None  # We'll add custom tiles
    )

    # Add Swisstopo base layer (Swiss National Map)
    folium.raster_layers.WmsTileLayer(
        url='https://wms.geo.admin.ch/',
        layers='ch.swisstopo.pixelkarte-farbe',
        fmt='image/png',
        transparent=False,
        name='Swisstopo Map',
        overlay=False,
        control=True,
        attr='© swisstopo'
    ).add_to(m)

    # Add drawing tools (rectangle and polygon)
    draw = Draw(
        export=False,
        draw_options={
            'polyline': False,
            'rectangle': True,
            'circle': False,
            'marker': False,
            'circlemarker': False,
            'polygon': True
        },
        edit_options={
            'edit': True,
            'remove': True
        }
    )
    draw.add_to(m)

    return m

def save_drawn_roi(geojson_data, output_path):
    """
    Save drawn polygon as shapefile in Swiss coordinate system (EPSG:2056).

    Args:
        geojson_data: GeoJSON dict from drawn polygon
        output_path: Path to save shapefile

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Extract geometry from GeoJSON
        geom = shape(geojson_data['geometry'])

        # Create GeoDataFrame in WGS84
        gdf = gpd.GeoDataFrame([{'id': 1}], geometry=[geom], crs='EPSG:4326')

        # Transform to Swiss coordinate system (LV95)
        gdf = gdf.to_crs('EPSG:2056')

        # Ensure output directory exists
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save as shapefile
        gdf.to_file(output_path)

        return True, f"✅ ROI saved successfully to {output_path}"

    except Exception as e:
        return False, f"❌ Error saving ROI: {str(e)}"


# Title
st.title("A3Dshell")
st.markdown("Configure Alpine3D simulation setups")

# Sidebar for existing configs
st.sidebar.header("Load Existing Config")
config_dir = Path("config")
existing_configs = list(config_dir.glob("*.ini"))
config_names = ["Create New"] + [c.name for c in existing_configs]

selected_config = st.sidebar.selectbox(
    "Select configuration:",
    config_names
)

# Build Info section in sidebar
st.sidebar.divider()
st.sidebar.header("About")

build_info = get_build_info()
if build_info:
    with st.sidebar.expander("Docker Build Info", expanded=False):
        st.code(build_info, language=None)
        st.caption("This information shows the exact versions of MeteoIO and Snowpack compiled into this Docker image.")
else:
    st.sidebar.info("Running in development mode (not Docker)")

# Initialize session state
if 'config' not in st.session_state:
    st.session_state.config = {}

# Initialize ROI validation state
if 'roi_validated' not in st.session_state:
    st.session_state['roi_validated'] = False

# Load selected config
if selected_config != "Create New":
    config_path = config_dir / selected_config
    parser = configparser.ConfigParser(inline_comment_prefixes=("#",))
    parser.read(config_path)

    # Parse config into session state
    if "GENERAL" in parser:
        st.session_state.config['simu_name'] = parser.get("GENERAL", "SIMULATION_NAME", fallback="")
        st.session_state.config['start_date'] = parser.get("GENERAL", "START_DATE", fallback="")
        st.session_state.config['end_date'] = parser.get("GENERAL", "END_DATE", fallback="")

    if "INPUT" in parser:
        st.session_state.config['poi_x'] = parser.get("INPUT", "EAST_epsg2056", fallback="")
        st.session_state.config['poi_y'] = parser.get("INPUT", "NORTH_epsg2056", fallback="")
        st.session_state.config['poi_z'] = parser.get("INPUT", "altLV95", fallback="")
        st.session_state.config['use_shp'] = parser.getboolean("INPUT", "USE_SHP_ROI", fallback=False)
        st.session_state.config['roi_size'] = parser.get("INPUT", "ROI", fallback="1000")
        st.session_state.config['buffer_size'] = parser.get("INPUT", "BUFFERSIZE", fallback="50000")
        st.session_state.config['roi_shapefile'] = parser.get("INPUT", "ROI_SHAPEFILE", fallback="")

    if "OUTPUT" in parser:
        st.session_state.config['coord_sys'] = parser.get("OUTPUT", "OUT_COORDSYS", fallback="CH1903+")
        st.session_state.config['gsd'] = parser.get("OUTPUT", "GSD", fallback="10.0")
        st.session_state.config['gsd_ref'] = parser.get("OUTPUT", "GSD_ref", fallback="2.0")

    if "A3D" in parser:
        # LUS source: support both new LUS_SOURCE and old USE_LUS_TLM formats
        if "LUS_SOURCE" in parser["A3D"]:
            st.session_state.config['lus_source'] = parser.get("A3D", "LUS_SOURCE", fallback="tlm")
        elif "USE_LUS_TLM" in parser["A3D"]:
            use_tlm = parser.getboolean("A3D", "USE_LUS_TLM", fallback=False)
            st.session_state.config['lus_source'] = "tlm" if use_tlm else "constant"
        else:
            st.session_state.config['lus_source'] = "tlm"
        st.session_state.config['lus_cst'] = parser.get("A3D", "LUS_PREVAH_CST", fallback="11500")

# Parent tabs for Switzerland vs Other Locations
mode_tab_switzerland, mode_tab_other = st.tabs(["Switzerland", "Other Locations"])

# ============================================================
# SWITZERLAND MODE
# ============================================================
with mode_tab_switzerland:
    st.info("ℹ️ **Switzerland Mode**: Automatic DEM download from Swisstopo, IMIS station data via MeteoIO, and Snowpack preprocessing")

    # Tabs for configuration sections
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["1.General", "2.ROI/DEM", "3.POI", "4.Landcover", "5.Meteo", "6.Run Config", "7.Run A3D"])

    # ============================================================
    # Tab 1: General Settings
    # ============================================================
    with tab1:
        st.header("General Settings")
    
        col1, col2 = st.columns(2)
    
        with col1:
            simu_name = st.text_input(
                "Simulation Name",
                value=st.session_state.config.get('simu_name', ''),
                help="Unique name for this simulation (no spaces)"
            )
    
        with col2:
            coord_sys = st.selectbox(
                "Coordinate System",
                ["CH1903+", "CH1903", "WGS84"],
                index=["CH1903+", "CH1903", "WGS84"].index(st.session_state.config.get('coord_sys', 'CH1903+'))
            )
    
        st.subheader("Simulation Period")
        col1, col2 = st.columns(2)
    
        # Parse dates from config or use defaults
        default_start = datetime(2023, 10, 1)
        default_end = datetime(2023, 10, 31, 23, 59, 59)
    
        if st.session_state.config.get('start_date'):
            try:
                default_start = datetime.fromisoformat(st.session_state.config['start_date'].replace('T', ' '))
            except:
                pass
    
        if st.session_state.config.get('end_date'):
            try:
                default_end = datetime.fromisoformat(st.session_state.config['end_date'].replace('T', ' '))
            except:
                pass
    
        with col1:
            start_date = st.date_input("Start Date", value=default_start)
            start_time = st.time_input("Start Time", value=default_start.time())
    
        with col2:
            end_date = st.date_input("End Date", value=default_end)
            end_time = st.time_input("End Time", value=default_end.time())
    
        st.divider()
        st.info("Continue to the next tab: **2. ROI/DEM**")
    
    # ============================================================
    # Tab 2: Location & ROI
    # ============================================================
    with tab2:
        st.header("Region of Interest (ROI)")

        # Initialize DEM settings variables (will be set by widgets below)
        gsd = float(st.session_state.config.get('gsd', 10.0))
        gsd_ref = float(st.session_state.config.get('gsd_ref', 2.0))

        use_shapefile = st.checkbox(
            "Use custom shapefile for ROI",
            value=st.session_state.config.get('use_shp', True)
        )
    
        if use_shapefile:
            # Option to provide existing shapefile or draw new one
            shapefile_option = st.radio(
                "How to define ROI:",
                ["Draw on interactive map", "Use existing shapefile"],
                horizontal=True
            )
    
            if shapefile_option == "Use existing shapefile":
                st.markdown("### Select Existing Shapefile")

                # Create two columns: shapefile selection on left, DEM settings on right
                shapefile_col, settings_col = st.columns([2, 1])

                with shapefile_col:
                    # Shapefile browser
                    col1, col2 = st.columns([1, 2])

                    with col1:
                        search_dir = st.text_input(
                            "Search directory:",
                            value="config/",
                            help="Directory to search for shapefiles (must be in a mounted volume)"
                        )

                    with col2:
                        # Find shapefiles in directory
                        found_shapefiles = find_shapefiles(search_dir)

                        if found_shapefiles:
                            # Create dropdown options
                            shapefile_options = ["[Type path manually]"] + [str(shp) for shp in found_shapefiles]

                            selected_shapefile = st.selectbox(
                                "Available shapefiles:",
                                options=shapefile_options,
                                help="Select from found shapefiles or choose to type manually"
                            )

                            # Auto-populate if user selected a file
                            if selected_shapefile != "[Type path manually]":
                                roi_shapefile = selected_shapefile
                                st.session_state['roi_validated'] = True
                                st.success(f"✓ Selected: `{roi_shapefile}`")
                            else:
                                # Manual path input only if user chooses to type manually
                                roi_shapefile = st.text_input(
                                    "Shapefile path:",
                                    value=st.session_state.config.get('roi_shapefile', ''),
                                    help="Path to .shp file (must be in a mounted volume: config/, shapefiles/, etc.)"
                                )
                                st.session_state['roi_validated'] = bool(roi_shapefile)
                        else:
                            st.info(f"ℹ️ No shapefiles found in `{search_dir}`. Enter path manually below.")
                            roi_shapefile = st.text_input(
                                "Shapefile path:",
                                value=st.session_state.config.get('roi_shapefile', ''),
                                help="Path to .shp file (must be in a mounted volume: config/, shapefiles/, etc.)"
                            )
                            st.session_state['roi_validated'] = bool(roi_shapefile)

                    # Info message about Docker volumes
                    st.caption("**Docker users**: Shapefiles must be in mounted volumes (e.g., `config/`, `shapefiles/`). See README for details.")

                with settings_col:
                    # DEM Settings
                    # st.markdown("#### DEM Settings")

                    gsd_ref = st.selectbox(
                        "Reference DEM Resolution",
                        [0.5, 2.0],
                        index=[0.5, 2.0].index(float(st.session_state.config.get('gsd_ref', 2.0))),
                        help="Source DEM resolution from Swisstopo"
                    )

                    gsd = st.number_input(
                        "Output Grid Spacing - meters",
                        value=max(float(st.session_state.config.get('gsd', 10.0)), gsd_ref),
                        min_value=gsd_ref,
                        max_value=100.0,
                        step=1.0,
                        help="Output resolution (smaller = higher resolution, longer processing). Must be >= reference DEM resolution."
                    )

                    mask_lus = st.checkbox(
                        "Mask LUS to polygon shape",
                        value=True,
                        help="If checked, LUS (land use surface) is cropped to polygon. "
                             "If unchecked, LUS covers entire bounding box. "
                             "Note: Grid extent is always the bounding box.",
                        key="mask_lus_checkbox_existing_shp"
                    )
                    st.session_state.config['mask_lus_to_polygon'] = mask_lus

                    mask_dem = st.checkbox(
                        "Mask DEM to polygon shape",
                        value=mask_lus,  # Default to same as LUS
                        disabled=not mask_lus,  # Can only enable if LUS is masked
                        help="If checked, DEM is cropped to polygon (values outside = nodata). "
                             "If unchecked, DEM covers entire bounding box with all valid values. "
                             "Note: Grid extent is always the bounding box. "
                             "Cannot be checked if LUS masking is disabled.",
                        key="mask_dem_checkbox_existing_shp"
                    )
                    # DEM can only be masked if LUS is also masked
                    st.session_state.config['mask_dem_to_polygon'] = mask_dem if mask_lus else False
            else:
                # Interactive map for drawing ROI
                st.markdown("### Draw ROI on Swiss Map")
                st.info("**Instructions**: Use the rectangle (□) or polygon (⬠) tool on the left side of the map to draw your ROI.")

                # Initialize roi_shapefile from session state
                roi_shapefile = st.session_state.config.get('roi_shapefile', '')

                # Create two columns: map on left, controls on right
                map_col, controls_col = st.columns([2, 1])

                with map_col:
                    # Show map
                    roi_map = create_roi_map()
                    map_output = st_folium(roi_map, width=600, height=500, key="roi_map")

                with controls_col:
                    st.markdown("#### Save ROI")

                    # Handle drawn polygon
                    if map_output and map_output.get('last_active_drawing'):
                        drawn_geom = map_output['last_active_drawing']

                        # Debug: Show what was captured
                        with st.expander("🔍 Debug", expanded=False):
                            st.json(drawn_geom)

                        # Validate polygon is within Swiss boundaries
                        is_valid, boundary_msg = check_polygon_in_swiss_boundaries(drawn_geom['geometry'])

                        if is_valid:
                            st.success("✅ Polygon drawn!")
                            st.caption(boundary_msg)

                            # Input for shapefile name
                            shapefile_name = st.text_input(
                                "Shapefile name",
                                value="roi_drawn",
                                help="Name for the shapefile (without .shp extension)"
                            )

                            save_button = st.button("Save ROI", type="primary", width="stretch")

                            if save_button and shapefile_name:
                                # Save shapefile
                                shapefile_dir = Path("config/shapefiles")
                                shapefile_path = shapefile_dir / f"{shapefile_name}.shp"

                                success, message = save_drawn_roi(drawn_geom, str(shapefile_path))
                                if success:
                                    st.success(message)
                                    roi_shapefile = str(shapefile_path)
                                    st.session_state.config['roi_shapefile'] = str(shapefile_path)
                                    # Mark ROI as validated (polygon was already validated above)
                                    st.session_state['roi_validated'] = True
                                else:
                                    st.error(message)
                                    st.session_state['roi_validated'] = False
                        else:
                            # Polygon outside boundaries - show error and prevent saving
                            st.error("🚫 Outside boundaries")
                            st.caption(boundary_msg)
                            st.session_state['roi_validated'] = False
                    else:
                        # Show warning if no polygon drawn yet
                        if not roi_shapefile:
                            st.warning("⚠️ No polygon drawn yet")

                    # DEM Settings (always visible)
                    st.divider()
                    st.markdown("#### DEM Settings")

                    gsd_ref = st.selectbox(
                        "Reference DEM Resolution",
                        [0.5, 2.0],
                        index=[0.5, 2.0].index(float(st.session_state.config.get('gsd_ref', 2.0))),
                        help="Source DEM resolution from Swisstopo"
                    )

                    gsd = st.number_input(
                        "Output Grid Spacing - meters",
                        value=max(float(st.session_state.config.get('gsd', 10.0)), gsd_ref),
                        min_value=gsd_ref,
                        max_value=100.0,
                        step=1.0,
                        help="Output resolution (smaller = higher resolution, longer processing). Must be >= reference DEM resolution."
                    )

                    mask_lus = st.checkbox(
                        "Mask LUS to polygon shape",
                        value=True,
                        help="If checked, LUS (land use surface) is cropped to polygon. "
                             "If unchecked, LUS covers entire bounding box. "
                             "Note: Grid extent is always the bounding box.",
                        key="mask_lus_checkbox"
                    )
                    st.session_state.config['mask_lus_to_polygon'] = mask_lus

                    mask_dem = st.checkbox(
                        "Mask DEM to polygon shape",
                        value=mask_lus,  # Default to same as LUS
                        disabled=not mask_lus,  # Can only enable if LUS is masked
                        help="If checked, DEM is cropped to polygon (values outside = nodata). "
                             "If unchecked, DEM covers entire bounding box with all valid values. "
                             "Note: Grid extent is always the bounding box. "
                             "Cannot be checked if LUS masking is disabled.",
                        key="mask_dem_checkbox"
                    )
                    # DEM can only be masked if LUS is also masked
                    st.session_state.config['mask_dem_to_polygon'] = mask_dem if mask_lus else False
        else:
            # Bounding box mode - need center point coordinates
            # Always mask DEM and LUS for bbox mode
            st.session_state.config['mask_dem_to_polygon'] = True
            st.session_state.config['mask_lus_to_polygon'] = True
            st.markdown("### ROI Center Point")
    
            # Option to pick point on map or enter manually
            center_point_option = st.radio(
                "How to define center point:",
                ["⌨️ Enter coordinates manually", "Pick on map"],
                horizontal=True
            )
    
            if center_point_option == "Pick on map":
                st.info("**Instructions**: Click anywhere on the map to select the ROI center point.")
    
                # Create map for point selection
                center_map = folium.Map(
                    location=[46.8, 8.2],
                    zoom_start=8,
                    tiles=None
                )
    
                # Add Swisstopo base layer
                folium.raster_layers.WmsTileLayer(
                    url='https://wms.geo.admin.ch/',
                    layers='ch.swisstopo.pixelkarte-farbe',
                    fmt='image/png',
                    transparent=False,
                    name='Swisstopo Map',
                    overlay=False,
                    control=True,
                    attr='© swisstopo'
                ).add_to(center_map)
    
                # Add click listener for coordinates
                center_map.add_child(folium.LatLngPopup())
    
                # Display map and capture clicks
                center_map_output = st_folium(center_map, width=800, height=400, key="center_point_map")
    
                # Extract coordinates from map click
                if center_map_output and center_map_output.get('last_clicked'):
                    lat = center_map_output['last_clicked']['lat']
                    lon = center_map_output['last_clicked']['lng']
    
                    # Transform WGS84 to Swiss LV95
                    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2056", always_xy=True)
                    poi_x, poi_y = transformer.transform(lon, lat)
    
                    # Fetch elevation from Swisstopo Height API
                    try:
                        import requests
                        height_url = f"https://api3.geo.admin.ch/rest/services/height?easting={poi_x:.1f}&northing={poi_y:.1f}&sr=2056"
                        response = requests.get(height_url, timeout=5)
    
                        if response.status_code == 200:
                            height_data = response.json()
                            poi_z = float(height_data.get('height', 1500))
                            st.success(f"✅ Point selected: {poi_x:.1f}, {poi_y:.1f} | Elevation: {poi_z:.1f}m")
                        else:
                            # Fallback if API fails
                            poi_z = float(st.session_state.config.get('poi_z', 1500))
                            st.warning(f"⚠️ Point selected: {poi_x:.1f}, {poi_y:.1f} | Using default elevation (API unavailable)")
                    except Exception:
                        # Fallback if request fails
                        poi_z = float(st.session_state.config.get('poi_z', 1500))
                        st.success(f"✅ Point selected: {poi_x:.1f}, {poi_y:.1f} | Using default elevation")
                else:
                    # Use defaults
                    poi_x = float(st.session_state.config.get('poi_x', 645000))
                    poi_y = float(st.session_state.config.get('poi_y', 115000))
                    poi_z = float(st.session_state.config.get('poi_z', 1500))
    
                # Show coordinates (read-only display)
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Easting (EPSG:2056)", f"{poi_x:.1f}")
                with col2:
                    st.metric("Northing (EPSG:2056)", f"{poi_y:.1f}")
    
                # Allow altitude adjustment
                poi_z = st.number_input(
                    "Altitude (m)",
                    value=float(poi_z),
                    format="%.1f",
                    help="Adjust altitude of center point if needed"
                )
    
            else:
                # Manual coordinate entry
                col1, col2, col3 = st.columns(3)
    
                with col1:
                    poi_x = st.number_input(
                        "Easting (EPSG:2056 or CH1903)",
                        value=float(st.session_state.config.get('poi_x', 645000)),
                        format="%.1f",
                        help="X coordinate of ROI center (auto-converts CH1903 to EPSG:2056)"
                    )
    
                with col2:
                    poi_y = st.number_input(
                        "Northing (EPSG:2056 or CH1903)",
                        value=float(st.session_state.config.get('poi_y', 115000)),
                        format="%.1f",
                        help="Y coordinate of ROI center (auto-converts CH1903 to EPSG:2056)"
                    )
    
                with col3:
                    poi_z = st.number_input(
                        "Altitude (m)",
                        value=float(st.session_state.config.get('poi_z', 1500)),
                        format="%.1f",
                        help="Altitude of ROI center point"
                    )
    
            # ROI size (applies to both map and manual entry)
            roi_size = st.number_input(
                "ROI Size (meters)",
                value=int(st.session_state.config.get('roi_size', 1000)),
                min_value=100,
                max_value=50000,
                step=100,
                help="Size of bounding box around center point"
            )
    
            # Validate ROI is within Swiss boundaries
            is_valid, boundary_msg = check_swiss_boundaries(poi_x, poi_y, roi_size)
    
            if is_valid:
                st.success(boundary_msg)
                st.session_state['roi_validated'] = True
            else:
                st.error(boundary_msg)
                st.warning("⚠️ Please adjust the center point or reduce the ROI size to fit within Switzerland.")
                st.session_state['roi_validated'] = False

        # When using shapefile, POI is derived from ROI center (no manual input needed)
        if use_shapefile:
            # Set default POI values (will be overridden by backend from shapefile)
            poi_x = float(st.session_state.config.get('poi_x', 645000))
            poi_y = float(st.session_state.config.get('poi_y', 115000))
            poi_z = float(st.session_state.config.get('poi_z', 1500))
        else:
            # DEM settings for bbox mode (shapefile mode has them in right column)
            st.divider()
            col1, col2 = st.columns(2)

            with col1:
                gsd_ref = st.selectbox(
                    "Reference DEM Resolution",
                    [0.5, 2.0],
                    index=[0.5, 2.0].index(float(st.session_state.config.get('gsd_ref', 2.0))),
                    help="Source DEM resolution from Swisstopo"
                )

            with col2:
                gsd = st.number_input(
                    "Output Grid Spacing - meters",
                    value=max(float(st.session_state.config.get('gsd', 10.0)), gsd_ref),
                    min_value=gsd_ref,
                    max_value=100.0,
                    step=1.0,
                    help="Output resolution (smaller = higher resolution, longer processing). Must be >= reference DEM resolution."
                )

        st.divider()
        st.info("Continue to the next tab: **3. POI**")

    # ============================================================
    # Tab 3: Points of Interest (POI)
    # ============================================================
    with tab3:
        st.header("Points of Interest (Optional)")
        st.caption("Define specific locations for detailed output. POIs are written to a SMET file for Alpine3D.")

        # Check if ROI is defined
        roi_defined = st.session_state.get('roi_validated', False)

        if not roi_defined:
            st.warning("**ROI not defined**: Please define and validate a Region of Interest in the **2. ROI/DEM** tab first.")
            st.info("POI selection will be enabled once the ROI is validated.")
        else:
            # Initialize POI list in session state for Switzerland mode
            if 'poi_list_ch' not in st.session_state:
                st.session_state.poi_list_ch = []

            # Get ROI info from session state
            _roi_shapefile = st.session_state.config.get('roi_shapefile', '')
            _use_shapefile = bool(_roi_shapefile)
            _poi_x = float(st.session_state.config.get('poi_x', 0) or 0)
            _poi_y = float(st.session_state.config.get('poi_y', 0) or 0)
            _roi_size = float(st.session_state.config.get('roi_size', 1000) or 1000)

            # Get ROI bounds for validation
            if _use_shapefile and _roi_shapefile:
                # Load shapefile to get bounds
                try:
                    roi_gdf = gpd.read_file(_roi_shapefile)
                    if roi_gdf.crs and roi_gdf.crs.to_epsg() != 2056:
                        roi_gdf = roi_gdf.to_crs("EPSG:2056")
                    roi_bounds = roi_gdf.total_bounds  # (minx, miny, maxx, maxy)
                    roi_polygon = roi_gdf.union_all()
                except Exception:
                    roi_bounds = None
                    roi_polygon = None
            else:
                # Bounding box mode
                half_size = _roi_size / 2
                roi_bounds = (_poi_x - half_size, _poi_y - half_size, _poi_x + half_size, _poi_y + half_size)
                roi_polygon = None

            # Two columns: map on left, form/table on right
            col_map, col_form = st.columns([2, 1])

            with col_map:
                st.subheader("Add POI on Map")
                st.caption("Click on the map to add a POI. Elevation is fetched automatically from Swisstopo.")

                # Create map centered on ROI
                if roi_bounds is not None:
                    center_x = (roi_bounds[0] + roi_bounds[2]) / 2
                    center_y = (roi_bounds[1] + roi_bounds[3]) / 2
                    transformer_to_wgs = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)
                    center_lon, center_lat = transformer_to_wgs.transform(center_x, center_y)
                else:
                    center_lat, center_lon = 46.8, 8.2

                poi_map = folium.Map(
                    location=[center_lat, center_lon],
                    tiles="https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
                    attr="swisstopo"
                )

                # Auto-fit map to ROI bounds
                if roi_bounds is not None:
                    sw_lon, sw_lat = transformer_to_wgs.transform(roi_bounds[0], roi_bounds[1])
                    ne_lon, ne_lat = transformer_to_wgs.transform(roi_bounds[2], roi_bounds[3])
                    poi_map.fit_bounds([[sw_lat, sw_lon], [ne_lat, ne_lon]])

                # Draw ROI boundary on map
                if roi_bounds is not None:
                    transformer_to_wgs = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)
                    if roi_polygon is not None and _use_shapefile:
                        # Draw shapefile polygon
                        from shapely.geometry import mapping
                        import json
                        roi_geojson = roi_gdf.to_crs("EPSG:4326").to_json()
                        folium.GeoJson(
                            roi_geojson,
                            style_function=lambda x: {'fillColor': 'blue', 'color': 'blue', 'weight': 2, 'fillOpacity': 0.1}
                        ).add_to(poi_map)
                    else:
                        # Draw bounding box
                        sw_lon, sw_lat = transformer_to_wgs.transform(roi_bounds[0], roi_bounds[1])
                        ne_lon, ne_lat = transformer_to_wgs.transform(roi_bounds[2], roi_bounds[3])
                        folium.Rectangle(
                            bounds=[[sw_lat, sw_lon], [ne_lat, ne_lon]],
                            color='blue',
                            weight=2,
                            fill=True,
                            fillOpacity=0.1
                        ).add_to(poi_map)

                # Add existing POIs as markers
                for idx, poi in enumerate(st.session_state.poi_list_ch):
                    transformer_to_wgs = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)
                    lon, lat = transformer_to_wgs.transform(poi['x'], poi['y'])
                    folium.Marker(
                        location=[lat, lon],
                        popup=f"{poi['name']}<br>({poi['x']:.0f}, {poi['y']:.0f})<br>{poi['z']:.0f}m",
                        icon=folium.Icon(color='red', icon='info-sign')
                    ).add_to(poi_map)

                # Enable click to add POI
                poi_map.add_child(folium.LatLngPopup())

                # Display map and capture clicks
                poi_map_output = st_folium(poi_map, width=600, height=400, key="poi_map_ch")

                # Process map click
                if poi_map_output and poi_map_output.get('last_clicked'):
                    lat = poi_map_output['last_clicked']['lat']
                    lon = poi_map_output['last_clicked']['lng']

                    # Transform WGS84 to Swiss LV95
                    transformer_to_ch = Transformer.from_crs("EPSG:4326", "EPSG:2056", always_xy=True)
                    click_x, click_y = transformer_to_ch.transform(lon, lat)

                    # Store in session state for the form
                    st.session_state['poi_click_x'] = click_x
                    st.session_state['poi_click_y'] = click_y

                    # Fetch elevation from Swisstopo
                    try:
                        import requests
                        height_url = f"https://api3.geo.admin.ch/rest/services/height?easting={click_x:.1f}&northing={click_y:.1f}&sr=2056"
                        response = requests.get(height_url, timeout=5)
                        if response.status_code == 200:
                            height_data = response.json()
                            st.session_state['poi_click_z'] = float(height_data.get('height', 0))
                        else:
                            st.session_state['poi_click_z'] = 0.0
                    except Exception:
                        st.session_state['poi_click_z'] = 0.0

                    st.info(f"Clicked: ({click_x:.0f}, {click_y:.0f}) at {st.session_state.get('poi_click_z', 0):.0f}m - Enter a name and click 'Add POI'")

            with col_form:
                st.subheader("POI Details")

                # Function to check if POI is within ROI
                def is_poi_in_roi(x, y):
                    if roi_polygon is not None and use_shapefile:
                        from shapely.geometry import Point
                        return roi_polygon.contains(Point(x, y))
                    elif roi_bounds is not None:
                        return (roi_bounds[0] <= x <= roi_bounds[2] and
                                roi_bounds[1] <= y <= roi_bounds[3])
                    return True

                # Form to add POI
                with st.form("add_poi_form_ch", clear_on_submit=True):
                    poi_name_ch = st.text_input(
                        "POI Name",
                        value="",
                        placeholder="e.g., Summit, Station1"
                    )

                    # Pre-fill from map click or allow manual entry
                    poi_x_ch = st.number_input(
                        "Easting (EPSG:2056)",
                        value=float(st.session_state.get('poi_click_x', 0)),
                        format="%.1f"
                    )
                    poi_y_ch = st.number_input(
                        "Northing (EPSG:2056)",
                        value=float(st.session_state.get('poi_click_y', 0)),
                        format="%.1f"
                    )
                    poi_z_ch = st.number_input(
                        "Elevation (m)",
                        value=float(st.session_state.get('poi_click_z', 0)),
                        format="%.1f"
                    )

                    add_poi_btn = st.form_submit_button("Add POI", type="primary", use_container_width=True)

                    if add_poi_btn:
                        if not poi_name_ch:
                            st.error("Please enter a POI name")
                        elif poi_x_ch == 0 and poi_y_ch == 0:
                            st.error("Please click on the map or enter coordinates")
                        elif not is_poi_in_roi(poi_x_ch, poi_y_ch):
                            st.error("POI is outside the defined ROI boundary")
                        else:
                            st.session_state.poi_list_ch.append({
                                'name': poi_name_ch,
                                'x': poi_x_ch,
                                'y': poi_y_ch,
                                'z': poi_z_ch
                            })
                            # Clear click coordinates
                            st.session_state.pop('poi_click_x', None)
                            st.session_state.pop('poi_click_y', None)
                            st.session_state.pop('poi_click_z', None)
                            st.rerun()

                st.divider()

                # Display POI table
                st.subheader(f"POI List ({len(st.session_state.poi_list_ch)})")

                if st.session_state.poi_list_ch:
                    for idx, poi in enumerate(st.session_state.poi_list_ch):
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.text(f"{poi['name']}: ({poi['x']:.0f}, {poi['y']:.0f}, {poi['z']:.0f}m)")
                        with col2:
                            if st.button("🗑️", key=f"del_poi_ch_{idx}", help="Delete this POI"):
                                st.session_state.poi_list_ch.pop(idx)
                                st.rerun()

                    # Clear all button
                    if st.button("Clear All POIs", type="secondary", use_container_width=True):
                        st.session_state.poi_list_ch = []
                        st.rerun()
                else:
                    st.caption("No POIs added yet. Click on the map or enter coordinates manually.")

        st.divider()
        st.info("Continue to the next tab: **4. Landcover**")

    # ============================================================
    # Tab 4: Landcover
    # ============================================================
    with tab4:
        st.header("Land Cover")
        st.caption("Alpine3D uses 'LUS' (Land Use Surface) internally, but this actually refers to land cover classification.")

        # Dropdown for source selection
        lus_source_options = ["SwissTLMRegio", "BFS Arealstatistik (NOAS04)", "Constant Value"]
        lus_source_map = {
            "SwissTLMRegio": "tlm",
            "BFS Arealstatistik (NOAS04)": "bfs",
            "Constant Value": "constant"
        }
        lus_source_reverse = {v: k for k, v in lus_source_map.items()}

        # Get default from config (with backwards compatibility)
        default_source = st.session_state.config.get('lus_source', 'tlm')
        if default_source not in lus_source_map.values():
            default_source = 'tlm'
        default_display = lus_source_reverse.get(default_source, "SwissTLMRegio")
        default_idx = lus_source_options.index(default_display)

        lus_source_display = st.selectbox(
            "Land Cover Data Source",
            options=lus_source_options,
            index=default_idx,
            help="**SwissTLMRegio**: Swisstopo topographic land cover (11 categories)\n\n"
                 "**BFS Arealstatistik (NOAS04)**: Federal statistics LC_27 classification (27 categories)\n\n"
                 "**Constant Value**: Single PREVAH code for entire domain"
        )

        lus_source = lus_source_map[lus_source_display]

        # Only show constant value input when "Constant Value" is selected
        if lus_source == "constant":
            lus_constant = st.number_input(
                "Constant PREVAH Code",
                value=int(st.session_state.config.get('lus_cst', 11500)),
                help="Single PREVAH land cover code (format: 1LLCD where LL is PREVAH code)."
            )
        else:
            lus_constant = int(st.session_state.config.get('lus_cst', 11500))

            # Show mapping table for selected source
            with st.expander("View category mapping to PREVAH codes", expanded=False):
                if lus_source == "tlm":
                    st.markdown("**SwissTLMRegio to PREVAH mapping:**")
                    tlm_mapping_data = {
                        "TLM Category": ["Wald", "Fels", "Geroell", "Gletscher", "See", "Stausee",
                                        "Siedl", "Stadtzentr", "Sumpf", "Obstanlage", "Reben"],
                        "PREVAH Code": [3, 15, 21, 14, 1, 1, 2, 2, 22, 18, 29],
                        "PREVAH Description": ["coniferous forest", "rock", "alpine vegetation", "bare ice",
                                              "water", "water", "settlement", "settlement", "wetlands",
                                              "fruit", "grapes"]
                    }
                    st.table(tlm_mapping_data)
                elif lus_source == "bfs":
                    st.markdown("**BFS Arealstatistik LC_27 to PREVAH mapping:**")
                    lc27_mapping_data = {
                        "LC_27": list(range(1, 28)),
                        "Description": [
                            "Industrial buildings", "Commercial/services", "Residential",
                            "Agricultural buildings", "Unspecified buildings", "Transport areas",
                            "Special urban areas", "Recreation/green spaces", "Orchards", "Vineyards",
                            "Horticulture", "Arable land", "Meadows/pastures", "Alpine pastures",
                            "Dense forest", "Open forest", "Shrub forest", "Hedges/groves",
                            "Standing water", "Flowing water", "Unproductive vegetation",
                            "Bare ground", "Rock", "Sand/gravel", "Glacier/firn", "Wetlands",
                            "Other unproductive"
                        ],
                        "PREVAH": [2, 2, 2, 2, 2, 11, 2, 7, 18, 29, 19, 6, 7, 23,
                                  5, 5, 8, 8, 1, 1, 21, 26, 15, 26, 14, 22, 27],
                        "PREVAH Name": [
                            "settlement", "settlement", "settlement", "settlement", "settlement", "road",
                            "settlement", "pasture", "fruit", "grapes", "vegetables", "cereals",
                            "pasture", "rough pasture", "mixed forest", "mixed forest", "bush", "bush",
                            "water", "water", "alpine vegetation", "bare soil vegetation", "rock",
                            "bare soil vegetation", "bare ice", "wetlands", "free"
                        ]
                    }
                    st.dataframe(lc27_mapping_data, width="stretch", hide_index=True)

        # Store in session state
        st.session_state.config['lus_source'] = lus_source

        st.divider()
        st.info("Continue to the next tab: **5. Meteo**")

    # ============================================================
    # Tab 5: Meteo Settings
    # ============================================================
    with tab5:
        st.header("Meteo files retrieval with Snowpack")

        # IMIS database access requires VPN - only available in local Docker builds
        _IMIS_AVAILABLE = os.environ.get('A3D_IMIS_AVAILABLE', '').lower() in ('true', '1', 'yes')

        if not _IMIS_AVAILABLE:
            st.info("**Snowpack preprocessing disabled** - The web version cannot access the SLF/IMIS database for meteo retrieval. Run locally with Docker to enable.")
            skip_snowpack = st.checkbox("Skip Snowpack preprocessing",
                                        value=True,
                                        disabled=True,
                                        help="Snowpack preprocessing requires VPN access to SLF/IMIS database.")
        else:
            skip_snowpack = st.checkbox("Skip Snowpack preprocessing",
                                        value=False,
                                        help="Disable Snowpack preprocessing step. Meteo files will not be downloaded and preprocessed (RSWR --> ISWR).")

        buffer_size = st.number_input(
            "Buffer Size for IMIS Stations (meters)",
            value=int(st.session_state.config.get('buffer_size', 10000)),
            min_value=1000,
            max_value=200000,
            step=1000,
            help="Distance to search for meteorological stations around the ROI. Stations within this buffer will be detected."
        )

        # Snowpack configuration (only shown if not skipping)
        if not skip_snowpack:
            st.warning("**SLF/WSL VPN Required**: Snowpack preprocessing requires VPN access to the IMIS database via MeteoIO.")
            st.warning("**The IMIS database is not sanitized and may contain missing or inconsistent data. This can lead to Snowpack crashes.**")

            st.divider()

            # Binary paths section
            st.subheader("Binary Paths")
            st.caption("Binary paths are configured via server environment variables. Contact administrator to change.")

            col1, col2 = st.columns(2)
            with col1:
                # Get default from environment variable
                default_meteoio = get_meteoio_bin()
                meteoio_bin_path = st.text_input(
                    "MeteoIO Binary Path",
                    value=st.session_state.config.get('meteoio_bin_path', default_meteoio),
                    placeholder=default_meteoio,
                    help=f"Server default: {default_meteoio} (from METEOIO_BIN env var)"
                )
                st.session_state.config['meteoio_bin_path'] = meteoio_bin_path or default_meteoio

            with col2:
                # Get default from environment variable
                default_snowpack = get_snowpack_bin()
                snowpack_bin_path = st.text_input(
                    "Snowpack Binary Path",
                    value=st.session_state.config.get('snowpack_bin_path', default_snowpack),
                    placeholder=default_snowpack,
                    help=f"Server default: {default_snowpack} (from SNOWPACK_BIN env var)"
                )
                st.session_state.config['snowpack_bin_path'] = snowpack_bin_path or default_snowpack

            st.divider()

            # Snowpack INI editor in collapsible section
            with st.expander("Snowpack Configuration (INI)", expanded=False):
                st.caption("Edit the Snowpack configuration file. Changes will be used during the preprocessing step.")

                # Load Snowpack INI template (embedded with file override capability)
                if 'snowpack_ini_content' not in st.session_state:
                    try:
                        st.session_state.snowpack_ini_content = get_template('spConfig.ini')
                    except KeyError:
                        st.session_state.snowpack_ini_content = "; Snowpack configuration template not found"

                snowpack_ini_edited = st.text_area(
                    "Snowpack INI Content",
                    value=st.session_state.snowpack_ini_content,
                    height=400,
                    key="snowpack_ini_editor"
                )
                st.session_state.snowpack_ini_content = snowpack_ini_edited

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Reset to Default", key="reset_snowpack_ini"):
                        if snowpack_ini_path.exists():
                            with open(snowpack_ini_path, 'r') as f:
                                st.session_state.snowpack_ini_content = f.read()
                            st.rerun()
                with col2:
                    if st.button("Save to Template", key="save_snowpack_ini"):
                        with open(snowpack_ini_path, 'w') as f:
                            f.write(snowpack_ini_edited)
                        st.success("Snowpack INI template saved!")

        st.divider()
        st.info("Continue to the next tab: **6. Run Config**")

    # ============================================================
    # Tab 6: Run Config
    # ============================================================
    with tab6:
        st.header("Configuration Summary")
    
        # Build start/end datetime strings
        start_dt = datetime.combine(start_date, start_time)
        end_dt = datetime.combine(end_date, end_time)
    
        # Summary display
        col1, col2 = st.columns(2)

        with col1:
            st.metric("Simulation Name", simu_name)
            st.metric("Period", f"{(end_dt - start_dt).days} days")
            st.metric("Coordinate System", coord_sys)
            st.metric("Grid Spacing", f"{gsd}m")

        with col2:
            if use_shapefile:
                st.metric("ROI", "Custom Shapefile/Polygon")
            else:
                st.metric("ROI", f"{roi_size}m bbox")

            # Land use option
            lus_labels = {
                "tlm": "SwissTLMRegio",
                "bfs": "BFS Arealstatistik",
                "constant": f"Constant ({lus_constant})"
            }
            st.metric("Land Use", lus_labels.get(lus_source, "Unknown"))

            # DEM and LUS masking options (only show if using shapefile)
            if use_shapefile:
                mask_dem_status = st.session_state.config.get('mask_dem_to_polygon', True)
                mask_lus_status = st.session_state.config.get('mask_lus_to_polygon', True)
                st.metric("DEM Masking", "Polygon" if mask_dem_status else "Full BBox")
                st.metric("LUS Masking", "Polygon" if mask_lus_status else "Full BBox")

            # POI count
            poi_count = len(st.session_state.get('poi_list_ch', []))
            st.metric("POIs", poi_count if poi_count > 0 else "None")

        st.divider()

        # Save config section
        col1, col2 = st.columns([3, 1])
    
        with col1:
            save_config_name = st.text_input(
                "Config filename (without .ini)",
                value=simu_name if simu_name else "my_simulation",
                help="Name for saving this A3Dshell configuration"
            )
    
        with col2:
            st.write("")  # Spacing
            st.write("")  # Spacing
            save_button = st.button("Save Config", width="stretch")
    
        if save_button:
            if not save_config_name:
                st.error("Please provide a config filename")
            else:
                # Create config file
                config_content = f"""# A3Dshell Configuration
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

[GENERAL]
SIMULATION_NAME = {simu_name}
START_DATE = {start_dt.strftime('%Y-%m-%dT%H:%M:%S')}
END_DATE = {end_dt.strftime('%Y-%m-%dT%H:%M:%S')}

[INPUT]
EAST_epsg2056 = {poi_x}
NORTH_epsg2056 = {poi_y}
altLV95 = {poi_z}
USE_SHP_ROI = {'true' if use_shapefile else 'false'}
"""
    
                if use_shapefile:
                    config_content += f"ROI_SHAPEFILE = {roi_shapefile}\n"
                else:
                    config_content += f"ROI = {roi_size}\n"

                config_content += f"BUFFERSIZE = {buffer_size}\n"
                config_content += f"\n[OUTPUT]\n"
                config_content += f"OUT_COORDSYS = {coord_sys}\n"
                config_content += f"GSD = {gsd}\n"
                config_content += f"GSD_ref = {gsd_ref}\n"
                config_content += f"DEM_ADDFMTLIST =\n"
                config_content += f"MESH_FMT = vtu\n"
                config_content += f"MASK_DEM_TO_POLYGON = {'true' if st.session_state.config.get('mask_dem_to_polygon', True) else 'false'}\n"
                config_content += f"MASK_LUS_TO_POLYGON = {'true' if st.session_state.config.get('mask_lus_to_polygon', True) else 'false'}\n"
                config_content += f"\n[MAPS]\n"
                config_content += f"PLOT_HORIZON = false\n"
                config_content += f"\n[A3D]\n"
                config_content += f"USE_GROUNDEYE = false\n"
                config_content += f"LUS_SOURCE = {lus_source}\n"

                if lus_source == "constant":
                    config_content += f"LUS_PREVAH_CST = {lus_constant}\n"

                config_content += "DO_PVP_3D = false\n"
                config_content += "PVP_3D_FMT = vtu\n"
                config_content += "SP_BIN_PATH = snowpack\n"

                # Add POIs if defined
                if st.session_state.get('poi_list_ch'):
                    config_content += "\n[POIS]\n"
                    for poi in st.session_state.poi_list_ch:
                        config_content += f"{poi['name']} = {poi['x']},{poi['y']},{poi['z']}\n"

                # Save file
                config_path = config_dir / f"{save_config_name}.ini"
                with open(config_path, 'w') as f:
                    f.write(config_content)

                st.success(f"✅ Configuration saved to: {config_path}")

        st.divider()

        # Run simulation section
        st.header("Run Setup")

        col1, col2 = st.columns([2, 1])

        with col1:
            log_level = st.selectbox("Log Level", ["INFO", "DEBUG", "WARNING", "ERROR"])
    
        with col2:
            st.write("")  # Spacing
            st.write("")  # Spacing
    
        # Check if ROI is validated
        roi_validated = st.session_state.get('roi_validated', False)
    
        # Show validation status
        if not roi_validated:
            st.error("Cannot run simulation: ROI/POI must be confirmed within Swiss boundaries")
            st.info("Go to the **Location & ROI** tab to configure and validate your region of interest.")
    
        # Disable button if validation failed
        if st.button("▶️ Start Run", type="primary", width="stretch", disabled=not roi_validated):
            if not simu_name:
                st.error("Please provide a simulation name")
            else:
                # Create a temporary config for this run
                temp_config = config_dir / f"_temp_{simu_name}.ini"
    
                config_content = f"""# Temporary A3Dshell Configuration
    [GENERAL]
    SIMULATION_NAME = {simu_name}
    START_DATE = {start_dt.strftime('%Y-%m-%dT%H:%M:%S')}
    END_DATE = {end_dt.strftime('%Y-%m-%dT%H:%M:%S')}
    
    [INPUT]
    EAST_epsg2056 = {poi_x}
    NORTH_epsg2056 = {poi_y}
    altLV95 = {poi_z}
    USE_SHP_ROI = {'true' if use_shapefile else 'false'}
    """
    
                if use_shapefile:
                    config_content += f"ROI_SHAPEFILE = {roi_shapefile}\n"
                else:
                    config_content += f"ROI = {roi_size}\n"

                config_content += f"BUFFERSIZE = {buffer_size}\n"
                config_content += f"\n[OUTPUT]\n"
                config_content += f"OUT_COORDSYS = {coord_sys}\n"
                config_content += f"GSD = {gsd}\n"
                config_content += f"GSD_ref = {gsd_ref}\n"
                config_content += f"DEM_ADDFMTLIST =\n"
                config_content += f"MESH_FMT = vtu\n"
                config_content += f"MASK_DEM_TO_POLYGON = {'true' if st.session_state.config.get('mask_dem_to_polygon', True) else 'false'}\n"
                config_content += f"MASK_LUS_TO_POLYGON = {'true' if st.session_state.config.get('mask_lus_to_polygon', True) else 'false'}\n"
                config_content += f"\n[MAPS]\n"
                config_content += f"PLOT_HORIZON = false\n"
                config_content += f"\n[A3D]\n"
                config_content += f"USE_GROUNDEYE = false\n"
                config_content += f"LUS_SOURCE = {lus_source}\n"

                if lus_source == "constant":
                    config_content += f"LUS_PREVAH_CST = {lus_constant}\n"

                config_content += "DO_PVP_3D = false\n"
                config_content += "PVP_3D_FMT = vtu\n"
                config_content += "SP_BIN_PATH = snowpack\n"

                # Add POIs if defined
                if st.session_state.get('poi_list_ch'):
                    config_content += "\n[POIS]\n"
                    for poi in st.session_state.poi_list_ch:
                        config_content += f"{poi['name']} = {poi['x']},{poi['y']},{poi['z']}\n"

                # Save temp config
                with open(temp_config, 'w') as f:
                    f.write(config_content)

                # Build command
                cmd = [
                    "python", "-m", "src.cli",
                    "--config", str(temp_config),
                    "--log-level", log_level
                ]
    
                if skip_snowpack:
                    cmd.append("--skip-snowpack")
    
                st.info(f"🚀 Starting simulation: {simu_name}")
                st.code(" ".join(cmd), language="bash")

                # Run simulation with real-time output streaming
                st.subheader("Run Log")
                log_container = st.container(height=400)
                log_placeholder = log_container.empty()
                full_log = []

                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    # Stream output in real-time
                    for line in process.stdout:
                        full_log.append(line.rstrip())
                        # Update log display
                        log_placeholder.code('\n'.join(full_log), language="text")

                    process.wait()

                    if process.returncode == 0:
                        st.success("✅ Run completed successfully!")
                        st.snow()

                        # Show output location and set working directory for A3D tab
                        output_dir = Path("output") / simu_name
                        if output_dir.exists():
                            st.info(f"Output location: {output_dir}")
                            # Set working directory for Run A3D tab
                            st.session_state.config['a3d_working_dir'] = str(output_dir)
                            st.info("Working directory set for **Run A3D** tab.")

                            # Offer download of the ZIP file produced by the CLI
                            zip_path = Path("output") / f"{simu_name}.zip"
                            if zip_path.exists():
                                with open(zip_path, "rb") as f:
                                    zip_data = f.read()
                                st.download_button(
                                    label="Download Simulation Package (.zip)",
                                    data=zip_data,
                                    file_name=f"{simu_name}.zip",
                                    mime="application/zip"
                                )
                            else:
                                st.warning(f"ZIP file not found at {zip_path}")
                    else:
                        st.error(f"❌ Run failed with exit code {process.returncode}")

                except Exception as e:
                    st.error(f"❌ Error running simulation: {str(e)}")

                finally:
                    # Clean up temp config
                    if temp_config.exists():
                        temp_config.unlink()

    # ============================================================
    # Tab 7: Run A3D (enabled via A3D_ENABLE_RUN_TAB env var for local Docker)
    # ============================================================
    with tab7:
        # Enable Tab 7 when running locally with Docker (set in docker-compose.yml)
        _TAB7_ENABLED = os.environ.get('A3D_ENABLE_RUN_TAB', '').lower() in ('true', '1', 'yes')

        if not _TAB7_ENABLED:
            st.info("**Run A3D** - This feature is not available in the web version. Run locally with Docker to enable.")

        if _TAB7_ENABLED:
            # Default to session state if set, otherwise try output/{simu_name}
            default_working_dir = st.session_state.config.get('a3d_working_dir', '')
            if not default_working_dir and simu_name:
                potential_dir = f"output/{simu_name}"
                if Path(potential_dir).exists():
                    default_working_dir = potential_dir

            a3d_working_dir = st.text_input(
                "Working Directory",
                value=default_working_dir,
                placeholder="output/my_simulation",
                help="Path to the simulation directory. Must contain input/ folder with DEM, LUS, and meteo files."
            )
            st.session_state.config['a3d_working_dir'] = a3d_working_dir

            # Validate working directory
            working_dir_valid = False
            if a3d_working_dir:
                working_dir_path = Path(a3d_working_dir)
                if working_dir_path.exists():
                    input_dir = working_dir_path / "input"
                    if input_dir.exists():
                        st.success(f"Working directory found: {a3d_working_dir}")
                        working_dir_valid = True
                    else:
                        st.warning(f"Working directory exists but missing 'input/' folder.")
                else:
                    st.warning(f"Working directory not found: {a3d_working_dir}")
            else:
                st.info("No working directory specified. Run 'Run Config' first or enter a path manually.")

            st.divider()

            # A3D Binary path
            st.subheader("Alpine3D Binary")
            st.caption("Binary path is configured via server environment variables. Contact administrator to change.")

            # Get default from environment variable
            default_alpine3d = get_alpine3d_bin()
            a3d_bin_path = st.text_input(
                "Alpine3D Binary Path",
                value=st.session_state.config.get('a3d_bin_path', default_alpine3d),
                placeholder=default_alpine3d,
                help=f"Server default: {default_alpine3d} (from ALPINE3D_BIN env var)"
            )
            st.session_state.config['a3d_bin_path'] = a3d_bin_path or default_alpine3d

            if not a3d_bin_path and not default_alpine3d:
                st.warning("No Alpine3D binary path configured. Contact server administrator.")

            st.divider()

            # A3D INI editor
            st.subheader("Alpine3D Configuration (INI)")
            st.caption("Edit the Alpine3D configuration file. This will be used when running the A3D simulation.")

            # Load A3D INI template (embedded with file override capability)
            if 'a3d_ini_content' not in st.session_state:
                try:
                    st.session_state.a3d_ini_content = get_template('a3dConfig.ini')
                except KeyError:
                    st.session_state.a3d_ini_content = "; Alpine3D configuration template not found"

            a3d_ini_edited = st.text_area(
                "Alpine3D INI Content",
                value=st.session_state.a3d_ini_content,
                height=400,
                key="a3d_ini_editor"
            )
            st.session_state.a3d_ini_content = a3d_ini_edited

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Reset to Default", key="reset_a3d_ini"):
                    if a3d_ini_path.exists():
                        with open(a3d_ini_path, 'r') as f:
                            st.session_state.a3d_ini_content = f.read()
                        st.rerun()
            with col2:
                if st.button("Save to Template", key="save_a3d_ini"):
                    with open(a3d_ini_path, 'w') as f:
                        f.write(a3d_ini_edited)
                    st.success("Alpine3D INI template saved!")

            st.divider()

            # Run A3D section
            st.subheader("Run Alpine3D")

            # Check if ready to run
            can_run_a3d = bool(a3d_bin_path) and working_dir_valid

            if not working_dir_valid:
                st.error("Working directory not configured or invalid. Set the working directory above.")
            elif not a3d_bin_path:
                st.warning("Alpine3D binary path not specified.")
            else:
                st.success(f"Ready to run Alpine3D in: **{a3d_working_dir}**")

            # Run button
            run_a3d_disabled = not can_run_a3d
            if st.button("Run Alpine3D", type="primary", disabled=run_a3d_disabled, key="run_a3d_btn"):
                st.subheader("Run Log")
                log_container = st.container(height=400)
                log_placeholder = log_container.empty()
                full_log = []

                # Placeholder for A3D run - will be implemented later
                log_placeholder.code(f"Alpine3D run not yet implemented.\n\nWorking directory: {a3d_working_dir}\nBinary: {a3d_bin_path}\n\nThis will execute the A3D simulation using the configured INI file.", language="text")
                st.info("Alpine3D execution will be implemented in a future update.")

# ============================================================
# OTHER LOCATIONS MODE
# ============================================================
with mode_tab_other:
    st.info("**Other Locations Mode**: Provide your own DEM (GeoTIFF) and land cover settings. Meteorological data must be provided manually after setup.")

    # Tabs for Other Locations mode (matching Switzerland structure)
    tab1_other, tab2_other, tab3_other, tab4_other = st.tabs(["1. General", "2. DEM", "3. Land Cover", "4. Run"])

    # ============================================================
    # Tab 1: General Settings (Other Locations)
    # ============================================================
    with tab1_other:
        st.header("General Settings")

        simu_name_other = st.text_input(
            "Simulation Name",
            value=st.session_state.config.get('simu_name', ''),
            help="Unique name for this simulation (no spaces allowed)",
            key="simu_name_other"
        )

        if simu_name_other and " " in simu_name_other:
            st.error("Simulation name cannot contain spaces")

        st.divider()
        st.info("Continue to the next tab: **2. DEM**")

    # ============================================================
    # Tab 2: DEM (Other Locations)
    # ============================================================
    with tab2_other:
        st.header("DEM Setup")

        st.subheader("Target Coordinate System")
        target_epsg = st.number_input(
            "EPSG Code",
            value=int(st.session_state.config.get('target_epsg', 32632)),
            min_value=1000,
            max_value=99999,
            help="EPSG code for your target coordinate system (e.g., 32632 for UTM Zone 32N, 2056 for Swiss LV95)"
        )

        st.divider()

        st.subheader("DEM Selection")
        st.markdown("Select your Digital Elevation Model (GeoTIFF) from the `config/dem/` directory.")
        st.caption("Place your DEM files in `config/dem/` before starting.")

        # Browse for DEM files in config/dem/
        dem_dir = Path("config/dem")
        dem_dir.mkdir(parents=True, exist_ok=True)
        dem_files = list(dem_dir.glob("*.tif")) + list(dem_dir.glob("*.tiff"))

        if dem_files:
            dem_options = ["[Select a DEM file]"] + [dem.name for dem in dem_files]
            selected_dem = st.selectbox(
                "Available DEM files:",
                options=dem_options,
                help="GeoTIFF files found in config/dem/",
                key="dem_select_other"
            )

            if selected_dem != "[Select a DEM file]":
                dem_path = dem_dir / selected_dem
                st.success(f"DEM selected: {selected_dem}")
                st.session_state.config['user_dem_path'] = str(dem_path.absolute())

                # Show file info
                file_size_mb = dem_path.stat().st_size / (1024 * 1024)
                st.caption(f"File size: {file_size_mb:.2f} MB")
            else:
                st.session_state.config['user_dem_path'] = None
        else:
            st.warning("No DEM files found in `config/dem/`")
            st.caption("Place your GeoTIFF (.tif) DEM files in the `config/dem/` directory and refresh.")
            st.session_state.config['user_dem_path'] = None

        st.divider()

        st.subheader("Output Grid")
        gsd_other = st.number_input(
            "Output Grid Spacing (m)",
            value=float(st.session_state.config.get('gsd', 25.0)),
            min_value=1.0,
            max_value=1000.0,
            help="Output resolution in meters. Smaller values = higher resolution but longer processing time.",
            key="gsd_other"
        )

        st.divider()
        st.info("Continue to the next tab: **3. Land Cover**")

    # ============================================================
    # Tab 3: Land Cover (Other Locations)
    # ============================================================
    with tab3_other:
        st.header("Land Cover")
        st.caption("Alpine3D uses 'LUS' (Land Use Surface) internally, but this actually refers to land cover classification.")

        st.warning("Swiss land cover data sources (SwissTLMRegio, BFS Arealstatistik) are not available for non-Swiss locations.")

        lus_source_other = "constant"  # Fixed to constant for Other Locations mode

        lus_constant_other = st.number_input(
            "Constant PREVAH Code",
            value=int(st.session_state.config.get('lus_cst', 11500)),
            help="Single PREVAH land cover code applied to all grid cells.",
            key="lus_constant_other"
        )

        # Show PREVAH codes reference
        with st.expander("View available PREVAH codes", expanded=False):
            st.markdown("**PREVAH Land Cover Codes:**")
            prevah_codes = {
                "Code": [1, 2, 3, 4, 5, 6, 7, 8, 11, 13, 14, 15, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
                "Description": [
                    "water", "settlement", "coniferous forest", "deciduous forest", "mixed forest",
                    "cereals", "pasture", "bush", "road", "firn", "bare ice", "rock", "fruit",
                    "vegetables", "wheat", "alpine vegetation", "wetlands", "rough pasture",
                    "subalpine meadow", "alpine meadow", "bare soil vegetation", "free", "corn", "grapes"
                ]
            }
            st.dataframe(prevah_codes, width="stretch", hide_index=True)
            st.caption("A3D format: 1LLCD where LL is the PREVAH code (e.g., 11500 for rock)")

        st.divider()

        # POIs Section
        st.header("Points of Interest (Optional)")
        st.caption("Define specific locations for detailed output. You can skip this if not needed.")

        # Initialize POI list in session state
        if 'poi_list' not in st.session_state:
            st.session_state.poi_list = []

        # POI input form
        with st.form("add_poi_form"):
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                poi_name = st.text_input("Name", value="", placeholder="e.g., Station1")
            with col2:
                poi_x_new = st.number_input(f"Easting", value=0.0, format="%.2f", help=f"EPSG:{target_epsg}")
            with col3:
                poi_y_new = st.number_input(f"Northing", value=0.0, format="%.2f", help=f"EPSG:{target_epsg}")
            with col4:
                poi_z_new = st.number_input("Elevation (m)", value=0.0, format="%.2f")

            add_button = st.form_submit_button("Add POI", width="stretch")

            if add_button and poi_name:
                st.session_state.poi_list.append({
                    'name': poi_name,
                    'x': poi_x_new,
                    'y': poi_y_new,
                    'z': poi_z_new
                })
                st.rerun()

        # Display current POIs
        if st.session_state.poi_list:
            for idx, poi in enumerate(st.session_state.poi_list):
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.text(f"{poi['name']}: ({poi['x']:.2f}, {poi['y']:.2f}, {poi['z']:.2f})")
                with col2:
                    if st.button("Remove", key=f"remove_poi_{idx}", width="stretch"):
                        st.session_state.poi_list.pop(idx)
                        st.rerun()
        else:
            st.caption("No POIs added. This is optional.")

        st.divider()
        st.info("Continue to the next tab: **4. Run**")

    # ============================================================
    # Tab 4: Run (Other Locations)
    # ============================================================
    with tab4_other:
        st.header("Summary & Run")

        # Summary display
        col1, col2 = st.columns(2)

        with col1:
            st.metric("Simulation Name", simu_name_other if simu_name_other else "Not set")
            st.metric("Coordinate System", f"EPSG:{target_epsg}")
            st.metric("Grid Spacing", f"{gsd_other}m")

        with col2:
            dem_status = "Selected" if st.session_state.config.get('user_dem_path') else "Not selected"
            st.metric("DEM", dem_status)
            st.metric("Land Cover", f"Constant ({lus_constant_other})")
            st.metric("POIs", len(st.session_state.poi_list))

        st.divider()

        st.warning("**Meteorological Data**: After setup completes, manually add your SMET files to `output/{simulation_name}/input/meteo/`")

        st.divider()

        # Save config section
        st.subheader("Save Configuration")
        col1, col2 = st.columns([3, 1])

        with col1:
            save_config_name_other = st.text_input(
                "Config filename (without .ini)",
                value=simu_name_other if simu_name_other else "",
                key="save_config_name_other"
            )

        with col2:
            st.write("")
            st.write("")
            save_button_other = st.button("Save Config", type="secondary", width="stretch", key="save_button_other")

        if save_button_other:
            if not save_config_name_other:
                st.error("Please provide a config filename")
            else:
                # Create config file for Other Locations mode
                config_content = f"""# A3Dshell Configuration - Other Locations Mode
# Generated: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}

[GENERAL]
SIMULATION_NAME = {simu_name_other}

[INPUT]
DEM_MODE = user_provided
USER_DEM_PATH = {st.session_state.config.get('user_dem_path', '')}
TARGET_EPSG = {target_epsg}

[OUTPUT]
OUT_COORDSYS = EPSG:{target_epsg}
GSD = {gsd_other}
DEM_ADDFMTLIST =
MESH_FMT = vtu

[MAPS]
PLOT_HORIZON = false

[A3D]
USE_GROUNDEYE = false
LUS_SOURCE = {lus_source_other}
LUS_PREVAH_CST = {lus_constant_other}
DO_PVP_3D = false
PVP_3D_FMT = vtu
SP_BIN_PATH = input/bin/snowpack
"""
                # Save POIs to config
                if st.session_state.poi_list:
                    config_content += "\n[POIS]\n"
                    for poi in st.session_state.poi_list:
                        config_content += f"{poi['name']} = {poi['x']},{poi['y']},{poi['z']}\n"

                # Save file
                config_dir = Path("config")
                config_path = config_dir / f"{save_config_name_other}.ini"
                with open(config_path, 'w') as f:
                    f.write(config_content)

                st.success(f"Configuration saved to: {config_path}")

        st.divider()

        # Run section
        st.subheader("Run Setup")

        log_level_other = st.selectbox("Log Level", ["INFO", "DEBUG", "WARNING", "ERROR"], key="log_level_other")

        if st.button("Start Setup", type="primary", width="stretch", key="run_button_other"):
            if not simu_name_other:
                st.error("Please provide a simulation name")
            elif not st.session_state.config.get('user_dem_path'):
                st.error("Please select a DEM file")
            else:
                # Create temporary config for this run
                temp_config = Path("config") / f"_temp_{simu_name_other}.ini"

                config_content = f"""# Temporary A3Dshell Configuration - Other Locations
[GENERAL]
SIMULATION_NAME = {simu_name_other}

[INPUT]
DEM_MODE = user_provided
USER_DEM_PATH = {st.session_state.config.get('user_dem_path', '')}
TARGET_EPSG = {target_epsg}
"""

                config_content += "\n[OUTPUT]\n"
                config_content += f"OUT_COORDSYS = EPSG:{target_epsg}\n"
                config_content += "DEM_ADDFMTLIST =\n"
                config_content += "MESH_FMT = vtu\n"
                config_content += "\n[MAPS]\n"
                config_content += "PLOT_HORIZON = false\n"
                config_content += "\n[A3D]\n"
                config_content += "USE_GROUNDEYE = false\n"
                config_content += f"LUS_SOURCE = {lus_source_other}\n"
                config_content += f"LUS_PREVAH_CST = {lus_constant_other}\n"
                config_content += "DO_PVP_3D = false\n"
                config_content += "PVP_3D_FMT = vtu\n"
                config_content += "SP_BIN_PATH = input/bin/snowpack\n"

                # Add POIs
                if st.session_state.poi_list:
                    config_content += "\n[POIS]\n"
                    for poi in st.session_state.poi_list:
                        config_content += f"{poi['name']} = {poi['x']},{poi['y']},{poi['z']}\n"

                with open(temp_config, 'w') as f:
                    f.write(config_content)

                # Run command (no Snowpack preprocessing for Other Locations)
                cmd = [
                    "python", "-m", "src.cli",
                    "--config", str(temp_config),
                    "--log-level", log_level_other,
                    "--skip-snowpack"  # Always skip Snowpack for Other Locations
                ]

                # Run with real-time output streaming
                st.subheader("Run Log")
                log_container = st.container(height=400)
                log_placeholder = log_container.empty()
                full_log = []

                try:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    for line in process.stdout:
                        full_log.append(line.rstrip())
                        log_placeholder.code('\n'.join(full_log), language="text")

                    process.wait()

                    if process.returncode == 0:
                        st.success("✅ Setup completed successfully!")
                        st.info(f"**Next Steps**: Add your SMET meteorological files to `output/{simu_name_other}/input/meteo/`")
                    else:
                        st.error(f"❌ Setup failed with exit code {process.returncode}")

                except Exception as e:
                    st.error(f"❌ Error running setup: {str(e)}")

                finally:
                    # Clean up temp config
                    if temp_config.exists():
                        temp_config.unlink()

# Footer
st.divider()
st.markdown("""
<div style='text-align: center; color: #666; font-size: 0.9em;'>
    <p style='margin-bottom: 5px;'><strong>A3Dshell</strong> </p>
    <p style='margin: 5px 0;'>
        <a href='https://github.com/frischwood/A3Dshell' target='_blank' style='color: #0366d6; text-decoration: none;'>
            GitHub
        </a>
    </p>
    <p style='margin-top: 5px; font-size: 0.85em;'>
        © 2025 A3Dshell Contributors | Open Source Software
    </p>
</div>
""", unsafe_allow_html=True)
