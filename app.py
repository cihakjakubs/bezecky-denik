import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_option_menu import option_menu
from datetime import datetime, timedelta
from garminconnect import Garmin

st.set_page_config(page_title="Běžecký Deník", page_icon="🏃", layout="wide")

# --- BOČNÍ MENU ---
with st.sidebar:
    st.title("🏃 Běžecký Deník")
    stranka = option_menu(
            menu_title=None,
            options=["Dashboard", "Plánovač", "Deník aktivit", "Nastavení zón", "Budoucí deník"],
            icons=["speedometer2", "calendar-plus", "activity", "gear", "journal-text"],
            default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"font-size": "18px"},
            "nav-link": {"font-size": "16px", "text-align": "left", "margin": "4px 0px", "--hover-color": "#262730"},
            "nav-link-selected": {"background-color": "#e74c3c", "font-weight": "bold"},
        }
    )


st.sidebar.divider()
st.sidebar.subheader("🔐 Připojení Garmin")
default_email = st.secrets.get("GARMIN_EMAIL", "")
default_heslo = st.secrets.get("GARMIN_HESLO", "")

email = st.sidebar.text_input("Garmin E-mail", value=default_email)
heslo = st.sidebar.text_input("Garmin Heslo", value=default_heslo, type="password")

# Výchozí hodnoty nastavení
# Výchozí hodnoty nastavení
if "pomaly_min" not in st.session_state:
    st.session_state.pomaly_min = 5
    st.session_state.pomaly_sec = 15
    st.session_state.rychly_min = 4
    st.session_state.rychly_sec = 15
    st.session_state.cil_objem = 80
    st.session_state.tolerance_pct = 3        # Tolerance ±3 %
    st.session_state.dni_zpet = 14
    st.session_state.cil_posilovani_tyden = 1 # Cíl: 1x týdně

if "limit_seda_pct" not in st.session_state:
    st.session_state.limit_seda_pct = 5       # Limit pro šedou zónu (5 %)

@st.cache_data(ttl=1800)
def nacti_data_z_garminu(user_email, user_pass, dni, lim_pomaly, lim_rychly):
    client = Garmin(user_email, user_pass)
    client.login()
    
    dnes = datetime.now()
    od_data = (dnes - timedelta(days=dni)).strftime("%Y-%m-%d")
    do_data = dnes.strftime("%Y-%m-%d")
    
    aktivity = client.get_activities_by_date(od_data, do_data)
    seznam_aktivit = []
    
    for akt in aktivity:
        typ = akt.get('activityType', {}).get('typeKey', '')
        
        if 'running' not in typ and 'strength' not in typ and 'cardio' not in typ and 'fitness' not in typ:
            continue

        aid = akt['activityId']
        nazev = akt['activityName']
        datum = akt['startTimeLocal'][:10]
        vzdalenost = round(akt.get('distance', 0) / 1000, 2)
        tep = akt.get('averageHR', 0)
        celkovy_cas_min = round(akt.get('duration', 0) / 60, 1)

        beh_objem, beh_seda, beh_rychlost = 0, 0, 0
        kategorie = "🏃 Běh" if 'running' in typ else "🏋️ Posilování"


        
        if kategorie == "🏃 Běh":
            detaily = client.get_activity_details(aid)
            popisy = [d['key'] for d in detaily['metricDescriptors']]
            
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
                            
        seznam_aktivit.append({
            "Datum": datum,
            "Typ": kategorie,
            "Název": nazev,
            "Vzdálenost (km)": vzdalenost if kategorie == "🏃 Běh" else 0.0,
            "Prům. tep": tep,
            "Objem (min)": round(beh_objem / 60, 1),
            "Šedá (min)": round(beh_seda / 60, 1),
            "Rychlost (min)": round(beh_rychlost / 60, 1),
            "Celkem (min)": round((beh_objem + beh_seda + beh_rychlost) / 60, 1) if kategorie == "🏃 Běh" else celkovy_cas_min
        })
        
    return pd.DataFrame(seznam_aktivit)

lim_pomaly_sec = st.session_state.pomaly_min * 60 + st.session_state.pomaly_sec
lim_rychly_sec = st.session_state.rychly_min * 60 + st.session_state.rychly_sec

if st.sidebar.button("🔄 Synchronizovat z Garminu", use_container_width=True):
    st.cache_data.clear()

# ==========================================
# STRÁNKA 1: DASHBOARD
# ==========================================
if stranka == "Dashboard":
    st.title(f"📊 Přehled za posledních {st.session_state.dni_zpet} dní")
    
    if not email or not heslo:
        st.warning("👈 Zadej v levém menu svůj Garmin e-mail a heslo pro načtení dat.")
    else:
        with st.spinner("Stahuji běhy a silové tréninky z Garmin Connect..."):
            try:
                df = nacti_data_z_garminu(email, heslo, st.session_state.dni_zpet, lim_pomaly_sec, lim_rychly_sec)
                
                if df.empty:
                    st.info("Za zvolené období nebyly nalezeny žádné aktivity.")
                else:
                    df_behy = df[df["Typ"] == "🏃 Běh"]
                    df_posilovani = df[df["Typ"] == "🏋️ Posilování"]
                    
                    # --- VÝPOČTY BĚH S TOLERANCÍ ---
                    objem = df_behy["Objem (min)"].sum()
                    seda = df_behy["Šedá (min)"].sum()
                    rychlost = df_behy["Rychlost (min)"].sum()
                    celkem_beh_min = objem + seda + rychlost
                    polarizovane = objem + rychlost
                    
                    cil_rychlost_pct = 100 - st.session_state.cil_objem
                    tol = st.session_state.tolerance_pct
                    min_rychlost_pct = max(1, cil_rychlost_pct - tol)
                    max_rychlost_pct = min(99, cil_rychlost_pct + tol)
                    
                    akt_obj_pct = round((objem / polarizovane) * 100, 1) if polarizovane > 0 else 0
                    akt_rych_pct = round((rychlost / polarizovane) * 100, 1) if polarizovane > 0 else 0
                    seda_pct = round((seda / celkem_beh_min) * 100, 1) if celkem_beh_min > 0 else 0
                    
                    # --- VÝPOČTY POSILOVÁNÍ ---
                    pocet_posilovani = len(df_posilovani)
                    cas_posilovani = df_posilovani["Celkem (min)"].sum()
                    cil_posilovani_obdobi = round((st.session_state.dni_zpet / 7) * st.session_state.cil_posilovani_tyden)
                    
                    # --- SEMAFOR S TOLERANCÍ ---
                    if polarizovane == 0:
                        st.info("Zatím nemáš žádné minuty v objemu ani v rychlosti.")
                    elif akt_rych_pct < min_rychlost_pct:
                        potrebna_min_rychlost = objem * (min_rychlost_pct / (100 - min_rychlost_pct))
                        chybi_rychlost = potrebna_min_rychlost - rychlost
                        st.error(f"🎯 **Málo rychlosti ({akt_rych_pct} %):** Do cílového rozmezí ({min_rychlost_pct}–{max_rychlost_pct} %) ti chybí **{round(chybi_rychlost, 1)} min** v rychlosti.")
                    elif akt_rych_pct > max_rychlost_pct:
                        potrebny_min_objem = rychlost * ((100 - max_rychlost_pct) / max_rychlost_pct)
                        chybi_objem = potrebny_min_objem - objem
                        st.warning(f"⚠️ **Hodně rychlosti ({akt_rych_pct} %):** Překročil jsi horní hranici ({max_rychlost_pct} %). Zařaď cca **{round(chybi_objem, 1)} min** volného objemu.")
                    else:
                        st.success(f"✅ **Ideální poměr ({akt_obj_pct} : {akt_rych_pct})!** Držíš se v cílovém rozmezí {st.session_state.cil_objem}:{cil_rychlost_pct} (tolerance ±{tol} %).")
                                        # --- VAROVÁNÍ PRO ŠEDOU ZÓNU ---
                    if seda_pct > st.session_state.limit_seda_pct:
                        st.warning(f"⚠️ **Pozor na šedou zónu ({seda_pct} %):** Překročil jsi nastavený limit {st.session_state.limit_seda_pct} % z celkového času běhu. Zkus volné běhy více zpomalit.")
                    
                    # Metriky v 5 sloupcích
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("🟢 Objem (běh)", f"{round(objem, 1)} min", f"{akt_obj_pct} % (cíl {st.session_state.cil_objem} ±{tol}%)")
                    c2.metric("🔴 Rychlost (běh)", f"{round(rychlost, 1)} min", f"{akt_rych_pct} % (cíl {cil_rychlost_pct} ±{tol}%)")
                    c3.metric("🟡 Šedá zóna", f"{round(seda, 1)} min", f"{seda_pct} % z běhu", delta_color="off")
                    c4.metric("🏋️ Posilování", f"{pocet_posilovani} / {cil_posilovani_obdobi} trén.", f"{round(cas_posilovani, 1)} min celkem")
                    c5.metric("⏱️ Celkem aktivně", f"{round(celkem_beh_min + cas_posilovani, 1)} min", f"{round(df_behy['Vzdálenost (km)'].sum(), 1)} km běh")
                    
                    st.divider()

                    # --- SEKCE 1: KOLÁČOVÝ GRAF ---
                    st.subheader("🎯 Rozložení běžeckých zón")
                    graf_data = pd.DataFrame({
                        "Zóna": ["🟢 Objem", "🟡 Šedá zóna", "🔴 Rychlost"],
                        "Minuty": [objem, seda, rychlost]
                    })
                    fig_pie = px.pie(
                        graf_data,
                        values="Minuty",
                        names="Zóna",
                        color="Zóna",
                        color_discrete_map={
                            "🟢 Objem": "#2ecc71",
                            "🟡 Šedá zóna": "#f1c40f",
                            "🔴 Rychlost": "#e74c3c"
                        },
                        hole=0.45
                    )
                    fig_pie.update_traces(
                        textinfo="none",
                        hovertemplate="<b>%{label}</b><br>Čas: %{value:.1f} min<br>Podíl: %{percent}<extra></extra>"
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)

                    st.divider()

                    # --- SEKCE 2: AEROBNÍ EFEKTIVITA (POD GRAFEM) ---
                    st.subheader("🫀 Aerobní efektivita (Tempo vs. Tep)")
                    behy_ef = df_behy[(df_behy["Prům. tep"] > 0) & (df_behy["Vzdálenost (km)"] > 0) & (df_behy["Celkem (min)"] > 0)].copy()

                    if behy_ef.empty:
                        st.info("Pro výpočet efektivity nejsou k dispozici běhy s tepem a GPS.")
                    else:
                        behy_ef["Rychlost_m_min"] = (behy_ef["Vzdálenost (km)"] * 1000) / behy_ef["Celkem (min)"]
                        behy_ef["Efektivita"] = (behy_ef["Rychlost_m_min"] / behy_ef["Prům. tep"]).round(2)

                        prum_ef = round(behy_ef["Efektivita"].mean(), 2)
                        posledni_ef = behy_ef.iloc["Efektivita"]
                        rozdil_ef = round(posledni_ef - prum_ef, 2)

                        st.metric(
                            "Index efektivity (EF)",
                            f"{posledni_ef} (poslední běh)",
                            f"{rozdil_ef:+} oproti průměru ({prum_ef})"
                        )
                        st.caption("Čím vyšší číslo, tím rychleji běžíš na stejný tep (forma jde nahoru).")

                        behy_trend = behy_ef.sort_values("Datum")
                        fig_ef = px.line(
                            behy_trend,
                            x="Datum",
                            y="Efektivita",
                            markers=True,
                            hover_data=["Název", "Prům. tep", "Vzdálenost (km)"]
                        )
                        fig_ef.update_layout(height=280, yaxis_title="EF (m/tep)")
                        st.plotly_chart(fig_ef, use_container_width=True)

            except Exception as e:
                st.error(f"Chyba při komunikaci s Garminem: {e}")

# ==========================================
# STRÁNKA 2: DENÍK AKTIVIT
# ==========================================
elif stranka == "Deník aktivit":
    st.title("🏃 Seznam stažených aktivit")
    if email and heslo:
        df = nacti_data_z_garminu(email, heslo, st.session_state.dni_zpet, lim_pomaly_sec, lim_rychly_sec)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.warning("👈 Nejdříve se přihlas v levém menu.")

# ==========================================
# STRÁNKA 3: NASTAVENÍ ZÓN
# ==========================================
elif stranka == "Nastavení zón":
    st.title("⚙️ Nastavení zón a tréninkových cílů")
    
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
    st.subheader("⚖️ Cílový poměr, tolerance, posilování a okno")
    
    col3, col4 = st.columns(2)
    with col3:
        st.session_state.cil_objem = st.slider("Cílový podíl běžeckého objemu (%)", 50, 95, st.session_state.cil_objem)
        st.session_state.tolerance_pct = st.slider("Tolerance odchylky (± %)", 0, 10, st.session_state.tolerance_pct)
        cil_r = 100 - st.session_state.cil_objem
        st.write(f"Ideální rozmezí rychlosti: **{max(1, cil_r - st.session_state.tolerance_pct)} % až {min(99, cil_r + st.session_state.tolerance_pct)} %**")
        
    with col4:
        st.session_state.cil_posilovani_tyden = st.number_input("Cíl posilování (tréninků za týden)", 0, 7, st.session_state.cil_posilovani_tyden)
        st.session_state.dni_zpet = st.selectbox("Délka klouzavého okna (dny)", [7, 14, 30], index=[7, 14, 30].index(st.session_state.dni_zpet))
    
    st.session_state.limit_seda_pct = st.slider("Maximální tolerance šedé zóny (% z běhu)", 0, 25, st.session_state.limit_seda_pct)

# ==========================================
# STRÁNKA 4: BUDOUCÍ TRÉNINKOVÝ DENÍK
# ==========================================
elif stranka == "Budoucí deník":
    st.title("📝 Tréninkový deník (Připraveno k rozšíření)")
    st.info("Tato sekce je připravená pro budoucí napojení databáze Supabase:")
    st.markdown("""
    * **Subjektivní pocity (RPE 1–10)** a poznámky ke každému tréninku
    * **Výjimky pro počasí a terén** (např. *'Běh v hlubokém sněhu – počítat jako objem bez ohledu na tempo'*)
    * **Sledování kilometrů v běžeckých botách**
    * **Plánovač tréninků dopředu** (simulace, jak plánovaný trénink změní 14denní poměr)
    """)
