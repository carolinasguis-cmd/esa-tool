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
    
    # 1. Catch distance descriptions
    if re.search(r'\b\d+(\.\d+)?\s*(MILE|MI\b|FT\b|FEET\b)', addr) or re.search(r'\b(MILE|MI\b|FT\b|FEET\b)\s*\d+(\.\d+)?', addr): 
        return True
        
    # 2. Universal Box Catcher
    if re.search(r'\bBOX\s*\d+\b', addr):
        return True
        
    # 3. DOT Jargon & Coordinate Filter
    if re.search(r'\b(LAT|LONG|LATITUDE|LONGITUDE)\s*:?\s*\d+', addr):
        return True
    jargon_terms = ['CONTROL SECTION', 'LOG MILE', 'LOGMILE', ' N LONG', ' W LAT']
    if any(term in addr for term in jargon_terms):
        return True
    
    # 4. Catch directional vagueness and PO Boxes
    if re.search(r'\b(NEAR|ADJACENT|BEHIND|VICINITY|APPROX|PO BOX|P\.O\. BOX|P O BOX|P\.O\.BOX)\b', addr):
        return True
        
    # 5. EXPANDED INDUSTRIAL FACILITY FILTER
    facility_regex = r'\b(AIRPORT|AFB|BASE|CAMPUS|PORT|PIER|TERMINAL|WELL|PUMP STATION|LIFT STATION|SUBSTATION|PIPELINE|OUTFALL|TANK|LEASE|MINE|PIT|QUARRY|FACILITY|PLANT|ANCHORAGE)\b'
    has_facility = re.search(facility_regex, addr)
    street_suffixes = [' RD', ' ST', ' AVE', ' BLVD', ' DR', ' LN', ' WAY', ' PKWY', ' HWY', ' PIKE', ' ROAD', ' STREET']
    
    has_street = any(suffix in addr for suffix in street_suffixes)
    
    if has_facility and not has_street:
        return True

    # 6. Strip Suites and Units BEFORE checking for real building numbers
    addr_no_suites = re.sub(r'\b(SUITE|STE|UNIT|BLDG|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr)
    addr_no_suites = re.sub(r'#\s*[A-Z0-9-]+', '', addr_no_suites)

    # 7. HIGHWAY & ORDINAL FILTER
    addr_without_hwy = re.sub(r'\b([A-Z]{2}|HWY|HIGHWAY|US|I\s*-?|SR|ROUTE|STATE ROUTE|COUNTY ROAD|USR|CR|PR|INTERSTATE|INT|RTE|RT)\s*\d+[A-Z]?\b', '', addr_no_suites)
    addr_without_ordinals = re.sub(r'\b\d+(ST|ND|RD|TH)\b', '', addr_without_hwy)
    
    is_intersection = any(x in addr for x in [' & ', ' AND ', ' @ ', ' AT '])
    has_real_number = any(char.isdigit() for char in addr_without_ordinals)
    
    if not has_real_number and not is_intersection:
        return True 
        
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
    
    addr = re.sub(r'\b(INTERSECTION OF|CORNER OF|INTERSECTION|INT OF)\b\s*', '', addr)
    
    # Strip highway directions and format intersection words
    addr = re.sub(r'\b(EB|WB|NB|SB)\b', '', addr)
    addr = addr.replace(' AT ', ' AND ')
    addr = addr.replace(' @ ', ' AND ')
    
    addr = re.sub(r'\b(SUITE|STE|UNIT|BLDG|APT|RM|ROOM)\s+[A-Z0-9-]+\b', '', addr)
    addr = re.sub(r'#\s*[A-Z0-9-]+', '', addr)
    addr = re.sub(r'^(\d+)[A-Z]\b', r'\1', addr)
    addr = re.sub(r'\bINDUS\b', 'INDUSTRIAL', addr)
    addr = re.sub(r'\bCOUR\b', 'COURT', addr)
    return " ".join(addr.split())

st.set_page_config(page_title="GIS Phase I ESA Agent", layout="wide", page_icon="📍")

if "run_complete" not in st.session_state:
    st.session_state.run_complete = False
    st.session_state.matches = []
    st.session_state.oob = []
    st.session_state.ngcs = []

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
                addr = clean_string(raw_addr).upper()
                
                addr = addr.split('/')[0].split('(')[0].strip()
                
                chop_words = [' BTWN ', ' BETWEEN ', ' SE OF ', ' SW OF ', ' NE OF ', ' NW OF ', ' NORTH OF ', ' SOUTH OF ', ' EAST OF ', ' WEST OF ', ' N OF ', ' S OF ', ' E OF ', ' W OF ']
                for cw in
