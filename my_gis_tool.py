import streamlit as st
import pandas as pd
from geopy.geocoders import ArcGIS
from geopy.distance import geodesic
import io
import pydeck as pdk
import time
import re
import json
import geopandas as gpd
from shapely.geometry import Point, Polygon, shape

def is_vague_address(addr):
    addr = str(addr).upper().strip()
    
    if not addr: return True
    
    # --- COORDINATE BYPASS (VIP DOOR) ---
    if '°' in addr or re.search(r'^-?\d{2}\.\d+\s*,?\s*-?\d{2,3}\.\d+', addr):
        return False 
    
    # 1. PURE NUMBER GARBAGE & EXACT JUNK DATA
    if not re.search(r'[A-Z]', addr): return True
    if re.search(r'\b(PO BOX|P\.O\. BOX|P O BOX)\b', addr): return True
    
    # 2. THE NEW KILL-SWITCHES
    if re.search(r'^\s*\d+(ST|ND|RD|TH)\b', addr): return True
    if re.search(r'\b\d+(\.\d+)?\s*(ACRE|ACRES|MILE|MILES|MI\b|FT\b|FEET\b|YARD|YARDS|YDS\b)\b', addr): return True
    if re.search(r'\b(OVERPASS|UNDERPASS|OUTFALL|DITCH|TRIBUTARY|INTERCHANGE|TOLL PLAZA)\b', addr): return True
    
    # MULTIPLE ADDRESS CATCHER
    address_blocks = re.findall(r'\b\d+\s+[A-Z\s]+?\b(ST|AVE|RD|BLVD|DR|LN|WAY|PKWY|ROAD|STREET)\b', addr)
    if len(address_blocks) > 1: return True

    # 3. CORE ADDRESS ISOLATOR
    addr_core = addr
    chop_words = [' BTWN ', ' BETWEEN ', ' SE OF ', ' SW OF ', ' NE OF ', ' NW OF ', ' NORTH OF ', ' SOUTH OF ', ' EAST OF ', ' WEST OF ', ' N OF ', ' S OF ', ' E OF ', ' W OF ', ' FROM ', ' AT ', ' @ ', ' & ', ' AND ', ' / ']
    for cw in chop_words:
        if cw in addr_core:
            addr_core = addr_core.split(cw)[0].strip()

    addr_no_suites = re.sub(r'\b(SUITE|STE|UNIT|BLDG|BUILDING|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr_core)
    addr_no_suites = re.sub(r'#\s*[A-Z0-9-]+', '', addr_no_suites).strip()

    # 4. THE STRICT WHITELIST BOUNCER
    whitelist_regex = r'^\s*\d{1,6}[A-Z\-]*\s+[A-Z0-9\s\.\-]*?\b(ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|BLVE|DR|DRIVE|LN|LANE|WAY|PKWY|PARKWAY|HWY|HIGHWAY|PIKE|CIR|CIRCLE|CT|COURT|PL|PLACE|TRL|TRAIL|SQ|SQUARE|CORNERS|INDUSTRIAL|IND|US|I|IH|SH|FM|RM|TX|SR|CR|CO RD|COUNTY ROAD|PR|RTE|RT|SPUR|LOOP|INTERSTATE|EXPY|EXPRESSWAY|TRPK|TURNPIKE|BRIDGE|NORTH|SOUTH|EAST|WEST|N|S|E|W)\b'
    
    if re.search(whitelist_regex, addr_no_suites):
        return False 
        
    return True 

def clean_string(val):
    if pd.isna(val): return ""
    clean_val = str(val).strip()
    if clean_val.lower() == 'nan' or clean_val.lower() == 'none': return ""
    if clean_val.endswith('.0'): clean_val = clean_val[:-2]
    return " ".join(clean_val.split())

def scrub_address_for_arcgis(addr):
    addr = addr.upper()
    addr = addr.split('/')[0].split('(')[0].strip()
    
    chop_words = [' BTWN ', ' BETWEEN ', ' SE OF ', ' SW OF ', ' NE OF ', ' NW OF ', ' NORTH OF ', ' SOUTH OF ', ' EAST OF ', ' WEST OF ', ' N OF ', ' S OF ', ' E OF ', ' W OF ']
    for cw in chop_words:
        if cw in addr:
            addr = addr.split(cw)[0].strip()
            
    addr = re.sub(r'\b(INTERSECTION OF|CORNER OF|INTERSECTION|INT OF)\b\s*', '', addr)
    addr = re.sub(r'\b(EB|WB|NB|SB)\b', '', addr)
    addr = addr.replace(' AT ', ' AND ')
    addr = addr.replace(' @ ', ' AND ')
    addr = re.sub(r'\b(SUITE|STE|UNIT|BLDG|BUILDING|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr)
    addr = re.sub(r'#\s*[A-Z0-9-]+', '', addr)
    addr = re.sub(r'^(\d+)[A-Z]\b', r'\1', addr)
    addr = re.sub(r'\bINDUS\b', 'INDUSTRIAL', addr)
    addr = re.sub(r'\bCOUR\b', 'COURT', addr)
    return " ".join(addr.split())

# --- 3-TIER CITY SORTING LOGIC ---
def get_local_match_tier(row, t_city, t_county, t_state, t_zips_list):
    r_city = next((clean_string(row[c]).upper() for c in row.index if c in ['city', 'site city', 'site_city']), "")
    r_county = next((clean_string(row[c]).upper() for c in row.index if c in ['county', 'site county', 'site_county']), "")
    r_state = next((clean_string(row[c]).upper() for c in row.index if c in ['state', 'st', 'site state', 'site_state']), "")
    
    r_zip = ""
    for col in row.index:
        if 'zip' in str(col).lower():
            r_zip = clean_string(row[col])
            break

    is_local = False
    
    if r_zip and t_zips_list:
        for z in t_zips_list:
            if z in r_zip or r_zip in z:
                is_local = True
                break
                
    if not is_local and t_city and r_city and (t_city in r_city or r_city in t_city):
        is_local = True
        
    t_county_clean = t_county.replace(" COUNTY", "").strip() if t_county else ""
    r_county_clean = r_county.replace(" COUNTY", "").strip() if r_county else ""
    if not is_local and t_county_clean and r_county_clean and (t_county_clean in r_county_clean or r_county_clean in t_county_clean):
        is_local = True
        
    if not is_local and t_state and r_state and (t_state == r_state or t_state in r_state or r_state in t_state):
        is_local = True
        
    if not is_local:
        return 0 

    if t_city and r_city and (t_city in r_city or r_city in t_city):
        return 1 
    elif not r_city:
        return 2 
    else:
        return 3 

st.set_page_config(page_title="GIS Phase I ESA Agent", layout="wide", page_icon="📍")

if "run_complete" not in st.session_state:
    st.session_state.run_complete = False
    st.session_state.matches = []
    st.session_state.oob = []
    st.session_state.ngcs_local = []
    st.session_state.ngcs_outside = []
    st.session_state.blank_addrs = []

with st.sidebar:
    st.header("⚙️ Project Settings")
    st.divider()
    
    st.subheader("📍 Target Property Definition")
    tp_mode = st.radio("How do you want to define the site?", [
        "Single Point (Lat/Lon)", 
        "Polygon Area (Upload File)", 
        "Polygon Area (Paste GeoJSON)"
    ])
    
    site_lat, site_lon = 33.927600, -84.247200 
    tp_polygon = None
    
    if tp_mode == "Single Point (Lat/Lon)":
        site_lat = st.number_input("Site Latitude", format="%.6f", value=33.927600)
        site_lon = st.number_input("Site Longitude", format="%.6f", value=-84.247200)
        
    elif tp_mode == "Polygon Area (Upload File)":
        st.caption("Upload a .zip file containing your shapefile OR a .geojson file.")
        tp_file = st.file_uploader("Upload Area Polygon", type=["zip", "geojson"])
        if tp_file:
            try:
                tp_polygon_gdf = gpd.read_file(tp_file)
                tp_polygon_gdf = tp_polygon_gdf.to_crs(epsg=4326) 
                tp_polygon = tp_polygon_gdf.geometry.unary_union
                site_lat = tp_polygon.centroid.y
                site_lon = tp_polygon.centroid.x
                st.success("Polygon successfully loaded!")
            except Exception as e:
                st.error(f"Error reading shapefile. Details: {e}")
                
    elif tp_mode == "Polygon Area (Paste GeoJSON)":
        st.caption("Paste a raw GeoJSON dictionary or coordinate array here.")
        coord_input = st.text_area("GeoJSON Data:", height=150)
        if coord_input:
            try:
                data = json.loads(coord_input)
                
                if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                    data = data["features"][0]["geometry"]
                elif isinstance(data, dict) and data.get("type") == "Feature":
                    data = data["geometry"]
                
                if isinstance(data, dict) and "type" in data:
                    tp_polygon = shape(data)
                elif isinstance(data, list):
                    tp_polygon = Polygon(data[0]) if isinstance(data[0][0], list) else Polygon(data)
                
                if tp_polygon and tp_polygon.is_valid:
                    site_lat = tp_polygon.centroid.y
                    site_lon = tp_polygon.centroid.x
                    st.success("Coordinates successfully mapped into a Polygon!")
                else:
                    st.error("Data parsed, but it does not form a valid Polygon.")
            except json.JSONDecodeError:
                st.error("Invalid JSON format. Make sure you copied the full bracket sequence.")
            except Exception as e:
                st.error(f"Could not build polygon: {e}")

    search_radius = st.slider("Search Radius (Miles)", 0.1, 2.0, 0.5)
    
    st.divider()
    st.subheader("🏙️ Target Property Details")
    target_city = st.text_input("Target City").strip().upper()
    target_county = st.text_input("Target County").strip().upper()
    target_state = st.text_input("Target State").strip().upper()
    target_zips_input = st.text_input("Target Zip Code(s) (comma-separated)").strip()
    target_zips = [z.strip() for z in target_zips_input.split(',') if z.strip()]
    
    st.divider()
    st.subheader("🗺️ Mapping Overrides")
    force_state = st.text_input("Force State (e.g., 'TX')", value="")
    show_oob = st.checkbox("Show 'Out of Bounds' (Blue Dots)", value=True)

st.title("📍 Phase I ESA: Mapping Agent")
st.markdown("Automated sorting of **Mappable Sites**, **Local Orphans**, **Outside Orphans**, and **Blank Data**.")

uploaded_files = st.file_uploader("📂 Drop ESA Files Here (Excel/CSV)", type=["xlsx", "csv"], accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Run Analysis"):
        if "Polygon" in tp_mode and not tp_polygon:
            st.error("Please upload or paste your Polygon data, or switch back to Single Point mode.")
            st.stop()
            
        geolocator = ArcGIS()
        site_coords = (site_lat, site_lon)
        all_data = []

        for f in uploaded_files:
            try:
                if f.name.endswith('.csv'): df = pd.read_csv(f, dtype=str)
                else: df = pd.read_excel(f, dtype=str)
                df.columns = df.columns.str.strip().str.lower()
                
                addr_cols = [c for c in df.columns if 'address' in c or 'site_address' in c]
                if addr_cols:
                    df.rename(columns={addr_cols[0]: 'address'}, inplace=True)
                    all_data.append(df)
            except Exception as e:
                st.error(f"Could not read {f.name}: {e}")

        if all_data:
            master_df = pd.concat(all_data, ignore_index=True)
            matches, oob, ngcs_local, ngcs_outside, blank_addrs = [], [], [], [], []
            
            prog_bar = st.progress(0)
            status_text = st.empty()
            total_rows = len(master_df)

            for i, (index, row) in enumerate(master_df.iterrows()):
                prog_bar.progress((i + 1) / total_rows)
                status_text.text(f"Processing Record {i+1} of {total_rows}...")
                
                raw_addr = row.get('address', '')
                addr = clean_string(raw_addr).upper()
                
                if not addr:
                    row['status'] = "Unmappable"
                    row['reason'] = "Address field is blank/missing"
                    blank_addrs.append(row)
                    continue
                    
                is_junk = False
                junk_exact = ['GENERIC', 'UNKNOWN', 'VARIOUS', 'MULTIPLE', 'NONE', 'N/A', 'CITYWIDE', 'COUNTYWIDE', 'THROUGHOUT', 'TBD', 'PENDING', 'UNNAMED', 'NO ADDRESS']
                
                if addr in junk_exact:
                    is_junk = True
                elif any(addr.startswith(prefix) for prefix in ['COVERS ALL AREAS', 'VARIOUS LOCATIONS', 'MULTIPLE LOCATIONS', 'NO PHYSICAL ADDRESS']):
                    is_junk = True
                    
                if is_junk:
                    row['status'] = "Unmappable"
                    row['reason'] = "Junk/Filler Data"
                    blank_addrs.append(row)
                    continue
                
                if is_vague_address(addr):
                    row['status'] = "NGC (Orphan)"
                    row['reason'] = "Vague Description / Failed Whitelist"
                    
                    match_tier = get_local_match_tier(row, target_city, target_county, target_state, target_zips)
                    if match_tier > 0:
                        row['local_sort_tier'] = match_tier
                        ngcs_local.append(row)
                    else:
                        ngcs_outside.append(row)
                    continue 

                scrubbed_addr = scrub_address_for_arcgis(addr)
                full_search_address = scrubbed_addr
                
                if force_state:
                    full_search_address += f", {force_state}"
                else:
                    city = next((clean_string(row[c]) for c in row.index if c in ['city', 'site city', 'site_city']), "")
                    county = next((clean_string(row[c]) for c in row.index if c in ['county', 'site county', 'site_county']), "")
                    state = next((clean_string(row[c]) for c in row.index if c in ['state', 'st', 'site state']), "")
                    
                    zip_code = ""
                    for c in row.index:
                        if 'zip' in c:
                            zip_code = clean_string(row[c])
                            break
                    
                    if city: full_search_address += f", {city}"
                    if county: full_search_address += f", {county} County"
                    if state: full_search_address += f", {state}"
                    if zip_code: full_search_address += f" {zip_code}"

                try:
                    loc = geolocator.geocode(full_search_address, timeout=10)
                    if loc:
                        if "Polygon" not in tp_mode:
                            dist = geodesic(site_coords, (loc.latitude, loc.longitude)).miles
                        else:
                            site_pt = Point(loc.longitude, loc.latitude)
                            local_crs = f"+proj=aeqd +lat_0={loc.latitude} +lon_0={loc.longitude} +datum=WGS84 +units=m"
                            
                            poly_proj = gpd.GeoSeries([tp_polygon], crs="EPSG:4326").to_crs(local_crs).iloc[0]
                            pt_proj = gpd.GeoSeries([site_pt], crs="EPSG:4326").to_crs(local_crs).iloc[0]
                            
                            dist_meters = poly_proj.distance(pt_proj)
                            dist = dist_meters * 0.000621371 
                        
                        row['mapped_lat'] = loc.latitude
                        row['mapped_lon'] = loc.longitude
                        row['miles_from_site'] = round(dist, 3)
                        row['arcgis_address'] = loc.address
                        row['search_string_used'] = full_search_address
                        
                        if dist <= search_radius:
                            row['status'] = "Match"
                            matches.append(row)
                        else:
                            row['status'] = "Out of Bounds"
                            oob.append(row)
                    else:
                        row['status'] = "NGC (Not Found)"
                        row['reason'] = "Address not found by ArcGIS"
                        
                        match_tier = get_local_match_tier(row, target_city, target_county, target_state, target_zips)
                        if match_tier > 0:
                            row['local_sort_tier'] = match_tier
                            ngcs_local.append(row)
                        else:
                            ngcs_outside.append(row)
                except Exception as e:
                    row['status'] = "Error"
                    row['reason'] = str(e)
                    match_tier = get_local_match_tier(row, target_city, target_county, target_state, target_zips)
                    if match_tier > 0:
                        row['local_sort_tier'] = match_tier
                        ngcs_local.append(row)
                    else:
                        ngcs_outside.append(row)
                
                time.sleep(0.1)

            prog_bar.empty()
            status_text.empty()
            
            st.session_state.matches = matches
            st.session_state.oob = oob
            st.session_state.ngcs_local = ngcs_local
            st.session_state.ngcs_outside = ngcs_outside
            st.session_state.blank_addrs = blank_addrs
            st.session_state.run_complete = True
            
            if "Polygon" in tp_mode:
                st.session_state.tp_polygon = tp_polygon

if st.session_state.run_complete:
    matches = st.session_state.matches
    oob = st.session_state.oob
    ngcs_local = st.session_state.ngcs_local
    ngcs_outside = st.session_state.ngcs_outside
    blank_addrs = st.session_state.blank_addrs
    tp_polygon_saved = st.session_state.get('tp_polygon', None)

    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("✅ Matches", len(matches))
    c2.metric("⚠️ Out of Bounds", len(oob))
    c3.metric("🟡 Local NGCs", len(ngcs_local))
    c4.metric("❌ Outside NGCs", len(ngcs_outside))
    c5.metric("🗑️ Blank", len(blank_addrs))

    st.subheader("🗺️ Site Map")
    
    search_site_id = st.text_input("🔍 Search Map by Site ID (Optional)")
    
    map_center_lat = site_lat
    map_center_lon = site_lon
    map_zoom = 13
    highlight_layer = None

    if search_site_id:
        found = False
        for r in matches + oob:
            sid1 = str(r.get('site_id', '')).strip()
            sid2 = str(r.get('site id', '')).strip()
            if search_site_id.strip() in [sid1, sid2] and search_site_id.strip() != "":
                map_center_lat = r['mapped_lat']
                map_center_lon = r['mapped_lon']
                map_zoom = 17 
                highlight_layer = pdk.Layer(
                    'ScatterplotLayer',
                    data=pd.DataFrame([{'lat': map_center_lat, 'lon': map_center_lon, 'address': r.get('address', '')}]),
                    get_position='[lon, lat]',
                    get_color='[255, 255, 0, 255]', 
                    get_radius=40,
                    pickable=True
                )
                found = True
                break
        if not found:
            st.warning(f"Could not find a mapped site with ID: '{search_site_id}'. It might be an Orphan.")

    layers = []
    
    if "Polygon" not in tp_mode:
        radius_in_meters = search_radius * 1609.34 
        layers.append(pdk.Layer(
            'ScatterplotLayer',
            data=pd.DataFrame([{'lat': site_lat, 'lon': site_lon}]),
            get_position='[lon, lat]',
            get_color='[255, 0, 0, 30]', 
            get_radius=radius_in_meters,
            pickable=False
        ))
        layers.append(pdk.Layer(
            'ScatterplotLayer',
            data=pd.DataFrame([{'lat': site_lat, 'lon': site_lon}]),
            get_position='[lon, lat]',
            get_color='[255, 0, 0, 255]', 
            get_radius=120,
            pickable=False
        ))
    elif tp_polygon_saved:
        tp_geojson = gpd.GeoSeries([tp_polygon_saved]).__geo_interface__
        layers.append(pdk.Layer(
            'GeoJsonLayer',
            tp_geojson,
            stroked=True,
            filled=True,
            get_fill_color=[255, 255, 0, 100],
            get_line_color=[255, 255, 0, 255],
            get_line_width=3,
        ))
        
        metric_crs = f"+proj=aeqd +lat_0={map_center_lat} +lon_0={map_center_lon} +datum=WGS84 +units=m"
        tp_proj_for_buffer = gpd.GeoSeries([tp_polygon_saved], crs="EPSG:4326").to_crs(metric_crs)
        buffer_proj = tp_proj_for_buffer.buffer(search_radius * 1609.34)
        buffer_geojson = buffer_proj.to_crs(epsg=4326).__geo_interface__
        
        layers.append(pdk.Layer(
            'GeoJsonLayer',
            buffer_geojson,
            stroked=True,
            filled=True,
            get_fill_color=[255, 0, 0, 30],
            get_line_color=[255, 0, 0, 150],
            line_width_min_pixels=2,
        ))
    
    if matches:
        layers.append(pdk.Layer(
            'ScatterplotLayer',
            data=pd.DataFrame(matches),
            get_position='[mapped_lon, mapped_lat]',
            get_color='[0, 200, 0, 200]', 
            get_radius=80,
            pickable=True
        ))
    
    if show_oob and oob:
        layers.append(pdk.Layer(
            'ScatterplotLayer',
            data=pd.DataFrame(oob),
            get_position='[mapped_lon, mapped_lat]',
            get_color='[0, 100, 255, 150]', 
            get_radius=60,
            pickable=True
        ))
        
    if highlight_layer:
        layers.append(highlight_layer)

    view_state = pdk.ViewState(latitude=map_center_lat, longitude=map_center_lon, zoom=map_zoom)
    
    st.pydeck_chart(pdk.Deck(
        map_style=None, 
        initial_view_state=view_state,
        layers=layers,
        tooltip={"text": "{address}\nDistance: {miles_from_site} mi\nStatus: {status}\nSite ID: {site_id}"}
    ))
    
    if matches:
        st.subheader("✅ Mapped Sites (Within Radius)")
        df_matches = pd.DataFrame(matches)
        display_cols_matches = ['address', 'miles_from_site', 'mapped_lat', 'mapped_lon']
        for col in ['site_name', 'site name', 'site id', 'site_id', 'city', 'county', 'state', 'st']:
            if col in df_matches.columns: display_cols_matches.insert(0, col)
        display_cols_matches = list(dict.fromkeys(display_cols_matches))
        st.dataframe(df_matches.sort_values(by='miles_from_site')[display_cols_matches], use_container_width=True)

    if oob:
        st.subheader("⚠️ Out of Bounds (Beyond Search Radius)")
        df_oob = pd.DataFrame(oob)
        display_cols_oob = ['address', 'miles_from_site', 'mapped_lat', 'mapped_lon']
        for col in ['site_name', 'site name', 'site id', 'site_id', 'city', 'county', 'state', 'st']:
            if col in df_oob.columns: display_cols_oob.insert(0, col)
        display_cols_oob = list(dict.fromkeys(display_cols_oob))
        st.dataframe(df_oob.sort_values(by='miles_from_site')[display_cols_oob], use_container_width=True)

    if ngcs_local:
        st.subheader("🟡 Local Orphans (City, County, State, or Zip Matches)")
        df_ngc_local = pd.DataFrame(ngcs_local)
        
        if 'local_sort_tier' in df_ngc_local.columns:
            df_ngc_local = df_ngc_local.sort_values(by='local_sort_tier')
        
        display_cols_local = ['address', 'reason']
        # --- ADDED SITE_NAME HERE ---
        for col in ['site_name', 'site name', 'site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_ngc_local.columns: display_cols_local.insert(-2, col)
        st.dataframe(df_ngc_local[list(dict.fromkeys(display_cols_local))], use_container_width=True)

    if ngcs_outside:
        st.subheader("❌ Outside Orphans (No Location Match)")
        df_ngc_outside = pd.DataFrame(ngcs_outside)
        display_cols_outside = ['address', 'reason']
        # --- ADDED SITE_NAME HERE ---
        for col in ['site_name', 'site name', 'site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_ngc_outside.columns: display_cols_outside.insert(-2, col)
        st.dataframe(df_ngc_outside[list(dict.fromkeys(display_cols_outside))], use_container_width=True)
        
    if blank_addrs:
        st.subheader("🗑️ Blank Addresses (Unmappable)")
        df_blanks = pd.DataFrame(blank_addrs)
        display_cols_blanks = ['address', 'reason']
        # --- ADDED SITE_NAME HERE ---
        for col in ['site_name', 'site name', 'site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_blanks.columns: display_cols_blanks.insert(-2, col)
        st.dataframe(df_blanks[list(dict.fromkeys(display_cols_blanks))], use_container_width=True)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if matches: pd.DataFrame(matches).to_excel(writer, sheet_name="Matches", index=False)
        if oob: pd.DataFrame(oob).to_excel(writer, sheet_name="Out_of_Bounds", index=False)
        
        if ngcs_local: 
            df_export_local = pd.DataFrame(ngcs_local)
            if 'local_sort_tier' in df_export_local.columns:
                df_export_local = df_export_local.sort_values(by='local_sort_tier').drop(columns=['local_sort_tier'])
            df_export_local.to_excel(writer, sheet_name="Local_Orphans", index=False)
            
        if ngcs_outside: pd.DataFrame(ngcs_outside).to_excel(writer, sheet_name="Outside_Orphans", index=False)
        if blank_addrs: pd.DataFrame(blank_addrs).to_excel(writer, sheet_name="Blank_Addresses", index=False)
    
    st.success("Analysis Complete!")
    st.download_button("📥 Download Final Excel Report", output.getvalue(), "ESA_Final_Report.xlsx")
