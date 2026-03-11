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
    if re.search(r'\b\d+(\.\d+)?\s*(MILE|MI\b|FT\b|FEET\b)', addr): 
        return True
        
    # 2. Universal Box Catcher
    if re.search(r'\bBOX\s*\d+\b', addr):
        return True
    
    # 3. Catch directional vagueness and old PO Box formats
    vague_terms = [
        'NEAR ', 'ADJACENT', 'BEHIND ', 'VICINITY', 'APPROX ',
        'PO BOX', 'P.O. BOX', 'P O BOX', 'P.O.BOX'
    ]
    if any(term in addr for term in vague_terms): 
        return True
        
    # --- 4. UPDATED CAMPUS FILTER: Now includes PORT, PIER, and TERMINAL ---
    has_campus = re.search(r'\b(AIRPORT|AFB|BASE|CAMPUS|PORT|PIER|TERMINAL)\b', addr)
    street_suffixes = [' RD', ' ST', ' AVE', ' BLVD', ' DR', ' LN', ' WAY', ' PKWY', ' HWY', ' PIKE', ' ROAD', ' STREET']
    
    has_street = any(suffix in addr for suffix in street_suffixes)
    
    # If it is a massive facility but lacks a street name, throw it out!
    if has_campus and not has_street:
        return True

    # 5. HIGHWAY & ORDINAL FILTER: Hide highways and ordinal numbers
    addr_without_hwy = re.sub(r'\b([A-Z]{2}|HWY|HIGHWAY|US|I-|I\s*-|SR|ROUTE|STATE ROUTE|COUNTY ROAD|USR|CR|PR|INTERSTATE|INT|RTE|RT)\s*\d+[A-Z]?\b', '', addr)
    addr_without_ordinals = re.sub(r'\b\d+(ST|ND|RD|TH)\b', '', addr_without_hwy)
    
    is_intersection = any(x in addr for x in [' & ', ' AND ', '@'])
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
    
    # Strip conversational fluff
    addr = re.sub(r'\b(INTERSECTION OF|CORNER OF|INTERSECTION|INT OF)\b\s*', '', addr)
    
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

if
