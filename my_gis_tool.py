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
    
    # 1. PURE NUMBER GARBAGE FILTER
    if not re.search(r'[A-Z]', addr):
        return True
    
    # 2. CHAINED INTERSECTIONS & LIST FILTER
    if sum(addr.count(x) for x in [' & ', ' AND ', ' @ ', ' AT ']) > 1:
        return True
    
    # --- NEW: MULTIPLE ADDRESS CATCHER ---
    # Catches lists of addresses mashed together (e.g., "1000 MAIN ST, 50 W TOWN ST")
    address_blocks = re.findall(r'\b\d+\s+[A-Z\s]+?\b(ST|AVE|RD|BLVD|DR|LN|WAY|PKWY|ROAD|STREET)\b', addr)
    if len(address_blocks) > 1:
        return True
    
    street_suffixes = [' RD', ' ST', ' AVE', ' BLVD', ' DR', ' LN', ' WAY', ' PKWY', ' HWY', ' PIKE', ' ROAD', ' STREET']
    has_street = any(suffix in addr for suffix in street_suffixes)
    
    # 3. DATE CATCHER 
    date_fragment = re.search(r'\b\d{1,2}\s*,\s*(?:19|20)\d{2}\b', addr)
    date_slashes = re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', addr)
    date_months = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+\d{1,2}\b', addr)
    
    if (date_fragment or date_slashes or date_months) and not has_street:
        return True
    
    # 4. Distance markers
    if re.search(r'\b\d*\.?\d+\s*(MILE|MILES|MI\b|FT\b|FEET\b)', addr) or re.search(r'\b(MILE|MILES|MI\b|FT\b|FEET\b)\s*\d+(\.\d+)?', addr): 
        return True
        
    # 5. Strict Directional Routing 
    if re.search(r'\b(N|S|E|W|NW|NE|SW|SE|NORTH|SOUTH|EAST|WEST|NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST)\s+(OF|CORNER|C/O|INTERSECTION|SIDE|END|PORTION)\b', addr):
        return True
    if re.search(r'\b(NWC|NEC|SWC|SEC)\b', addr):
        return True
        
    # 6. Universal Box & Rural Route Catcher
    if re.search(r'\bBOX\s*(?:#|NO\.?)?\s*\d+[A-Z]*\b', addr):
        return True
    if re.search(r'\b(RR|RURAL ROUTE|ROUTE|ROUTH|RT)\s*\d+.*\bBOX\b', addr):
        return True
        
    if re.search(r'\b(LAT|LONG|LATITUDE|LONGITUDE)\s*:?\s*\d+', addr):
        return True
        
    jargon_terms = ['CONTROL SECTION', 'LOG MILE', 'LOGMILE', ' N LONG', ' W LAT', 'MILEPOST', 'MILE POST']
    if any(term in addr for term in jargon_terms):
        return True
    
    if re.search(r'\b(NEAR|ADJACENT|BEHIND|VICINITY|APPROX|PO BOX|P\.O\. BOX|P O BOX|P\.O\.BOX|EB|WB|NB|SB|EXIT|EXI|ON RAMP|OFF RAMP|LOCATED|SITUATED)\b', addr):
        return True
        
    # UPGRADED: Added PENINSULA, PARK, and TEST
    facility_regex = r'\b(AIRPORT|AFB|BASE|CAMPUS|PORT|PIER|TERMINAL|WELL|PUMP STATION|LIFT STATION|SUBSTATION|PIPELINE|OUTFALL|TANK|LEASE|MINE|PIT|QUARRY|FACILITY|PLANT|ANCHORAGE|UST|AST|LUST|SWMU|AOC|PENINSULA|PARK|TEST)\b'
    if re.search(facility_regex, addr) and not has_street:
        return True

    legal_regex = r'\b(ACRE|ACRES|SURVEY|ABSTRACT|ABS|TRACT|PARCEL|LOT|BLOCK|SECT|SECTION)\b'
    if re.search(legal_regex, addr) and not has_street:
        return True

    if re.match(r'^#?\s*[A-Z0-9]+-[A-Z0-9]+', addr) and not has_street:
        return True

    # --- THE CORE ADDRESS ISOLATOR ---
    addr_core = addr
    chop_words = [' BTWN ', ' BETWEEN ', ' SE OF ', ' SW OF ', ' NE OF ', ' NW OF ', ' NORTH OF ', ' SOUTH OF ', ' EAST OF ', ' WEST OF ', ' N OF ', ' S OF ', ' E OF ', ' W OF ', ' FROM ']
    for cw in chop_words:
        if cw in addr_core:
            addr_core = addr_core.split(cw)[0].strip()

    addr_no_suites = re.sub(r'\b(SUITE|STE|UNIT|BLDG|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr_core)
    addr_no_suites = re.sub(r'#\s*[A-Z0-9-]+', '', addr_no_suites)

    # HIGHWAY FILTER
    addr_without_hwy = re.sub(r'\b([A-Z]{2}|HWY|HIGHWAY|US|I\s*-?|SR|ROUTE|ROUTH|RR|STATE ROUTE|COUNTY ROAD|USR|CR|PR|INTERSTATE|INT|RTE|RT)\s*\d+[A-Z0-9\-]*\b', '', addr_no_suites)
    addr_without_ordinals = re.sub(r'\b\d+(ST|ND|RD|TH)\b', '', addr_without_hwy)
    
    addr_without_zips = re.sub(r'\b\d{5}(?:-\d{4})?\s*$', '', addr_without_ordinals)
    
    # --- UPGRADED FAKE INTERSECTION BLOCKER ---
    # Now requires the string to actually have a street suffix to count as an intersection
    is_intersection = any(x in addr_core for x in [' & ', ' AND ', ' @ ', ' AT ']) and has_street
    has_real_number = any(char.isdigit() for char in addr_without_zips)
    
    if not has_real_number and not is_intersection:
        return True 
        
    return False

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
    
    addr = re.sub(r'\b(SUITE|STE|UNIT|BLDG|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr)
    addr = re.sub(r'#\s*[A-Z0-9-]+', '', addr)
    addr = re.sub(r'^(\d+)[A-Z]\b', r'\1', addr)
    addr = re.sub(r'\bINDUS\b', 'INDUSTRIAL', addr)
    addr = re.sub(r'\bCOUR\b', 'COURT', addr)
    return " ".join(addr.split())

def is_local_ngc(row, t_city, t_county, t_state, t_zips_list):
    if not t_city and not t_county and not t_state and not t_zips_list:
        return False
        
    r_city = next((clean_string(row[c]).upper() for c in row.index if c in ['city', 'site city', 'site_city']), "")
    r_county = next((clean_string(row[c]).upper() for c in row.index if c in ['county', 'site county', 'site_county']), "")
    r_state = next((clean_string(row[c]).upper() for c in row.index if c in ['state', 'st', 'site state', 'site_state']), "")
    
    r_zip = ""
    for col in row.index:
        if 'zip' in str(col).lower():
            r_zip = clean_string(row[col])
            break

    t_county_clean = t_county.replace(" COUNTY", "").strip()
    r_county_clean = r_county.replace(" COUNTY", "").strip()

    if t_city and r_city and (t_city in r_city or r_city in t_city): return True
    if t_county_clean and r_county_clean and (t_county_clean in r_county_clean or r_county_clean in t_county_clean): return True
    if t_state and r_state and (t_state == r_state or t_state in r_state or r_state in t_state): return True
    
    if r_zip and t_zips_list:
        for z in t_zips_list:
            if z in r_zip or r_zip in z:
                return True
    
    return False

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
    st.subheader("📍 Target Property Coordinates")
    site_lat = st.number_input("Site Latitude", format="%.6f", value=33.927600)
    site_lon = st.number_input("Site Longitude", format="%.6f", value=-84.247200)
    search_radius = st.slider("Search Radius (Miles)", 0.1, 2.0, 0.5)
    
    st.divider()
    st.subheader("🏙️ Target Property Details")
    st.caption("Used to sort Orphans into Local vs. Outside.")
    target_city = st.text_input("Target City").strip().upper()
    target_county = st.text_input("Target County (e.g., Guadalupe)").strip().upper()
    target_state = st.text_input("Target State (e.g., TX)").strip().upper()
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
                
                if is_vague_address(addr):
                    row['status'] = "NGC (Orphan)"
                    row['reason'] = "Vague Description / Missing Number"
                    if is_local_ngc(row, target_city, target_county, target_state, target_zips):
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
                        if is_local_ngc(row, target_city, target_county, target_state, target_zips):
                            ngcs_local.append(row)
                        else:
                            ngcs_outside.append(row)
                except Exception as e:
                    row['status'] = "Error"
                    row['reason'] = str(e)
                    if is_local_ngc(row, target_city, target_county, target_state, target_zips):
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

if st.session_state.run_complete:
    matches = st.session_state.matches
    oob = st.session_state.oob
    ngcs_local = st.session_state.ngcs_local
    ngcs_outside = st.session_state.ngcs_outside
    blank_addrs = st.session_state.blank_addrs

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
    
    # --- 4. RESULTS TABLES ---
    if matches:
        st.subheader("✅ Mapped Sites (Within Radius)")
        df_matches = pd.DataFrame(matches)
        
        display_cols_matches = ['address', 'miles_from_site', 'mapped_lat', 'mapped_lon']
        for col in ['site_name', 'site name', 'site id', 'site_id', 'city', 'county', 'state', 'st']:
            if col in df_matches.columns: display_cols_matches.insert(0, col)
            
        display_cols_matches = list(dict.fromkeys(display_cols_matches))
        st.dataframe(df_matches.sort_values(by='miles_from_site')[display_cols_matches], use_container_width=True)

    if ngcs_local:
        st.subheader("🟡 Local Orphans (City, County, State, or Zip Matches)")
        df_ngc_local = pd.DataFrame(ngcs_local)
        display_cols_local = ['address', 'reason']
        for col in ['site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_ngc_local.columns: display_cols_local.insert(-2, col)
        st.dataframe(df_ngc_local[list(dict.fromkeys(display_cols_local))], use_container_width=True)

    if ngcs_outside:
        st.subheader("❌ Outside Orphans (No Location Match)")
        df_ngc_outside = pd.DataFrame(ngcs_outside)
        display_cols_outside = ['address', 'reason']
        for col in ['site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_ngc_outside.columns: display_cols_outside.insert(-2, col)
        st.dataframe(df_ngc_outside[list(dict.fromkeys(display_cols_outside))], use_container_width=True)
        
    if blank_addrs:
        st.subheader("🗑️ Blank Addresses (Unmappable)")
        df_blanks = pd.DataFrame(blank_addrs)
        display_cols_blanks = ['address', 'reason']
        for col in ['site id', 'site_id', 'city', 'county', 'state', 'st', 'zip', 'zipcode']:
            if col in df_blanks.columns: display_cols_blanks.insert(-2, col)
        st.dataframe(df_blanks[list(dict.fromkeys(display_cols_blanks))], use_container_width=True)

    # --- 5. EXPORT ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if matches: pd.DataFrame(matches).to_excel(writer, sheet_name="Matches", index=False)
        if oob: pd.DataFrame(oob).to_excel(writer, sheet_name="Out_of_Bounds", index=False)
        if ngcs_local: pd.DataFrame(ngcs_local).to_excel(writer, sheet_name="Local_Orphans", index=False)
        if ngcs_outside: pd.DataFrame(ngcs_outside).to_excel(writer, sheet_name="Outside_Orphans", index=False)
        if blank_addrs: pd.DataFrame(blank_addrs).to_excel(writer, sheet_name="Blank_Addresses", index=False)
    
    st.success("Analysis Complete!")
    st.download_button("📥 Download Final Excel Report", output.getvalue(), "ESA_Final_Report.xlsx")
