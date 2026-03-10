import streamlit as st
import pandas as pd
from geopy.geocoders import ArcGIS
from geopy.distance import geodesic
import io
import pydeck as pdk
import time
import re

def is_vague_address(addr):
    addr = str(addr).upper().strip()
    
    if not addr: return True
    
    # 1. Catch distance descriptions like "0.54 miles from"
    if re.search(r'\d+(\.\d+)?\s*MILE', addr): 
        return True
    
    # 2. Catch directional vagueness and PO Boxes
    vague_terms = [
        'NORTH OF', 'SOUTH OF', 'EAST OF', 'WEST OF',
        'NEAR ', 'ADJACENT', 'BEHIND ', 'VICINITY', 'APPROX ',
        'PO BOX', 'P.O. BOX', 'P O BOX'
    ]
    if any(term in addr for term in vague_terms): 
        return True
    
    # 3. MUST contain a number OR be an intersection
    is_intersection = any(x in addr for x in [' & ', ' AND ', '@'])
    has_number = any(char.isdigit() for char in addr)
    
    if not has_number and not is_intersection:
        return True # e.g., "N BRIDGE ST" or "MAIN STREET" becomes NGC
        
    return False

def clean_string(val):
    if pd.isna(val): return ""
    clean_val = str(val).strip()
    if clean_val.lower() == 'nan': return ""
    if clean_val.endswith('.0'): clean_val = clean_val[:-2]
    return " ".join(clean_val.split())

def scrub_address_for_arcgis(addr):
    """Aggressively cleans addresses so ArcGIS doesn't choke on them."""
    addr = addr.upper()
    # Strip Suites, Units, Apartments, and Building numbers
    addr = re.sub(r'\b(SUITE|STE|UNIT|BLDG|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr)
    addr = re.sub(r'#\s*[A-Z0-9-]+', '', addr)
    # Strip letters directly attached to street numbers (e.g., "5105B" -> "5105")
    addr = re.sub(r'^(\d+)[A-Z]\b', r'\1', addr)
    # Fix common database typos
    addr = re.sub(r'\bINDUS\b', 'INDUSTRIAL', addr)
    addr = re.sub(r'\bCOUR\b', 'COURT', addr)
    return " ".join(addr.split())

st.set_page_config(page_title="GIS Phase I ESA Agent", layout="wide", page_icon="📍")

# --- INITIALIZE MEMORY ---
if "run_complete" not in st.session_state:
    st.session_state.run_complete = False
    st.session_state.matches = []
    st.session_state.oob = []
    st.session_state.ngcs = []

# --- 1. SIDEBAR INPUTS ---
with st.sidebar:
    st.header("⚙️ Project Settings")
    
    st.divider()
    st.subheader("📍 Target Property")
    site_lat = st.number_input("Site Latitude", format="%.6f", value=33.927600)
    site_lon = st.number_input("Site Longitude", format="%.6f", value=-84.247200)
    search_radius = st.slider("Search Radius (Miles)", 0.1, 2.0, 0.5)
    
    st.divider()
    st.subheader("🗺️ Address Settings")
    force_state = st.text_input("Force State/City (e.g., 'TX' or 'Dallas, TX')", value="")
    show_oob = st.checkbox("Show 'Out of Bounds' (Blue Dots)", value=True)

st.title("📍 Phase I ESA: Mapping Agent")
st.markdown("Automated sorting of **Mappable Sites** vs. **Orphans (NGCs)**.")

uploaded_files = st.file_uploader("📂 Drop ESA Files Here (Excel/CSV)", type=["xlsx", "csv"], accept_multiple_files=True)

# --- 2. MAIN ANALYSIS ENGINE ---
if uploaded_files:
    if st.button("🚀 Run Analysis"):
        geolocator = ArcGIS()
        site_coords = (site_lat, site_lon)
        all_data = []

        for f in uploaded_files:
            try:
                if f.name.endswith('.csv'): df = pd.read_csv(f)
                else: df = pd.read_excel(f)
                df.columns = df.columns.str.strip().str.lower()
                
                addr_cols = [c for c in df.columns if 'address' in c or 'site_address' in c]
                if addr_cols:
                    df.rename(columns={addr_cols[0]: 'address'}, inplace=True)
                    all_data.append(df)
            except Exception as e:
                st.error(f"Could not read {f.name}: {e}")

        if all_data:
            master_df = pd.concat(all_data, ignore_index=True)
            matches, oob, ngcs = [], [], []
            
            prog_bar = st.progress(0)
            status_text = st.empty()
            total_rows = len(master_df)

            for i, (index, row) in enumerate(master_df.iterrows()):
                prog_bar.progress((i + 1) / total_rows)
                status_text.text(f"Processing Record {i+1} of {total_rows}...")
                
                raw_addr = row.get('address', '')
                addr = clean_string(raw_addr)
                
                # Chop off inspector notes (e.g., /SOUTH OF GARAGE)
                addr = addr.split('/')[0].split('(')[0].strip()
                
                # 1. ORPHAN FILTER
                if is_vague_address(addr):
                    row['status'] = "NGC (Orphan)"
                    row['reason'] = "Vague Description / Missing Number"
                    ngcs.append(row)
                    continue 

                # 2. SCRUB ADDRESS
                scrubbed_addr = scrub_address_for_arcgis(addr)
                full_search_address = scrubbed_addr
                
                if force_state:
                    full_search_address += f", {force_state}"
                else:
                    # Dynamically find City, County, State, Zip columns
                    city = next((clean_string(row[c]) for c in row.index if c in ['city', 'site city', 'site_city']), "")
                    county = next((clean_string(row[c]) for c in row.index if c in ['county', 'site county', 'site_county']), "")
                    state = next((clean_string(row[c]) for c in row.index if c in ['state', 'st', 'site state']), "")
                    zip_code = next((clean_string(row[c]) for c in row.index if 'zip' in c), "")
                    
                    if city: full_search_address += f", {city}"
                    if county: full_search_address += f", {county} County"
                    if state: full_search_address += f", {state}"
                    if zip_code: full_search_address += f" {zip_code}"

                # 3. GEOCODE
                try:
                    loc = geolocator.geocode(full_search_address, timeout=10)
                    if loc:
                        found_coords = (loc.latitude, loc.longitude)
                        dist = geodesic(site_coords, found_coords).miles
                        
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
                        ngcs.append(row)
                except Exception as e:
                    row['status'] = "Error"
                    row['reason'] = str(e)
                    ngcs.append(row)
                
                time.sleep(0.1)

            prog_bar.empty()
            status_text.empty()
            
            # Save to memory so user can search later without reloading
            st.session_state.matches = matches
            st.session_state.oob = oob
            st.session_state.ngcs = ngcs
            st.session_state.run_complete = True

# --- 3. RESULTS DISPLAY & INTERACTIVE MAP ---
if st.session_state.run_complete:
    matches = st.session_state.matches
    oob = st.session_state.oob
    ngcs = st.session_state.ngcs

    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("✅ Matches (Within Radius)", len(matches))
    c2.metric("⚠️ Out of Bounds", len(oob))
    c3.metric("❌ Orphans (NGCs)", len(ngcs))

    st.subheader("🗺️ Site Map")
    
    # --- SITE ID SEARCH FEATURE ---
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
                map_zoom = 17 # Zoom in tight
                # Create a big yellow highlight dot
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

    # --- MAP LAYERS ---
    layers = []
    
    # 1. VISUAL BUFFER RING (Translucent Red)
    radius_in_meters = search_radius * 1609.34 # Convert miles to meters
    layers.append(pdk.Layer(
        'ScatterplotLayer',
        data=pd.DataFrame([{'lat': site_lat, 'lon': site_lon}]),
        get_position='[lon, lat]',
        get_color='[255, 0, 0, 30]', 
        get_radius=radius_in_meters,
        pickable=False
    ))
    
    # 2. Target Property Dot
    layers.append(pdk.Layer(
        'ScatterplotLayer',
        data=pd.DataFrame([{'lat': site_lat, 'lon': site_lon}]),
        get_position='[lon, lat]',
        get_color='[255, 0, 0, 255]', 
        get_radius=120,
        pickable=False
    ))
    
    # 3. Matches (Green)
    if matches:
        layers.append(pdk.Layer(
            'ScatterplotLayer',
            data=pd.DataFrame(matches),
            get_position='[mapped_lon, mapped_lat]',
            get_color='[0, 200, 0, 200]', 
            get_radius=80,
            pickable=True
        ))
    
    # 4. Out of Bounds (Blue)
    if show_oob and oob:
        layers.append(pdk.Layer(
            'ScatterplotLayer',
            data=pd.DataFrame(oob),
            get_position='[mapped_lon, mapped_lat]',
            get_color='[0, 100, 255, 150]', 
            get_radius=60,
            pickable=True
        ))
        
    # 5. Add Yellow Highlight if searched
    if highlight_layer:
        layers.append(highlight_layer)

    view_state = pdk.ViewState(latitude=map_center_lat, longitude=map_center_lon, zoom=map_zoom)
    
    st.pydeck_chart(pdk.Deck(
        map_style=None, 
        initial_view_state=view_state,
        layers=layers,
        tooltip={"text": "{address}\nDistance: {miles_from_site} mi\nStatus: {status}\nSite ID: {site_id}"}
    ))

    # --- 4. NGC TABLE ---
    if ngcs:
        st.subheader("❌ Orphan (NGC) List")
        df_ngc = pd.DataFrame(ngcs)
        display_cols = ['address', 'reason']
        for col in ['site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_ngc.columns: display_cols.insert(-2, col)
            
        st.dataframe(df_ngc[list(dict.fromkeys(display_cols))], use_container_width=True)

    # --- 5. EXPORT ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if matches: pd.DataFrame(matches).to_excel(writer, sheet_name="Matches", index=False)
        if oob: pd.DataFrame(oob).to_excel(writer, sheet_name="Out_of_Bounds", index=False)
        if ngcs: pd.DataFrame(ngcs).to_excel(writer, sheet_name="Orphans_NGC", index=False)
    
    st.success("Analysis Complete!")
    st.download_button("📥 Download Final Excel Report", output.getvalue(), "ESA_Final_Report.xlsx")
