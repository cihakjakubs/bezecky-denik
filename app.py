import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from garminconnect import Garmin

# Nastavení stránky (název v prohlížeči a ikona)
st.set_page_config(page_title="Běžecký Deník", page_icon="🏃", layout="wide")

# --- BOČNÍ MENU (NAVIGACE + NASTAVENÍ) ---
st.sidebar.title("🏃 Běžecký Deník")
stranka = st.sidebar.radio(
    "Menu",
    ["📊 Dashboard", "🏃 Deník aktivit", "⚙️ Nastavení zón", "📝 Budoucí deník"]
)

st.sidebar.divider()
st.sidebar.subheader("🔐 Připojení Garmin")
# Údaje se načtou z bezpečného trezoru (Secrets), nebo je lze zadat ručně
default_email = st.secrets.get("GARMIN_EMAIL", "")
default_heslo = st.secrets.get("GARMIN_HESLO", "")

email = st.sidebar.text_input("Garmin E-mail", value=default_email)
heslo = st.sidebar.text_input("Garmin Heslo", value=default_heslo, type="password")

# Výchozí hodnoty nastavení v paměti aplikace
if "pomaly_min" not in st.session_state:
    st.session_state.pomaly_min = 5
    st.session_state.pomaly_sec = 15
    st.session_state.rychly_min = 4
    st.session_state.rychly_sec = 15
    st.session_state.cil_objem = 80
    st.session_state.dni_zpet = 14

# Funkce pro stažení a výpočet dat z Garminu
@st.cache_data(ttl=1800) # Uloží data do mezipaměti na 30 minut, ať se nestahují při každém kliknutí
def nacti_data_z_garminu(user_email, user_pass, dni, lim_pomaly, lim_rychly):
    client = Garmin(user_email, user_pass)
    client.login()
    
    dnes = datetime.now()
    od_data = (dnes - timedelta(days=dni)).strftime("%Y-%m-%d")
    do_data = dnes.strftime("%Y-%m-%d")
    
    aktivity = client.get_activities_by_date(od_data, do_data, "running")
    seznam_behu = []
    
    for akt in aktivity:
        aid = akt['activityId']
        nazev = akt['activityName']
        datum = akt['startTimeLocal'][:10]
        vzdalenost = round(akt.get('distance', 0) / 1000, 2)
        tep = akt.get('averageHR', 0)
        
        detaily = client.get_activity_details(aid)
        popisy = [d['key'] for d in detaily['metricDescriptors']]
        
        beh_objem, beh_seda, beh_rychlost = 0, 0, 0
        
        if 'directTimestamp' in popisy and 'directSpeed' in popisy:
            idx_cas = popisy.index('directTimestamp')
            idx_rychlost = popisy.index('directSpeed')
            vzorky = detaily['activityDetailMetrics']
            
            for i in range(1, len(vzorky)):
                m_ted = vzorky[i]['metrics']
                m_pred = vzorky[i-1]['metrics']
                c_ted, c_pred, r_ms = m_ted[idx_cas], m_pred[idx_cas], m_ted[idx_rychlost]
                
                if c_ted and c_pred and r_ms is not None:
                    rozdil = (c_ted - c_pred) / 1000.0
                    if rozdil > 10 or r_ms < 0.5:
                        continue
                    tempo = 1000.0 / r_ms
                    if tempo >= lim_pomaly:
                        beh_objem += rozdil
                    elif tempo <= lim_rychly:
                        beh_rychlost += rozdil
                    else:
                        beh_seda += rozdil
                        
        seznam_behu.append({
            "Datum": datum,
            "Název": nazev,
            "Vzdálenost (km)": vzdalenost,
            "Prům. tep": tep,
            "Objem (min)": round(beh_objem / 60, 1),
            "Šedá (min)": round(beh_seda / 60, 1),
            "Rychlost (min)": round(beh_rychlost / 60, 1),
            "Celkem (min)": round((beh_objem + beh_seda + beh_rychlost) / 60, 1)
        })
        
    return pd.DataFrame(seznam_behu)

# Přepočet limitů na sekundy
lim_pomaly_sec = st.session_state.pomaly_min * 60 + st.session_state.pomaly_sec
lim_rychly_sec = st.session_state.rychly_min * 60 + st.session_state.rychly_sec

# Tlačítko pro ruční aktualizaci dat
if st.sidebar.button("🔄 Synchronizovat z Garminu", use_container_width=True):
    st.cache_data.clear()

# ==========================================
# STRÁNKA 1: DASHBOARD
# ==========================================
if stranka == "📊 Dashboard":
    st.title(f"📊 Přehled za posledních {st.session_state.dni_zpet} dní")
    
    if not email or not heslo:
        st.warning("👈 Zadej v levém menu svůj Garmin e-mail a heslo pro načtení dat.")
    else:
        with st.spinner("Stahuji a analyzuji běhy z Garmin Connect..."):
            try:
                df = nacti_data_z_garminu(email, heslo, st.session_state.dni_zpet, lim_pomaly_sec, lim_rychly_sec)
                
                if df.empty:
                    st.info("Za zvolené období nebyly nalezeny žádné běhy.")
                else:
                    objem = df["Objem (min)"].sum()
                    seda = df["Šedá (min)"].sum()
                    rychlost = df["Rychlost (min)"].sum()
                    celkem = objem + seda + rychlost
                    polarizovane = objem + rychlost
                    
                    cil_rychlost_pct = 100 - st.session_state.cil_objem
                    potrebna_rychlost = objem * (cil_rychlost_pct / st.session_state.cil_objem)
                    rozdil_min = potrebna_rychlost - rychlost
                    
                    # Hlavní doporučení (Semafor)
                    if rozdil_min > 0:
                        st.error(f"🎯 **Do cílového poměru ({st.session_state.cil_objem}:{cil_rychlost_pct}) ti chybí {round(rozdil_min, 1)} minut v rychlosti.**")
                    else:
                        st.success(f"✅ **Splněno! Máš navíc {round(abs(rozdil_min), 1)} minut v rychlosti oproti cíli ({st.session_state.cil_objem}:{cil_rychlost_pct}).**")
                    
                    # Metriky v 4 sloupcích
                    c1, c2, c3, c4 = st.columns(4)
                    akt_obj_pct = round((objem / polarizovane) * 100, 1) if polarizovane > 0 else 0
                    akt_rych_pct = round((rychlost / polarizovane) * 100, 1) if polarizovane > 0 else 0
                    seda_pct = round((seda / celkem) * 100, 1) if celkem > 0 else 0
                    
                    c1.metric("🟢 Objem (pomalu)", f"{round(objem, 1)} min", f"{akt_obj_pct} % (cíl {st.session_state.cil_objem}%)")
                    c2.metric("🔴 Rychlost (rychle)", f"{round(rychlost, 1)} min", f"{akt_rych_pct} % (cíl {cil_rychlost_pct}%)")
                    c3.metric("🟡 Šedá zóna", f"{round(seda, 1)} min", f"{seda_pct} % z celku", delta_color="inverse")
                    c4.metric("⏱️ Celkový čas", f"{round(celkem, 1)} min", f"{round(df['Vzdálenost (km)'].sum(), 1)} km")
                    
                    st.divider()
                    
                    # Graf rozložení zón
                    st.subheader("Rozložení času v zónách (minuty)")
                    graf_data = pd.DataFrame({
                        "Zóna": ["🟢 Objem", "🟡 Šedá zóna", "🔴 Rychlost"],
                        "Minuty": [objem, seda, rychlost]
                    }).set_index("Zóna")
                    st.bar_chart(graf_data)
                    
            except Exception as e:
                st.error(f"Chyba při komunikaci s Garminem: {e}")

# ==========================================
# STRÁNKA 2: DENÍK AKTIVIT
# ==========================================
elif stranka == "🏃 Deník aktivit":
    st.title("🏃 Seznam stažených běhů")
    if email and heslo:
        df = nacti_data_z_garminu(email, heslo, st.session_state.dni_zpet, lim_pomaly_sec, lim_rychly_sec)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("👈 Nejdříve se přihlas v levém menu.")

# ==========================================
# STRÁNKA 3: NASTAVENÍ ZÓN
# ==========================================
elif stranka == "⚙️ Nastavení zón":
    st.title("⚙️ Nastavení tempových zón a cílů")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🟢 Hranice pro Objem (pomalejší než)")
        st.session_state.pomaly_min = st.number_input("Minuty / km (Objem)", 3, 10, st.session_state.pomaly_min)
        st.session_state.pomaly_sec = st.number_input("Sekundy / km (Objem)", 0, 59, st.session_state.pomaly_sec)
        st.info(f"Aktuální hranice objemu: **pomalejší než {st.session_state.pomaly_min}:{st.session_state.pomaly_sec:02d} /km**")

    with col2:
        st.subheader("🔴 Hranice pro Rychlost (rychlejší než)")
        st.session_state.rychly_min = st.number_input("Minuty / km (Rychlost)", 2, 8, st.session_state.rychly_min)
        st.session_state.rychly_sec = st.number_input("Sekundy / km (Rychlost)", 0, 59, st.session_state.rychly_sec)
        st.info(f"Aktuální hranice rychlosti: **rychlejší než {st.session_state.rychly_min}:{st.session_state.rychly_sec:02d} /km**")
        
    st.divider()
    st.subheader("⚖️ Cílový poměr a klouzavé okno")
    st.session_state.cil_objem = st.slider("Cílový podíl objemu (%)", 50, 95, st.session_state.cil_objem)
    st.write(f"Cílový poměr nastaven na: **{st.session_state.cil_objem} % Objem : {100 - st.session_state.cil_objem} % Rychlost**")
    
    st.session_state.dni_zpet = st.selectbox("Délka klouzavého okna (dny)", [7, 14, 21, 30], index=1)
    st.caption("Změny se uloží okamžitě – stačí se přepnout zpět na Dashboard.")

# ==========================================
# STRÁNKA 4: BUDOUCÍ TRÉNINKOVÝ DENÍK
# ==========================================
elif stranka == "📝 Budoucí deník":
    st.title("📝 Tréninkový deník (Připraveno k rozšíření)")
    st.info("Tato sekce je připravená jako kostra pro budoucí funkce, až napojíme databázi Supabase:")
    st.markdown("""
    * **Subjektivní pocity (RPE 1–10)** a poznámky ke každému běhu
    * **Výjimky pro počasí a terén** (např. *'Běh v hlubokém sněhu – počítat jako objem bez ohledu na tempo'*)
    * **Sledování kilometrů v běžeckých botách**
    * **Plánovač tréninků dopředu** (simulace, jak plánovaný trénink změní 14denní poměr)
    """)
