import streamlit as st
import pandas as pd
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import io
import pydeck as pdk
import time
import re
import os

# --- 1. SETUP & KEY MANAGEMENT ---
KEY_FILE = "google_key.txt"

def save_key(key):
    with open(KEY_FILE, "w") as f:
        f.write(key)

def load_key():
    if os.path.exists(KEY_FILE):
        return open(KEY_FILE, "r").read().strip()
    return ""

def is_vague_address(addr):
    """
    STRICT NGC FILTER:
    Identifies 'Orphan' sites that are descriptive rather than precise.
    """
    addr = str(addr).upper().strip()
    
    # 1. KEYWORD TRIGGER:
    # If the address contains these words, it is likely an Orphan/NGC.
    # Note: "UNIT", "#", "STE" are EXCLUDED so they map correctly.
    vague_terms = [
        'INTERSEC', 'CORNER', ' OF ', 
        'NORTH OF', 'SOUTH OF', 'EAST OF', 'WEST OF',
        '1 MI', '2 MI', '3 MI', 'MILE', 
        'NEAR', 'ADJACENT', 'BEHIND', 'VICINITY', 
        'APPROX', '&' 
    ]
    
    if any(term in addr for term in vague_terms): 
        return True
        
    # 2. NUMERIC CHECK:
    # Valid street addresses almost always start with a number (e.g., "6839 Narcoossee").
    # If it starts with a letter (e.g., "Lake Hart Property"), it's likely an NGC.
    first_word = addr.split(' ')[0]
    # Check if the first word contains ANY digit
    if not any(char.isdigit() for char in first_word):
        return True
        
    return False

def clean_address_string(addr_raw):
    """Basic cleanup to remove Excel artifacts."""
    if pd.isna(addr_raw): return ""
    addr = str(addr_raw).strip()
    if addr.lower() == 'nan': return ""
    if addr.endswith('.0'): addr = addr[:-2]
    return " ".join(addr.split())

st.set_page_config(page_title="GIS Phase I ESA Agent", layout="wide", page_icon="📍")

# --- 2. SIDEBAR INPUTS ---
with st.sidebar:
    st.header("⚙️ Project Settings")
    
    # API Key Handling
    existing_key = load_key()
    api_key = st.text_input("Google API Key", value=existing_key, type="password")
    if st.button("💾 Save Key"): 
        save_key(api_key)
        st.success("Key Saved!")
    
    st.divider()
    st.subheader("📍 Target Property")
    # Default coordinates (approx. Narcoossee area)
    site_lat = st.number_input("Site Latitude", format="%.6f", value=28.349200)
    site_lon = st.number_input("Site Longitude", format="%.6f", value=-81.234000)
    search_radius = st.slider("Search Radius (Miles)", 0.1, 2.0, 0.25)
    
    st.divider()
    show_oob = st.checkbox("Show 'Out of Bounds' (Gray Dots)", value=True)

st.title("📍 Phase I ESA: Final Mapping Agent")
st.markdown("Automated sorting of **Mappable Sites** vs. **Orphans (NGCs)**.")

uploaded_files = st.file_uploader("📂 Drop ESA Files Here (Excel/CSV)", type=["xlsx", "csv"], accept_multiple_files=True)

# --- 3. MAIN ANALYSIS ENGINE ---
if uploaded_files and api_key:
    if st.button("🚀 Run Analysis"):
        geolocator = GoogleV3(api_key=api_key)
        site_coords = (site_lat, site_lon)
        
        # Setup viewbox to bias results to the project area (approx +/- 15 miles)
        viewbox = [
            (site_lat + 0.2, site_lon + 0.2), 
            (site_lat - 0.2, site_lon - 0.2)
        ]

        all_data = []
        # Load all files
        for f in uploaded_files:
            try:
                if f.name.endswith('.csv'): df = pd.read_csv(f)
                else: df = pd.read_excel(f)
                # Standardize column names
                df.columns = df.columns.str.strip().str.lower()
                if 'address' in df.columns: 
                    all_data.append(df)
            except Exception as e:
                st.error(f"Could not read {f.name}: {e}")

        if all_data:
            master_df = pd.concat(all_data, ignore_index=True)
            matches, oob, ngcs = [], [], []
            
            # Progress Bar
            prog_bar = st.progress(0)
            status_text = st.empty()
            total_rows = len(master_df)

            for i, (index, row) in enumerate(master_df.iterrows()):
                # Update progress
                prog_bar.progress((i + 1) / total_rows)
                status_text.text(f"Processing Record {i+1} of {total_rows}...")
                
                # Get and clean address
                raw_addr = row.get('address', '')
                addr = clean_address_string(raw_addr)
                
                # --- STEP 1: ORPHAN CHECK ---
                if is_vague_address(addr):
                    row['status'] = "NGC (Orphan)"
                    row['reason'] = "Vague Description / Intersection"
                    ngcs.append(row)
                    continue # Skip to next row

                # --- STEP 2: GEOCODING ---
                try:
                    loc = geolocator.geocode(addr, bounds=viewbox, timeout=10)
                    
                    if loc:
                        # Calculate distance
                        found_coords = (loc.latitude, loc.longitude)
                        dist = geodesic(site_coords, found_coords).miles
                        
                        # Add spatial data to row
                        row['mapped_lat'] = loc.latitude
                        row['mapped_lon'] = loc.longitude
                        row['miles_from_site'] = round(dist, 3)
                        row['google_address'] = loc.address
                        
                        if dist <= search_radius:
                            row['status'] = "Match"
                            matches.append(row)
                        else:
                            row['status'] = "Out of Bounds"
                            oob.append(row)
                    else:
                        # Google couldn't find it -> NGC
                        row['status'] = "NGC (Not Found)"
                        row['reason'] = "Google returned zero results"
                        ngcs.append(row)
                        
                except Exception as e:
                    # API Error -> NGC
                    row['status'] = "Error"
                    row['reason'] = str(e)
                    ngcs.append(row)
                
                # Tiny pause to respect API limits
                time.sleep(0.05)

            # Clear progress bar
            prog_bar.empty()
            status_text.empty()

            # --- 4. RESULTS DISPLAY ---
            st.divider()
            
            # Metrics
            c1, c2, c3 = st.columns(3)
            c1.metric("✅ Matches (Within Radius)", len(matches))
            c2.metric("⚠️ Out of Bounds", len(oob))
            c3.metric("❌ Orphans (NGCs)", len(ngcs))

            # --- 5. MAP ---
            # Layers
            layers = []
            
            # Layer 1: Target Property (Big Red Dot)
            layers.append(pdk.Layer(
                'ScatterplotLayer',
                data=pd.DataFrame([{'lat': site_lat, 'lon': site_lon}]),
                get_position='[lon, lat]',
                get_color='[255, 0, 0, 255]', # Red
                get_radius=120,
                pickable=False
            ))
            
            # Layer 2: Matches (Green Dots)
            if matches:
                layers.append(pdk.Layer(
                    'ScatterplotLayer',
                    data=pd.DataFrame(matches),
                    get_position='[mapped_lon, mapped_lat]',
                    get_color='[0, 200, 0, 200]', # Green
                    get_radius=80,
                    pickable=True
                ))
            
            # Layer 3: Out of Bounds (Gray Dots - Optional)
            if show_oob and oob:
                layers.append(pdk.Layer(
                    'ScatterplotLayer',
                    data=pd.DataFrame(oob),
                    get_position='[mapped_lon, mapped_lat]',
                    get_color='[150, 150, 150, 150]', # Gray
                    get_radius=60,
                    pickable=True
                ))

            # Render Map
            st.subheader("🗺️ Site Map")
            
            # Determine Zoom Level
            # If we have matches, center the map nicely.
            view_state = pdk.ViewState(latitude=site_lat, longitude=site_lon, zoom=13)
            
            st.pydeck_chart(pdk.Deck(
                map_style=None, # Default style (Reliable)
                initial_view_state=view_state,
                layers=layers,
                tooltip={"text": "{address}\nDistance: {miles_from_site} mi"}
            ))

            # --- 6. NGC TABLE (Live View) ---
            if ngcs:
                st.subheader("❌ Orphan (NGC) List")
                st.warning("The following sites were identified as Orphans (Vague or Unmappable). Please review for the report.")
                st.dataframe(pd.DataFrame(ngcs)[['address', 'reason']], use_container_width=True)

            # --- 7. EXPORT ---
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                if matches: pd.DataFrame(matches).to_excel(writer, sheet_name="Matches", index=False)
                if oob: pd.DataFrame(oob).to_excel(writer, sheet_name="Out_of_Bounds", index=False)
                if ngcs: pd.DataFrame(ngcs).to_excel(writer, sheet_name="Orphans_NGC", index=False)
            
            st.success("Analysis Complete!")
            st.download_button("📥 Download Final Excel Report", output.getvalue(), "ESA_Final_Report.xlsx")