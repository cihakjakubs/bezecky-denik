import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_option_menu import option_menu
from datetime import datetime, timedelta
from garminconnect import Garmin
from supabase import create_client

@st.cache_resource
def init_supabase():
    url = st.secrets.get("SUPABASE_URL", "").strip()
    key = st.secrets.get("SUPABASE_KEY", "").strip()
    if "/rest/v1" in url:
        url = url.split("/rest/v1")
    url = url.rstrip("/")
    if url and key:
        return create_client(url, key)
    return None

supabase = init_supabase()

st.set_page_config(page_title="Běžecký Deník", page_icon="🏃", layout="wide")

if "db_nacteno" not in st.session_state:
    if supabase:
        try:
            res = supabase.table("nastaveni").select("*").eq("id", 1).execute()
            if res.data:
                for klic, hodnota in res.data[0].items():
                    if klic != "id":
                        st.session_state[klic] = hodnota
            res_poznamky = supabase.table("aktivity_poznamky").select("*").execute()
            st.session_state.rucni_teren_ids = [r["activity_id"] for r in res_poznamky.data if r.get("je_teren_ob")]
            st.session_state.vlastni_typy_db = {r["activity_id"]: r["vlastni_typ"] for r in res_poznamky.data if r.get("vlastni_typ")}
        except Exception as e:
            st.sidebar.error(f"Chyba DB: {e}")
    st.session_state.db_nacteno = True

# --- BOČNÍ MENU ---
with st.sidebar:
    st.title("🏃 Běžecký Deník")
    stranka = option_menu(
            menu_title=None,
            options=["Dashboard", "Plánovač", "Statistiky", "Deník aktivit", "Tréninkový deník", "Nastavení zón"],
            icons=["speedometer2", "calendar-plus", "bar-chart-line", "activity", "journal-check", "gear"],
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
    ulozene_dict = {}
    if supabase:
        try:
            res = supabase.table("aktivity_cache").select("*").gte("datum", od_data).lte("datum", do_data).execute()
            for r in res.data:
                ulozene_dict[r["activity_id"]] = {
                    "ID": r["activity_id"], "Datum": r["datum"], "Typ": r["typ"],
                    "Trénink": r["trenink"], "Název": r["nazev"], "Vzdálenost (km)": r["vzdalenost_km"],
                    "Prům. tep": r["prum_tep"], "Objem (min)": r["objem_min"], "Šedá (min)": r["seda_min"],
                    "Rychlost (min)": r["rychlost_min"], "Celkem (min)": r["celkem_min"], "Převýšení (m)": r["prevyseni_m"]
                }
        except Exception:
            pass


    aktivity = client.get_activities_by_date(od_data, do_data)
    seznam_aktivit = []

    for akt in aktivity:
        typ = akt.get('activityType', {}).get('typeKey', '')

        if 'running' not in typ and 'strength' not in typ and 'cardio' not in typ and 'fitness' not in typ:
            continue

        aid = akt['activityId']
        if aid in ulozene_dict:
            seznam_aktivit.append(ulozene_dict[aid])
            continue
        nazev = akt['activityName']
        datum = akt['startTimeLocal'][:10]
        vzdalenost = round(akt.get('distance', 0) / 1000, 2)
        tep = akt.get('averageHR', 0)
        celkovy_cas_min = round(akt.get('duration', 0) / 60, 1)
        prevyseni_celkem = int(round(akt.get('elevationGain', 0) or 0))
        event_type = akt.get('eventType', {}).get('typeKey', '')

        beh_objem, beh_seda, beh_rychlost = 0, 0, 0
        kategorie = "🏃 Běh" if 'running' in typ else "🏋️ Posilování"

        nazev_lower = nazev.lower()
        je_ob_auto = ('trail' in typ) or any(slovo in nazev_lower for slovo in ['trail', 'ob', 'orienťák', 'orientak', 'les'])
        je_zavod_nazev = ('race' in event_type) or any(slovo in nazev_lower for slovo in ['závod', 'zavod', 'race', 'mčr', 'mcr', 'pohár', 'pohar', 'liga'])
        pouzit_tep_rezim = (aid in rucni_teren_ids) or je_ob_auto

        rychle_useky_delky = []
        aktualni_rychly_usek = 0.0
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
                        if aktualni_rychly_usek >= 8.0:
                            rychle_useky_delky.append(aktualni_rychly_usek)
                        aktualni_rychly_usek = 0.0
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
                            if aktualni_rychly_usek >= 8.0:
                                rychle_useky_delky.append(aktualni_rychly_usek)
                            aktualni_rychly_usek = 0.0
                            continue

                        sklon = 0.0
                        if pouzit_gap and idx_vyska is not None and m_ted[idx_vyska] is not None:
                         historie_vysky.append((c_ted / 1000.0, m_ted[idx_vyska], r_ms * rozdil))
                         while len(historie_vysky) > 1 and (historie_vysky[-1][0] - historie_vysky[0][0]) > 10.0:
                             historie_vysky.pop(0)

                         if len(historie_vysky) >= 2:
                             prevyseni_10s = historie_vysky[-1][1] - historie_vysky[0][1]
                             vzdalenost_10s = sum(x[2] for x in historie_vysky[1:])
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

                    if je_ted_rychle:
                        aktualni_rychly_usek += rozdil
                        if idx_vyska is not None and m_ted[idx_vyska] is not None and m_pred[idx_vyska] is not None:
                            nastoupano_v_rychlosti += max(0.0, m_ted[idx_vyska] - m_pred[idx_vyska])
                    else:
                        if aktualni_rychly_usek >= 8.0:
                            rychle_useky_delky.append(aktualni_rychly_usek)
                        aktualni_rychly_usek = 0.0

                if aktualni_rychly_usek >= 8.0:
                    rychle_useky_delky.append(aktualni_rychly_usek)

        # --- AUTOMATICKÉ URČENÍ ŠTÍTKU TRÉNINKU ---
        typ_treninku = "🏋️ POSILOVÁNÍ"
        if kategorie == "🏃 Běh":
            celkem_sec = beh_objem + beh_seda + beh_rychlost
            podil_rychlosti = (beh_rychlost / celkem_sec) if celkem_sec > 0 else 0
            prumerna_delka_useku = (sum(rychle_useky_delky) / len(rychle_useky_delky)) if rychle_useky_delky else 0
            nejdelsi_usek = max(rychle_useky_delky) if rychle_useky_delky else 0
            kratke_rovinky = [d for d in rychle_useky_delky if 8.0 <= d <= 40.0]

            # Závodní slova (včetně orienťáckých disciplín) nebo 12+ minut v kuse bez přestávky
            zavodni_slova = ['závod', 'zavod', 'race', 'mčr', 'mcr', 'pohár', 'pohar', 'liga', 'štafet', 'stafet', 'klasik', 'krátk', 'kratk', 'sprint', 'middle', 'long']
            je_zavod_slovo = je_zavod_nazev or any(s in nazev_lower for s in zavodni_slova)
            je_zavod_vykon = (nejdelsi_usek >= 720.0) # 12+ minut v kuse v nejvyšší zóně (funguje i s rozklusem a výklusem!)

            # Kopce: musí být alespoň 3 opakované výběhy a výrazné stoupání v rychlosti (90+ m nebo > 20 m/min)
            rychlost_min_celkem = beh_rychlost / 60.0
            stoupani_za_min = (nastoupano_v_rychlosti / rychlost_min_celkem) if rychlost_min_celkem > 0 else 0
            jsou_to_kopce = ('kopce' in nazev_lower) or (len(rychle_useky_delky) >= 3 and nastoupano_v_rychlosti >= 90 and stoupani_za_min >= 20)

            if je_zavod_slovo or je_zavod_vykon:
                typ_treninku = "🏆 ZÁVOD (OB)" if pouzit_tep_rezim else "🏆 ZÁVOD"
            elif pouzit_tep_rezim:
                typ_treninku = "🧭 OB (Orienťák)"
            elif jsou_to_kopce:
                typ_treninku = "⛰️ KOPCE"
            elif podil_rychlosti < 0.12:
                if len(kratke_rovinky) >= 2:
                    typ_treninku = "🟢 OBJEM + STRIDES"
                else:
                    typ_treninku = "🟢 OBJEM"
            elif prumerna_delka_useku >= 180.0:
                typ_treninku = "🔥 TEMPO"
            elif len(rychle_useky_delky) >= 4:
                typ_treninku = "⚡ INTERVALY"
            else:
                typ_treninku = "🔥 TEMPO"

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
            "Celkem (min)": round((beh_objem + beh_seda + beh_rychlost) / 60, 1) if kategorie == "🏃 Běh" else celkovy_cas_min,
            "Převýšení (m)": prevyseni_celkem if kategorie == "🏃 Běh" else 0
        })
        if supabase:
            try:
                posl = seznam_aktivit[-1]
                supabase.table("aktivity_cache").upsert({
                    "activity_id": aid, "datum": posl["Datum"], "typ": posl["Typ"],
                    "trenink": posl["Trénink"], "nazev": posl["Název"], "vzdalenost_km": posl["Vzdálenost (km)"],
                    "prum_tep": posl["Prům. tep"], "objem_min": posl["Objem (min)"], "seda_min": posl["Šedá (min)"],
                    "rychlost_min": posl["Rychlost (min)"], "celkem_min": posl["Celkem (min)"], "prevyseni_m": posl["Převýšení (m)"]
                }).execute()
            except Exception:
                pass
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
# STRÁNKA 2: PLÁNOVAČ TRÉNINKŮ
# ==========================================
elif stranka == "Plánovač":
    st.title("🔮 Plánovač tréninků (Co kdyby...)")

    if not email or not heslo:
        st.warning("👈 Nejdříve se přihlas v levém menu.")
    else:
        df = nacti_data_z_garminu(
            email, heslo, st.session_state.dni_zpet,
            lim_pomaly_sec, lim_rychly_sec,
            st.session_state.tep_objem_max, st.session_state.tep_rychlost_min,
            st.session_state.zapnout_gap, tuple(st.session_state.rucni_teren_ids)
        )
        df_behy = df[df["Typ"] == "🏃 Běh"] if not df.empty else pd.DataFrame()

        objem = df_behy["Objem (min)"].sum() if not df_behy.empty else 0.0
        rychlost = df_behy["Rychlost (min)"].sum() if not df_behy.empty else 0.0

        st.info(f"📊 **Aktuální základ (za posledních {st.session_state.dni_zpet} dní):** {round(objem, 1)} min objemu a {round(rychlost, 1)} min rychlosti.")
        st.divider()

        st.subheader("1️⃣ Naklikej si plánované tréninky dopředu")
        pocet_planu = st.number_input("Kolik tréninků chceš nasimulovat?", 1, 7, 2)

        plan_objem_celkem = 0
        plan_rychlost_celkem = 0

        for i in range(int(pocet_planu)):
            with st.container():
                c_nazev, c_obj, c_rych = st.columns([1.5, 1, 1])
                c_nazev.text_input(f"Trénink #{i+1}", value=f"Plánovaný běh {i+1}", key=f"plan_nazev_{i}")
                p_obj = c_obj.number_input("Objem (min)", 0, 240, 45 if i == 0 else 25, step=5, key=f"plan_obj_{i}")
                p_rych = c_rych.number_input("Rychlost (min)", 0, 120, 0 if i == 0 else 20, step=5, key=f"plan_rych_{i}")
                plan_objem_celkem += p_obj
                plan_rychlost_celkem += p_rych

        st.divider()

        # --- VÝPOČET VÝSLEDKU PO PLÁNU ---
        novy_objem = objem + plan_objem_celkem
        nova_rychlost = rychlost + plan_rychlost_celkem
        nove_polarizovane = novy_objem + nova_rychlost

        st.subheader("2️⃣ Výsledek po splnění plánu")
        m1, m2, m3 = st.columns(3)
        m1.metric("🟢 Nový objem", f"{round(novy_objem, 1)} min", f"+{plan_objem_celkem} min")
        m2.metric("🔴 Nová rychlost", f"{round(nova_rychlost, 1)} min", f"+{plan_rychlost_celkem} min")

        if nove_polarizovane > 0:
            novy_rychlost_pct = round((nova_rychlost / nove_polarizovane) * 100, 1)
            novy_objem_pct = round(100 - novy_rychlost_pct, 1)
            m3.metric("⚖️ Nový poměr", f"{novy_objem_pct} : {novy_rychlost_pct}", f"Cíl {st.session_state.cil_objem}:{100 - st.session_state.cil_objem}")

            cil_rychlost_pct = 100 - st.session_state.cil_objem
            tol = st.session_state.tolerance_pct
            min_r = max(1, cil_rychlost_pct - tol)
            max_r = min(99, cil_rychlost_pct + tol)

            if min_r <= novy_rychlost_pct <= max_r:
                st.success(f"✅ **Perfektní plán!** Trefíš se přesně do cílového pásma ({min_r}–{max_r} % rychlosti).")
            elif novy_rychlost_pct < min_r:
                potrebna_r = novy_objem * (min_r / (100 - min_r))
                chybi_r = potrebna_r - nova_rychlost
                st.warning(f"🎯 **Po tomto plánu ti bude stále chybět {round(chybi_r, 1)} min rychlosti**, aby ses dostal na spodní hranici ({min_r} %).")
            else:
                potrebny_o = nova_rychlost * ((100 - max_r) / max_r)
                chybi_o = potrebny_o - novy_objem
                st.error(f"⚠️ **Pozor, moc rychlosti!** Překročíš horní hranici ({max_r} %). Přidej do plánu ještě cca **{round(chybi_o, 1)} min** volného objemu.")

# ==========================================
# STRÁNKA 3: STATISTIKY (MĚSÍČNÍ PŘEHLEDY)
# ==========================================
elif stranka == "Statistiky":
    st.title("📈 Dlouhodobé statistiky a složení tréninků")

    if not email or not heslo:
        st.warning("👈 Nejdříve se přihlas v levém menu.")
    else:
        pocet_mesicu = st.slider("Zobrazit historii za posledních (měsíců):", 2, 12, 6)
        dnes_dt = datetime.now()
        # Posuneme se o (pocet_mesicu - 1) měsíců zpět a nastavíme 1. den v měsíci
        cilovy_mesic = dnes_dt.month - (pocet_mesicu - 1)
        cilovy_rok = dnes_dt.year
        while cilovy_mesic <= 0:
            cilovy_mesic += 12
            cilovy_rok -= 1
        prvni_den = datetime(cilovy_rok, cilovy_mesic, 1)
        dni_historie = (dnes_dt - prvni_den).days

        with st.spinner(f"Načítám historii za posledních {pocet_mesicu} měsíců z Garminu..."):
            df_hist = nacti_data_z_garminu(
                email, heslo, dni_historie,
                lim_pomaly_sec, lim_rychly_sec,
                st.session_state.tep_objem_max, st.session_state.tep_rychlost_min,
                st.session_state.zapnout_gap, tuple(st.session_state.rucni_teren_ids)
            )

        if df_hist.empty:
            st.info("Za zvolené období nebyly nalezeny žádné aktivity.")
        else:
            if df_hist.empty:
            st.info("Za zvolené období nebyly nalezeny žádné aktivity")
        else:
            df_hist["Trénink"] = df_hist.apply(lambda r: st.session_state.get("vlastni_typy_db", {}).get(r["ID"], r["Trénink"]), axis=1)
            df_hist["Měsíc"] = df_hist["Datum"].str.slice(0, 7) # Např. "2026-11"
            df_behy_h = df_hist[df_hist["Typ"] == "🏃 Běh"]

            # --- 1. SOUHRNNÉ REKORDY ZA OBDOBÍ ---
            st.subheader(f"🏆 Souhrn za posledních {pocet_mesicu} měsíců")
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("🏃 Naběháno celkem", f"{round(df_behy_h['Vzdálenost (km)'].sum(), 1)} km", f"{len(df_behy_h)} běhů")
            s2.metric("⏱️ Celkový čas (vše)", f"{round(df_hist['Celkem (min)'].sum() / 60, 1)} hod", f"{len(df_hist)} aktivit")
            s3.metric("⛰️ Nastoupáno celkem", f"{int(df_behy_h['Převýšení (m)'].sum())} m", f"Max {int(df_behy_h['Převýšení (m)'].max()) if not df_behy_h.empty else 0} m / běh")
            s4.metric("📏 Nejdelší běh", f"{df_behy_h['Vzdálenost (km)'].max() if not df_behy_h.empty else 0} km", f"Průměr {round(df_behy_h['Vzdálenost (km)'].mean(), 1) if not df_behy_h.empty else 0} km")

            st.divider()

            # --- 2. MĚSÍČNÍ SLOŽENÍ TRÉNINKŮ (SLOUPCOVÝ GRAF) ---
            st.subheader("📊 Měsíční složení tréninků (minuty podle typu)")
            mesicni_typy = df_hist.groupby(["Měsíc", "Trénink"])["Celkem (min)"].sum().reset_index()

            fig_mesice = px.bar(
                mesicni_typy,
                x="Měsíc",
                y="Celkem (min)",
                color="Trénink",
                color_discrete_map={
                    "🟢 OBJEM": "#2ecc71",
                    "🟢 OBJEM +STRIDES": "#27ae60",
                    "🔥 TEMPO (Prahový)": "#e67e22",
                    "⚡ INTERVALY (VO2max)": "#e74c3c",
                    "⛰️ KOPCE": "#9b59b6",
                    "🧭 OB (Orienťák)": "#16a085",
                    "🏆 ZÁVOD": "#f1c40f",
                    "🏆 ZÁVOD (OB)": "#f39c12",
                    "🏋️ POSILOVÁNÍ": "#3498db"
                },
                labels={"Celkem (min)": "Čas (minuty)", "Měsíc": "Měsíc"}
            )
            fig_mesice.update_layout(barmode="stack", xaxis_type="category")
            st.plotly_chart(fig_mesice, use_container_width=True)

            st.divider()

            # --- 3. MĚSÍČNÍ KILOMETRY A PŘEVÝŠENÍ ---
            col_st1, col_st2 = st.columns(2)
            with col_st1:
                st.subheader("📏 Kilometry po měsících")
                mesicni_km = df_behy_h.groupby("Měsíc")["Vzdálenost (km)"].sum().reset_index()
                fig_km = px.bar(mesicni_km, x="Měsíc", y="Vzdálenost (km)", text_auto=".1f")
                fig_km.update_traces(marker_color="#2ecc71")
                fig_km.update_layout(xaxis_type="category")
                st.plotly_chart(fig_km, use_container_width=True)

            with col_st2:
                st.subheader("⛰️ Nastoupané metry po měsících")
                mesicni_vyska = df_behy_h.groupby("Měsíc")["Převýšení (m)"].sum().reset_index()
                fig_vyska = px.bar(mesicni_vyska, x="Měsíc", y="Převýšení (m)", text_auto=True)
                fig_vyska.update_traces(marker_color="#9b59b6")
                fig_vyska.update_layout(xaxis_type="category")
                st.plotly_chart(fig_vyska, use_container_width=True)

# ==========================================
# STRÁNKA 4: DENÍK AKTIVIT
# ==========================================
elif stranka == "Deník aktivit":
    st.title("🏃 Deník aktivit (Klikni na 'Trénink' pro ruční úpravu)")
    if email and heslo:
        # Nabídneme i přepínač období, ať můžeš upravovat i starší OB ze Statistik
        obdobi_denik = st.radio("Zobrazit období:", [st.session_state.dni_zpet, 90, 180], horizontal=True, format_func=lambda x: f"Posledních {x} dní")

        df = nacti_data_z_garminu(
            email, heslo, obdobi_denik,
            lim_pomaly_sec, lim_rychly_sec,
            st.session_state.tep_objem_max, st.session_state.tep_rychlost_min,
            st.session_state.zapnout_gap, tuple(st.session_state.rucni_teren_ids)
        )

        # Pokud už má aktivita uložený tvůj ruční typ, zobrazíme ho
        vlastni_mapa = st.session_state.get("vlastni_typy_db", {})
        df["Trénink"] = df.apply(lambda r: vlastni_mapa.get(r["ID"], r["Trénink"]), axis=1)

        mozne_typy = [
            "🟢 OBJEM", "🟢 OBJEM +STRIDES", "🔥 TEMPO (Prahový)",
            "⚡ INTERVALY (VO2max)", "⛰️ KOPCE", "🧭 OB (Orienťák)",
            "🏆 ZÁVOD", "🏆 ZÁVOD (OB)", "🏋️ POSILOVÁNÍ"
        ]

        # Interaktivní tabulka – upravovat jde jen sloupec 'Trénink'
        upraveny_df = st.data_editor(
            df.drop(columns=["ID"]),
            column_config={
                "Trénink": st.column_config.SelectboxColumn(
                    "Trénink (klikni pro změnu)",
                    options=mozne_typy,
                    required=True
                )
            },
            disabled=[c for c in df.columns if c not in ["ID", "Trénink"]],
            use_container_width=True,
            hide_index=True
        )

        if st.button("💾 Uložit ruční úpravy tréninků", type="primary"):
            if supabase:
                for idx, radek in upraveny_df.iterrows():
                    aid = int(df.loc[idx, "ID"])
                    novy_typ = radek["Trénink"]
                    puvodni_typ = df.loc[idx, "Trénink"]

                    if novy_typ != puvodni_typ:
                        je_ob = "OB" in novy_typ
                        supabase.table("aktivity_poznamky").upsert({
                            "activity_id": aid,
                            "je_teren_ob": je_ob,
                            "vlastni_typ": novy_typ
                        }).execute()
                        # Aktualizujeme rovnou i v cache, ať se to projeví ve Statistikách
                        supabase.table("aktivity_cache").update({"trenink": novy_typ}).eq("activity_id", aid).execute()
                        st.session_state.vlastni_typy_db[aid] = novy_typ
                        if je_ob and aid not in st.session_state.rucni_teren_ids:
                            st.session_state.rucni_teren_ids.append(aid)

                st.cache_data.clear()
                st.success("✅ Úpravy uloženy! Grafy ve Statistikách i na Dashboardu jsou aktualizované.")
                st.rerun()
    else:
        st.warning("👈 Nejdříve se přihlas v levém menu.")

# ==========================================
# STRÁNKA 5: NASTAVENÍ ZÓN
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

    st.divider()
    if st.button("💾 Uložit nastavení do databáze", type="primary"):
        if supabase:
            supabase.table("nastaveni").upsert({
                "id": 1,
                "pomaly_min": int(st.session_state.pomaly_min),
                "pomaly_sec": int(st.session_state.pomaly_sec),
                "rychly_min": int(st.session_state.rychly_min),
                "rychly_sec": int(st.session_state.rychly_sec),
                "cil_objem": int(st.session_state.cil_objem),
                "tolerance_pct": int(st.session_state.tolerance_pct),
                "dni_zpet": int(st.session_state.dni_zpet),
                "cil_posilovani_tyden": int(st.session_state.cil_posilovani_tyden),
                "limit_seda_pct": int(st.session_state.limit_seda_pct),
                "tep_objem_max": int(st.session_state.tep_objem_max),
                "tep_rychlost_min": int(st.session_state.tep_rychlost_min),
                "zapnout_gap": bool(st.session_state.zapnout_gap)
            }).execute()
            st.success("✅ Uloženo!")

# ==========================================
# STRÁNKA 6: TRÉNINKOVÝ DENÍK
# ==========================================
elif stranka == "Budoucí deník":
    st.title("📝 Tréninkový deník (Připraveno k rozšíření)")
    st.info("Databáze Supabase je připojená a připravená pro přidání hodnocení pocitů (RPE), poznámek k tréninkům a sledování naběhaných kilometrů v botách.")
