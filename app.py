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

if "tep_objem_max" not in st.session_state:
    st.session_state.tep_objem_max = 148      # Tep pod tuto hranici = Objem v terénu
    st.session_state.tep_rychlost_min = 168   # Tep nad tuto hranici = Rychlost v terénu
    st.session_state.zapnout_gap = True       # Automatický přepočet kopců (GAP)

if "rucni_teren_ids" not in st.session_state:
    st.session_state.rucni_teren_ids = []

@st.cache_data(ttl=1800)
def nacti_data_z_garminu(user_email, user_pass, dni, lim_pomaly, lim_rychly, tep_obj, tep_rych, pouzit_gap, rucni_teren_ids):
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
        prevyseni_celkem = akt.get('elevationGain', 0) or 0

        beh_objem, beh_seda, beh_rychlost = 0, 0, 0
        kategorie = "🏃 Běh" if 'running' in typ else "🏋️ Posilování"

        nazev_lower = nazev.lower()
        je_ob_auto = ('trail' in typ) or any(slovo in nazev_lower for slovo in ['trail', 'ob', 'orienťák', 'orientak', 'les'])
        pouzit_tep_rezim = (aid in rucni_teren_ids) or je_ob_auto

        rychle_useky_pocet = 0
        v_rychlem_useku = False
        nastoupano_v_rychlosti = 0.0

        if kategorie == "🏃 Běh":
            detaily = client.get_activity_details(aid)
            popisy = [d['key'] for d in detaily['metricDescriptors']]

            if 'directTimestamp' in popisy:
                idx_cas = popisy.index('directTimestamp')
                idx_rychlost = popisy.index('directSpeed') if 'directSpeed' in popisy else None
                idx_vyska = popisy.index('directElevation') if 'directElevation' in popisy else None
                idx_tep = popisy.index('directHeartRate') if 'directHeartRate' in popisy else None

                vzorky = detaily['activityDetailMetrics']
                historie_vysky = []

                for i in range(1, len(vzorky)):
                    m_ted = vzorky[i]['metrics']
                    m_pred = vzorky[i-1]['metrics']
                    c_ted, c_pred = m_ted[idx_cas], m_pred[idx_cas]

                    if not c_ted or not c_pred:
                        continue

                    rozdil = (c_ted - c_pred) / 1000.0
                    if rozdil > 10:
                        historie_vysky.clear()
                        v_rychlem_useku = False
                        continue

                    hr_ted = m_ted[idx_tep] if (idx_tep is not None and m_ted[idx_tep] is not None) else None
                    je_ted_rychle = False

                    # --- REŽIM A: OB / TERÉN (PODLE TEPU) ---
                    if pouzit_tep_rezim and hr_ted is not None:
                        if hr_ted <= tep_obj:
                            beh_objem += rozdil
                        elif hr_ted >= tep_rych:
                            beh_rychlost += rozdil
                            je_ted_rychle = True
                        else:
                            beh_seda += rozdil

                    # --- REŽIM B: SILNICE (POROVNÁNÍ TEP × TEMPO + 10s KOPCE) ---
                    elif idx_rychlost is not None and m_ted[idx_rychlost] is not None:
                        r_ms = m_ted[idx_rychlost]
                        if r_ms < 0.5:
                            v_rychlem_useku = False
                            continue

                        sklon = 0.0
                        prevyseni_krok = 0.0
                        if idx_vyska is not None and m_ted[idx_vyska] is not None and m_pred[idx_vyska] is not None:
                            prevyseni_krok = max(0.0, m_ted[idx_vyska] - m_pred[idx_vyska])

                        if pouzit_gap and idx_vyska is not None and m_ted[idx_vyska] is not None:
                            historie_vysky.append((c_ted / 1000.0, m_ted[idx_vyska], r_ms * rozdil))
                            while len(historie_vysky) > 1 and (historie_vysky[-1] - historie_vysky) > 10.0:
                                historie_vysky.pop(0)
                            
                            if len(historie_vysky) >= 2:
                                prevyseni_10s = historie_vysky[-1] - historie_vysky
                                vzdalenost_10s = sum(x for x in historie_vysky[1:])
                                if vzdalenost_10s > 5.0:
                                    sklon = max(-0.25, min(0.25, prevyseni_10s / vzdalenost_10s))

                        efektivni_rychlost = r_ms
                        if pouzit_gap and sklon > 0.025:
                            efektivni_rychlost = r_ms * (1 + (sklon * 3.3))

                        tempo = 1000.0 / efektivni_rychlost

                        if pouzit_gap and sklon < -0.025 and hr_ted is not None and hr_ted <= tep_obj:
                            beh_objem += rozdil
                        elif pouzit_gap and sklon > 0.025 and ((hr_ted is not None and hr_ted >= tep_rych) or tempo <= lim_rychly):
                            beh_rychlost += rozdil
                            je_ted_rychle = True
                        elif tempo >= lim_pomaly:
                            beh_objem += rozdil
                        elif tempo <= lim_rychly:
                            beh_rychlost += rozdil
                            je_ted_rychle = True
                        else:
                            beh_seda += rozdil

                    # Počítání rychlých úseků a nastoupaných metrů v rychlosti (pro rozpoznání Intervalů vs. Kopců)
                    if je_ted_rychle:
                        if idx_vyska is not None and m_ted[idx_vyska] is not None and m_pred[idx_vyska] is not None:
                            nastoupano_v_rychlosti += max(0.0, m_ted[idx_vyska] - m_pred[idx_vyska])
                        if not v_rychlem_useku:
                            rychle_useky_pocet += 1
                            v_rychlem_useku = True
                    else:
                        v_rychlem_useku = False

        # --- AUTOMATICKÉ URČENÍ ŠTÍTKU TRÉNINKU ---
        typ_treninku = "🏋️ POSILOVÁNÍ"
        if kategorie == "🏃 Běh":
            celkem_sec = beh_objem + beh_seda + beh_rychlost
            podil_rychlosti = (beh_rychlost / celkem_sec) if celkem_sec > 0 else 0

            if pouzit_tep_rezim:
                typ_treninku = "🧭 OB (Orienťák)"
            elif 'kopce' in nazev_lower or (nastoupano_v_rychlosti >= 45 and podil_rychlosti >= 0.12):
                typ_treninku = "⛰️ KOPCE"
            elif podil_rychlosti < 0.12:
                typ_treninku = "🟢 OBJEM"
            elif rychle_useky_pocet >= 4:
                typ_treninku = "⚡ INTERVALY (VO2max)"
            else:
                typ_treninku = "🔥 TEMPO (Prahový)"

        seznam_aktivit.append({
            "ID": aid,
            "Datum": datum,
            "Typ": kategorie,
            "Trénink": typ_treninku,
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
                df = nacti_data_z_garminu(
    email, heslo, st.session_state.dni_zpet,
    lim_pomaly_sec, lim_rychly_sec,
    st.session_state.tep_objem_max, st.session_state.tep_rychlost_min,
    st.session_state.zapnout_gap, tuple(st.session_state.rucni_teren_ids)
)
                
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
                        posledni_ef = behy_ef["Efektivita"].iloc[0]
                        rozdil_ef = round(posledni_ef - prum_ef, 2)

                        st.metric(
                            "Index efektivity (EF)",
                            f"{posledni_ef} (poslední běh)",
                            f"{rozdil_ef:+} oproti průměru ({prum_ef})"
                        )
                        with st.expander("ℹ️ Jak se počítá Index aerobní efektivity (EF)?"):
                         st.markdown("""
                         **Vzorec:** `EF = Průměrná rychlost (v metrech za minutu) ÷ Průměrný tep (bpm)`

                         * **Co to znamená:** Ukazuje, kolik metrů uběhneš na jeden jediný úder srdce.
                         * **Jak to číst:** Pokud běžíš stejným tempem, ale tvůj tep klesá (nebo při stejném tepu běžíš rychleji), **index EF roste** – tvoje aerobní kondice se zlepšuje.
                         * **Poznámka:** Nejlépe se porovnávají podobné rovinaté běhy; kopce nebo intervaly mohou hodnotu mírně zkreslit.
                         """)

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
        df = nacti_data_z_garminu(
            email, heslo, st.session_state.dni_zpet,
            lim_pomaly_sec, lim_rychly_sec,
            st.session_state.tep_objem_max, st.session_state.tep_rychlost_min,
            st.session_state.zapnout_gap, tuple(st.session_state.rucni_teren_ids)
        )
        st.dataframe(df.drop(columns=["ID"]), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("🌲 Přepnout běh na režim Terén / Orienťák / Sníh (podle tepu)")
        behy_jen = df[df["Typ"] == "🏃 Běh"]
        moznosti = {f"{r['Datum']} – {r['Název']}": r["ID"] for _, r in behy_jen.iterrows()}
        vybrane = st.multiselect(
            "Vyber běhy, které se mají hodnotit podle tepu místo tempa:",
            options=list(moznosti.keys()),
            default=[k for k, v in moznosti.items() if v in st.session_state.rucni_teren_ids]
        )
        st.session_state.rucni_teren_ids = [moznosti[k] for k in vybrane]
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
    st.divider()
    st.subheader("⛰️ Kopce a 🌲 Terén / Orientační běh")
    st.session_state.zapnout_gap = st.checkbox("Automaticky přepočítávat kopce na rovinaté tempo (GAP)", value=st.session_state.zapnout_gap)
    col_t1, col_t2 = st.columns(2)
    st.session_state.tep_objem_max = col_t1.number_input("Max. tep pro Objem v terénu (bpm)", 100, 180, st.session_state.tep_objem_max)
    st.session_state.tep_rychlost_min = col_t2.number_input("Min. tep pro Rychlost v terénu (bpm)", 130, 210, st.session_state.tep_rychlost_min)
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
