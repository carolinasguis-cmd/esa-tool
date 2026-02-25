import streamlit as st
import pandas as pd
from geopy.geocoders import ArcGIS
from geopy.distance import geodesic
import io
import pydeck as pdk
import time

def is_vague_address(addr):
    """
    STRICT NGC FILTER:
    Identifies 'Orphan' sites that are descriptive rather than precise.
    """
    addr = str(addr).upper().strip()
    
    # 1. KEYWORD TRIGGER:
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
    first_word = addr.split(' ')[0]
    if not any(char.isdigit() for char in first_word):
        return True
        
    return False

def clean_string(val):
    """Basic cleanup to remove Excel artifacts."""
    if pd.isna(val): return ""
    clean_val = str(val).strip()
    if clean_val.lower() == 'nan': return ""
    if clean_val.endswith('.0'): clean_val = clean_val[:-2]
    return " ".join(clean_val.split())

st.set_page_config(page_title="GIS Phase I ESA Agent", layout="wide", page_icon="📍")

# --- 1. SIDEBAR INPUTS ---
with st.sidebar:
    st.header("⚙️ Project Settings")
    st.success("✅ Using Free ArcGIS Engine (Fast & State-Aware)")
    
    st.divider()
    st.subheader("📍 Target Property")
    site_lat = st.number_input("Site Latitude", format="%.6f", value=28.349200)
    site_lon = st.number_input("Site Longitude", format="%.6f", value=-81.234000)
    search_radius = st.slider("Search Radius (Miles)", 0.1, 2.0, 0.25)
    
    st.divider()
    show_oob = st.checkbox("Show 'Out of Bounds' (Blue Dots)", value=True)

st.title("📍 Phase I ESA: Fast & Free Mapping Agent")
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
                if 'address' in df.columns: 
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
                
                # Extract address components
                raw_addr = row.get('address', '')
                addr = clean_string(raw_addr)
                
                # STEP 1: ORPHAN CHECK (Only test the base address string)
                if is_vague_address(addr):
                    row['status'] = "NGC (Orphan)"
                    row['reason'] = "Vague Description / Intersection"
                    ngcs.append(row)
                    continue 

                # STEP 2: BUILD FULL ADDRESS FOR GEOCODING
                city = clean_string(row.get('city', ''))
                state = clean_string(row.get('state', ''))
                
                # Find zip code regardless of how the column is named
                zip_code = ''
                for z_col in ['zip', 'zip code', 'zipcode', 'zip_code']:
                    if z_col in row:
                        zip_code = clean_string(row[z_col])
                        break
                
                # Glue them together safely
                full_search_address = addr
                if city: full_search_address += f", {city}"
                if state: full_search_address += f", {state}"
                if zip_code: full_search_address += f" {zip_code}"

                # STEP 3: GEOCODING
                try:
                    loc = geolocator.geocode(full_search_address, timeout=10)
                    
                    if loc:
                        found_coords = (loc.latitude, loc.longitude)
                        dist = geodesic(site_coords, found_coords).miles
                        
                        row['mapped_lat'] = loc.latitude
                        row['mapped_lon'] = loc.longitude
                        row['miles_from_site'] = round(dist, 3)
                        row['arcgis_address'] = loc.address
                        row['search_string_used'] = full_search_address # Helpful for debugging
                        
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

            # --- 3. RESULTS DISPLAY ---
            st.divider()
            
            c1, c2, c3 = st.columns(3)
            c1.metric("✅ Matches (Within Radius)", len(matches))
            c2.metric("⚠️ Out of Bounds", len(oob))
            c3.metric("❌ Orphans (NGCs)", len(ngcs))

            # --- 4. MAP ---
            layers = []
            
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

            st.subheader("🗺️ Site Map")
            
            view_state = pdk.ViewState(latitude=site_lat, longitude=site_lon, zoom=13)
            
            st.pydeck_chart(pdk.Deck(
                map_style=None, 
                initial_view_state=view_state,
                layers=layers,
                tooltip={"text": "{address}\nDistance: {miles_from_site} mi\nStatus: {status}"}
            ))

            # --- 5. NGC TABLE ---
            if ngcs:
                st.subheader("❌ Orphan (NGC) List")
                st.warning("The following sites were identified as Orphans (Vague or Unmappable).")
                
                df_ngc = pd.DataFrame(ngcs)
                desired_cols = ['site id', 'site_id', 'city', 'state', 'zip', 'zip code', 'address', 'reason']
                final_cols = [c for c in desired_cols if c in df_ngc.columns]
                if not final_cols: final_cols = ['address', 'reason']
                    
                st.dataframe(df_ngc[final_cols], use_container_width=True)

            # --- 6. EXPORT ---
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                if matches: pd.DataFrame(matches).to_excel(writer, sheet_name="Matches", index=False)
                if oob: pd.DataFrame(oob).to_excel(writer, sheet_name="Out_of_Bounds", index=False)
                if ngcs: pd.DataFrame(ngcs).to_excel(writer, sheet_name="Orphans_NGC", index=False)
            
            st.success("Analysis Complete!")
            st.download_button("📥 Download Final Excel Report", output.getvalue(), "ESA_Final_Report.xlsx")
